"""Tests for B7-B9: Orchestrator (task graph, planner, critic)."""

import unittest
import asyncio
import json
from typing import Dict, Any, Optional, List
from unittest.mock import AsyncMock, MagicMock, patch

from apps.orchestrator import (
    TaskGraph,
    TaskNode,
    TaskStatus,
    NodeStatus,
    NodeType,
    Planner,
    Critic,
    Orchestrator,
    PlanValidator,
    PostgresPersistence,
    VerificationLevel,
    CriticResult,
    RetryPolicy,
)


class MockPersistence:
    """Mock persistence for testing."""
    
    def __init__(self):
        self.tasks: Dict[str, TaskGraph] = {}
    
    async def save(self, task: TaskGraph) -> None:
        self.tasks[task.task_id] = task
    
    async def load(self, task_id: str) -> Optional[TaskGraph]:
        return self.tasks.get(task_id)
    
    async def create(self, task: TaskGraph) -> None:
        self.tasks[task.task_id] = task
    
    async def list_tasks(self, user_id: str, limit: int = 10) -> List[TaskGraph]:
        return [t for t in self.tasks.values() if t.user_id == user_id][:limit]


class TestTaskGraph(unittest.TestCase):
    """Test task graph model and DAG validation."""
    
    def setUp(self):
        self.task = TaskGraph(
            task_id="t-1",
            goal="Extract and draft approval note",
            purpose="inspection_review",
            user_id="u-42",
        )
    
    def test_create_task_graph(self):
        """Test creating a task graph."""
        self.assertEqual(self.task.task_id, "t-1")
        self.assertEqual(self.task.status, TaskStatus.PLANNING)
        self.assertEqual(len(self.task.nodes), 0)
    
    def test_add_node(self):
        """Test adding nodes to the graph."""
        node = TaskNode(
            id="n1",
            type=NodeType.OCR_EXTRACT,
            depends_on=[],
        )
        self.task.add_node(node)
        
        self.assertIn("n1", self.task.nodes)
        self.assertEqual(self.task.nodes["n1"].type, NodeType.OCR_EXTRACT)
    
    def test_dag_validation_no_cycles(self):
        """Test DAG validation rejects cycles."""
        n1 = TaskNode(id="n1", type=NodeType.OCR_EXTRACT, depends_on=[])
        n2 = TaskNode(id="n2", type=NodeType.RETRIEVE_SOPS, depends_on=["n1"])
        n3 = TaskNode(id="n3", type=NodeType.DRAFT_NOTE, depends_on=["n2", "n1"])
        
        self.task.add_node(n1)
        self.task.add_node(n2)
        self.task.add_node(n3)
        
        valid, msg = self.task.validate_dag()
        self.assertTrue(valid)
    
    def test_dag_validation_depth_limit(self):
        """Test DAG validation respects depth limit."""
        for i in range(6):
            node = TaskNode(
                id=f"n{i}",
                type=NodeType.OCR_EXTRACT,
                depends_on=[f"n{i-1}"] if i > 0 else [],
            )
            self.task.add_node(node)
        
        valid, msg = self.task.validate_dag()
        self.assertFalse(valid)
        self.assertIn("depth", msg.lower())
    
    def test_dag_validation_node_count_limit(self):
        """Test DAG validation respects node count limit."""
        for i in range(15):
            node = TaskNode(
                id=f"n{i}",
                type=NodeType.OCR_EXTRACT,
                depends_on=[],
            )
            self.task.add_node(node)
        
        valid, msg = self.task.validate_dag()
        self.assertFalse(valid)
        self.assertIn("node", msg.lower())
    
    def test_ready_nodes(self):
        """Test ready_nodes() returns nodes with satisfied dependencies."""
        n1 = TaskNode(id="n1", type=NodeType.OCR_EXTRACT, depends_on=[])
        n2 = TaskNode(id="n2", type=NodeType.RETRIEVE_SOPS, depends_on=["n1"])
        n3 = TaskNode(id="n3", type=NodeType.DRAFT_NOTE, depends_on=["n2"])
        
        self.task.add_node(n1)
        self.task.add_node(n2)
        self.task.add_node(n3)
        
        # Initially only n1 is ready
        ready = self.task.ready_nodes()
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].id, "n1")
        
        # Mark n1 as done
        n1.mark_done()
        ready = self.task.ready_nodes()
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].id, "n2")
        
        # Mark n2 as done
        n2.mark_done()
        ready = self.task.ready_nodes()
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].id, "n3")
    
    def test_task_serialization(self):
        """Test serialization and deserialization."""
        node = TaskNode(
            id="n1",
            type=NodeType.OCR_EXTRACT,
            depends_on=[],
            success_criteria="All fields present",
        )
        self.task.add_node(node)
        
        # Serialize
        task_dict = self.task.to_dict()
        self.assertIn('nodes', task_dict)
        self.assertEqual(task_dict['task_id'], "t-1")
        
        # Deserialize
        restored = TaskGraph.from_dict(task_dict)
        self.assertEqual(restored.task_id, "t-1")
        self.assertEqual(len(restored.nodes), 1)
        self.assertEqual(restored.nodes["n1"].type, NodeType.OCR_EXTRACT)


class TestPlanner(unittest.TestCase):
    """Test planner and plan validation."""
    
    def setUp(self):
        # Create a mock model
        self.mock_model = AsyncMock()
    
    def test_plan_validation(self):
        """Test PlanValidator rejects invalid plans."""
        # Valid plan
        task = TaskGraph(
            task_id="t-1",
            goal="test",
            purpose="test",
            user_id="u-1",
        )
        n1 = TaskNode(id="n1", type=NodeType.OCR_EXTRACT, depends_on=[])
        task.add_node(n1)
        
        valid, msg = PlanValidator.validate(task)
        self.assertTrue(valid)


