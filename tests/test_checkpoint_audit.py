"""Checkpoint decisions: auditing, validation, edit verification."""

import unittest
from unittest.mock import MagicMock

from apps.orchestrator import (
    TaskGraph, TaskNode, TaskStatus, NodeStatus, NodeType, Orchestrator, CriticResult,
)


class MemPersistence:
    def __init__(self):
        self.tasks = {}

    async def save(self, t):
        self.tasks[t.task_id] = t

    async def load(self, i):
        return self.tasks.get(i)

    async def create(self, t):
        self.tasks[t.task_id] = t


class StubCritic:
    def __init__(self, passed=True):
        self.passed = passed

    async def verify(self, *a):
        return CriticResult(passed=self.passed, notes="n", required_fixes=[])


class CheckpointAuditTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.persistence = MemPersistence()
        self.orch = Orchestrator(MagicMock(), StubCritic(True), self.persistence)
        self.ledger = []

        task = TaskGraph(task_id="T-1", goal="g", purpose="p", user_id="u-1")
        task.add_node(TaskNode(id="n1", type=NodeType.DRAFT_NOTE, requires_human_review=True))
        task.add_node(TaskNode(id="n2", type=NodeType.EXPORT_DOCX, depends_on=["n1"]))

        async def policy(u, n):
            return True

        async def router(n):
            return "m"

        async def run(n, m, fb=None):
            return {"text": "x"}

        async def ledger(t, p):
            pass

        task = await self.orch.execute_task(task, policy, router, run, ledger)
        self.assertEqual(task.status, TaskStatus.PAUSED)
        self.cp_id = task.pending_checkpoints[0].id

    async def emit(self, event_type, payload):
        self.ledger.append((event_type, payload))

    async def test_approve_is_ledgered_with_actor(self):
        await self.orch.handle_checkpoint_decision(
            "T-1", self.cp_id, "approve", decided_by="alice", ledger_emit=self.emit)
        self.assertEqual(len(self.ledger), 1)
        event, payload = self.ledger[0]
        self.assertEqual(event, "checkpoint.decided")
        self.assertEqual(payload["decision"], "approve")
        self.assertEqual(payload["decided_by"], "alice")

    async def test_reject_is_ledgered(self):
        task = await self.orch.handle_checkpoint_decision(
            "T-1", self.cp_id, "reject", decided_by="bob", ledger_emit=self.emit)
        self.assertEqual(task.nodes["n1"].status, NodeStatus.FAILED)
        self.assertEqual(self.ledger[0][1]["decision"], "reject")

    async def test_edit_is_verified_and_ledger_has_hash_not_content(self):
        task = await self.orch.handle_checkpoint_decision(
            "T-1", self.cp_id, "edit", edit_data={"text": "SECRET-FIXED"},
            decided_by="alice", ledger_emit=self.emit)
        self.assertEqual(task.nodes["n1"].output_data, {"text": "SECRET-FIXED"})
        self.assertTrue(task.nodes["n1"].verification["edited_by_human"])
        payload = self.ledger[0][1]
        self.assertEqual(len(payload["edit_sha256"]), 64)
        self.assertNotIn("SECRET-FIXED", str(payload))

    async def test_failed_edit_is_rejected_and_checkpoint_stays_pending(self):
        self.orch.critic = StubCritic(passed=False)
        with self.assertRaises(ValueError):
            await self.orch.handle_checkpoint_decision(
                "T-1", self.cp_id, "edit", edit_data={"text": "bad"},
                ledger_emit=self.emit)
        task = await self.persistence.load("T-1")
        self.assertEqual(len(task.pending_checkpoints), 1)
        self.assertEqual(task.nodes["n1"].status, NodeStatus.NEEDS_REVIEW)
        self.assertEqual(self.ledger, [])

    async def test_invalid_decision_changes_nothing(self):
        with self.assertRaises(ValueError):
            await self.orch.handle_checkpoint_decision(
                "T-1", self.cp_id, "aprove", ledger_emit=self.emit)
        task = await self.persistence.load("T-1")
        self.assertEqual(len(task.pending_checkpoints), 1)
        self.assertEqual(self.ledger, [])

    async def test_edit_without_data_is_rejected(self):
        with self.assertRaises(ValueError):
            await self.orch.handle_checkpoint_decision("T-1", self.cp_id, "edit")

    async def test_unknown_checkpoint_raises(self):
        with self.assertRaises(ValueError):
            await self.orch.handle_checkpoint_decision("T-1", "c-nope", "approve")

    async def test_ledger_outage_prevents_decision_from_persisting(self):
        async def broken(t, p):
            raise ConnectionError("ledger down")

        with self.assertRaises(ConnectionError):
            await self.orch.handle_checkpoint_decision(
                "T-1", self.cp_id, "approve", ledger_emit=broken)


if __name__ == "__main__":
    unittest.main()
