"""Executor loop tests: happy path, retries, policy denial, checkpoints, persistence."""

import unittest
from unittest.mock import MagicMock

from apps.orchestrator import (
    TaskGraph, TaskNode, TaskStatus, NodeStatus, NodeType,
    Orchestrator, CriticResult,
)
from apps.orchestrator.task_graph import Checkpoint


class MemPersistence:
    def __init__(self):
        self.tasks = {}

    async def save(self, task):
        self.tasks[task.task_id] = task

    async def load(self, task_id):
        return self.tasks.get(task_id)

    async def create(self, task):
        self.tasks[task.task_id] = task

    async def list_tasks(self, user_id, limit=10):
        return list(self.tasks.values())[:limit]


class StubCritic:
    """Returns scripted results in order; the last one repeats."""
    def __init__(self, results):
        self.results = results
        self.calls = 0

    async def verify(self, node_id, node_type, output, success_criteria):
        r = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        return r


PASS = CriticResult(passed=True, notes="ok", required_fixes=[])
FAIL = CriticResult(passed=False, notes="bad", required_fixes=["fix it"],
                    retry_prompt_delta="please fix")


def make_task(n_nodes=2, review_on=None, max_retries=3):
    task = TaskGraph(task_id="T-1", goal="g", purpose="inspection_review", user_id="u-1")
    task.limits.max_retries = max_retries
    prev = None
    for i in range(1, n_nodes + 1):
        nid = f"n{i}"
        task.add_node(TaskNode(
            id=nid, type=NodeType.DRAFT_NOTE,
            depends_on=[prev] if prev else [],
            requires_human_review=(nid == review_on),
        ))
        prev = nid
    return task


class ExecutorLoopTests(unittest.IsolatedAsyncioTestCase):

    def build(self, critic):
        self.persistence = MemPersistence()
        self.ledger, self.events = [], []

        async def sink(event_type, payload):
            self.events.append((event_type, payload))

        self.orch = Orchestrator(MagicMock(), critic, self.persistence, event_sink=sink)

    async def run_task(self, task, executor_fn=None, allow=True):
        self.feedbacks = []

        async def policy(user_id, node):
            return allow

        async def router(node):
            return "qwen2.5:7b"

        async def default_exec(node, model_id, feedback=None):
            self.feedbacks.append(feedback)
            return {"text": f"out-{node.id}"}

        async def ledger(event_type, payload):
            self.ledger.append((event_type, payload))

        return await self.orch.execute_task(
            task, policy, router, executor_fn or default_exec, ledger)

    # --- happy path -------------------------------------------------------
    async def test_happy_path_completes(self):
        self.build(StubCritic([PASS]))
        task = await self.run_task(make_task(2))
        self.assertEqual(task.status, TaskStatus.COMPLETED)
        self.assertTrue(all(n.status == NodeStatus.DONE for n in task.nodes.values()))
        self.assertEqual(task.total_attempts, 2)
        self.assertEqual(task.nodes["n1"].model_used, "qwen2.5:7b")
        self.assertEqual([e[0] for e in self.ledger], ["node.executed"] * 2)
        self.assertIn("task.completed", [e[0] for e in self.events])
        self.assertEqual(self.persistence.tasks["T-1"].status, TaskStatus.COMPLETED)

    # --- retries ----------------------------------------------------------
    async def test_critic_failure_retries_with_feedback_then_succeeds(self):
        self.build(StubCritic([FAIL, PASS]))
        task = await self.run_task(make_task(1))
        self.assertEqual(task.nodes["n1"].status, NodeStatus.DONE)
        self.assertEqual(task.nodes["n1"].attempts, 2)
        self.assertEqual(self.feedbacks, [None, "please fix"])

    async def test_retry_exhaustion_fails_node_and_task(self):
        self.build(StubCritic([FAIL]))
        task = await self.run_task(make_task(2, max_retries=3))
        self.assertEqual(task.nodes["n1"].status, NodeStatus.FAILED)
        self.assertEqual(task.nodes["n1"].attempts, 3)
        self.assertEqual(task.nodes["n2"].status, NodeStatus.PENDING)
        self.assertEqual(task.status, TaskStatus.FAILED)

    async def test_executor_exception_is_retried(self):
        self.build(StubCritic([PASS]))
        seen = []

        async def flaky(node, model_id, feedback=None):
            seen.append(feedback)
            if len(seen) == 1:
                raise RuntimeError("transient")
            return {"text": "ok"}

        task = await self.run_task(make_task(1), executor_fn=flaky)
        self.assertEqual(task.nodes["n1"].status, NodeStatus.DONE)
        self.assertEqual(task.nodes["n1"].attempts, 2)
        self.assertEqual(seen, [None, "transient"])

    # --- policy -----------------------------------------------------------
    async def test_policy_denial_fails_without_executing(self):
        self.build(StubCritic([PASS]))
        task = await self.run_task(make_task(1), allow=False)
        self.assertEqual(task.nodes["n1"].status, NodeStatus.FAILED)
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(self.feedbacks, [])  # executor never called

    async def test_policy_denial_is_recorded_in_ledger(self):
        self.build(StubCritic([PASS]))
        await self.run_task(make_task(1), allow=False)
        self.assertTrue(
            any(e[0] in ("policy.denied", "node.failed") for e in self.ledger),
            "denied access left no ledger record")

    # --- checkpoints ------------------------------------------------------
    async def test_review_node_pauses_task_and_requests_checkpoint(self):
        self.build(StubCritic([PASS]))
        task = await self.run_task(make_task(2, review_on="n1"))
        self.assertEqual(task.status, TaskStatus.PAUSED)
        self.assertEqual(task.nodes["n1"].status, NodeStatus.NEEDS_REVIEW)
        self.assertEqual(task.nodes["n2"].status, NodeStatus.PENDING)
        self.assertEqual(len(task.pending_checkpoints), 1)
        self.assertIn("checkpoint.required", [e[0] for e in self.events])

    async def test_approve_then_resume_completes(self):
        self.build(StubCritic([PASS]))
        task = await self.run_task(make_task(2, review_on="n1"))
        cp_id = task.pending_checkpoints[0].id
        task = await self.orch.handle_checkpoint_decision("T-1", cp_id, "approve")
        self.assertEqual(task.nodes["n1"].status, NodeStatus.DONE)
        task = await self.run_task(task)
        self.assertEqual(task.status, TaskStatus.COMPLETED)

    async def test_reject_fails_task_on_resume(self):
        self.build(StubCritic([PASS]))
        task = await self.run_task(make_task(2, review_on="n1"))
        cp_id = task.pending_checkpoints[0].id
        task = await self.orch.handle_checkpoint_decision("T-1", cp_id, "reject")
        self.assertEqual(task.nodes["n1"].status, NodeStatus.FAILED)
        task = await self.run_task(task)
        self.assertEqual(task.status, TaskStatus.FAILED)


class PersistenceRoundTripTests(unittest.TestCase):
    def test_pending_checkpoints_survive_serialization(self):
        task = make_task(2, review_on="n1")
        task.pending_checkpoints.append(Checkpoint(
            id="c-T-1-n1", task_id="T-1", node_id="n1",
            reason="Review required", requested_at="2026-01-01T00:00:00"))
        restored = TaskGraph.from_dict(task.to_dict())
        self.assertEqual([c.id for c in restored.pending_checkpoints], ["c-T-1-n1"])


if __name__ == "__main__":
    unittest.main()