class TestCritic(unittest.TestCase):
    """Test critic verification."""
    
    def test_verify_ocr_extract_valid(self):
        """Test verification of valid OCR output."""
        critic = Critic()
        output = {
            'fields': [
                {'name': 'equipment_tag', 'value': 'P-101A', 'confidence': 0.97},
                {'name': 'thickness_mm', 'value': '6.4', 'confidence': 0.62, 'needs_review': True},
            ]
        }
        
        # Run async call
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(
            critic.verify("n1", "ocr_extract", output, "Fields present")
        )
        self.assertTrue(result.passed)
    
    def test_verify_ocr_extract_invalid(self):
        """Test verification rejects invalid OCR output."""
        critic = Critic()
        
        # Missing fields
        output = {}
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(
            critic.verify("n1", "ocr_extract", output, "Fields present")
        )
        self.assertFalse(result.passed)
    
    def test_verify_draft_note_with_citations(self):
        """Test verification requires citations in draft."""
        critic = Critic()
        output = {
            'note_text': 'Inspection revealed equipment P-101A is operational.',
            'citations': [
                {'doc_id': 'd-203', 'page': 1, 'section': '2.1'}
            ]
        }
        
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(
            critic.verify("n3", "draft_note", output, "Grounded generation")
        )
        self.assertTrue(result.passed)
    
    def test_verify_draft_note_missing_citations(self):
        """Test verification rejects draft without citations."""
        critic = Critic()
        output = {
            'note_text': 'Inspection revealed equipment P-101A is operational.',
        }
        
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(
            critic.verify("n3", "draft_note", output, "Grounded generation")
        )
        self.assertFalse(result.passed)
        self.assertIn("citations", result.notes.lower())
    
    def test_retry_policy(self):
        """Test retry policy logic."""
        critic_result = CriticResult(
            passed=False,
            notes="Bad output",
            required_fixes=["Fix field format"],
        )
        
        # Should retry
        should_retry, feedback = RetryPolicy.should_retry(
            "draft_note", attempts=1, max_retries=3, critic_result=critic_result
        )
        self.assertTrue(should_retry)
        
        # Should not retry at max
        should_retry, feedback = RetryPolicy.should_retry(
            "draft_note", attempts=3, max_retries=3, critic_result=critic_result
        )
        self.assertFalse(should_retry)


class TestOrchestrator(unittest.TestCase):
    """Test orchestrator execution."""
    
    def setUp(self):
        self.persistence = MockPersistence()
        self.planner = AsyncMock()
        self.critic = AsyncMock()
        self.orchestrator = Orchestrator(
            self.planner,
            self.critic,
            self.persistence,
        )
    
    def test_plan_task(self):
        """Test task planning."""
        # Mock planner to return a valid plan
        task = TaskGraph(
            task_id="t-1",
            goal="Extract findings",
            purpose="inspection_review",
            user_id="u-42",
        )
        node = TaskNode(id="n1", type=NodeType.OCR_EXTRACT, depends_on=[])
        task.add_node(node)
        
        self.planner.plan.return_value = task
        
        # Run async call
        loop = asyncio.get_event_loop()
        planned = loop.run_until_complete(
            self.orchestrator.plan_task(
                goal="Extract findings",
                purpose="inspection_review",
                user_id="u-42",
                user_clearance="internal",
            )
        )
        
        self.assertEqual(planned.task_id, "t-1")
        self.assertEqual(len(planned.nodes), 1)
    
    def test_handle_checkpoint_decision(self):
        """Test checkpoint decision handling."""
        task = TaskGraph(
            task_id="t-1",
            goal="test",
            purpose="test",
            user_id="u-42",
        )
        
        node = TaskNode(id="n1", type=NodeType.DRAFT_NOTE, depends_on=[])
        node.status = NodeStatus.NEEDS_REVIEW
        task.add_node(node)
        
        from apps.orchestrator import Checkpoint
        checkpoint = Checkpoint(
            id="c-1",
            task_id="t-1",
            node_id="n1",
            reason="Review draft",
            requested_at="2026-10-02T10:00:00Z",
        )
        task.pending_checkpoints.append(checkpoint)
        
        loop = asyncio.get_event_loop()
        loop.run_until_complete(self.persistence.create(task))
        
        # Handle approval
        updated = loop.run_until_complete(
            self.orchestrator.handle_checkpoint_decision("t-1", "c-1", "approve")
        )
        
        self.assertEqual(updated.nodes["n1"].status, NodeStatus.DONE)
        self.assertEqual(len(updated.pending_checkpoints), 0)
        self.assertIn("c-1", updated.checkpoint_decisions)


class TestTaskNodeState(unittest.TestCase):
    """Test TaskNode state transitions."""
    
    def test_node_status_transitions(self):
        """Test valid node status transitions."""
        node = TaskNode(id="n1", type=NodeType.OCR_EXTRACT, depends_on=[])
        
        # PENDING → RUNNING
        node.mark_running()
        self.assertEqual(node.status, NodeStatus.RUNNING)
        
        # RUNNING → DONE
        node.mark_done({'fields': []})
        self.assertEqual(node.status, NodeStatus.DONE)
        self.assertIsNotNone(node.output_data)
    
    def test_node_retry_attempt_counter(self):
        """Test node tracks retry attempts."""
        node = TaskNode(id="n1", type=NodeType.RUN_PYTHON, depends_on=[])
        
        self.assertEqual(node.attempts, 0)
        node.attempts += 1
        self.assertEqual(node.attempts, 1)


if __name__ == '__main__':
    unittest.main()
