"""B7: Orchestrator executor — main task execution loop."""

import asyncio
import json
from typing import Dict, List, Any, Optional, Callable, Awaitable
from dataclasses import dataclass
from datetime import datetime
import logging

from .task_graph import TaskGraph, TaskNode, TaskStatus, NodeStatus, Checkpoint
from .planner import Planner, PlanValidator
from .critic import Critic, RetryPolicy, VerificationLevel


logger = logging.getLogger(__name__)


@dataclass
class ExecutionContext:
    """Context for a node execution."""
    task: TaskGraph
    node: TaskNode
    policy_check: Callable[[str, TaskNode], Awaitable[bool]]  # (user_id, node) -> allowed
    router_select: Callable[[TaskNode], Awaitable[str]]  # (node) -> model_id
    executor_fn: Callable[[TaskNode, str], Awaitable[Dict[str, Any]]]  # (node, model_id) -> output
    ledger_emit: Callable[[str, Dict[str, Any]], Awaitable[None]]  # (event_type, payload)


class Orchestrator:
    """B7: Orchestrates task execution with checkpoints, retries and verification."""
    
    def __init__(
        self,
        planner: Planner,
        critic: Critic,
        persistence,  # PersistenceBackend
        event_sink: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None,
    ):
        self.planner = planner
        self.critic = critic
        self.persistence = persistence
        self.event_sink = event_sink
    
    async def plan_task(self, goal: str, purpose: str, user_id: str, user_clearance: str) -> TaskGraph:
        """Generate a task plan from a goal."""
        from .planner import PlanRequest
        
        request = PlanRequest(
            goal=goal,
            purpose=purpose,
            user_id=user_id,
            user_clearance=user_clearance,
        )
        
        task = await self.planner.plan(request)
        
        # Validate plan
        valid, msg = PlanValidator.validate(task)
        if not valid:
            logger.error(f"Invalid plan: {msg}")
            raise ValueError(f"Planner generated invalid plan: {msg}")
        
        # Save to persistence
        await self.persistence.create(task)
        
        # Emit event
        if self.event_sink:
            await self.event_sink('task.created', {
                'task_id': task.task_id,
                'goal': task.goal,
                'nodes': len(task.nodes),
            })
        
        return task
    
    async def execute_task(
        self,
        task: TaskGraph,
        policy_check: Callable[[str, TaskNode], Awaitable[bool]],
        router_select: Callable[[TaskNode], Awaitable[str]],
        executor_fn: Callable[[TaskNode, str], Awaitable[Dict[str, Any]]],
        ledger_emit: Callable[[str, Dict[str, Any]], Awaitable[None]],
    ) -> TaskGraph:
        """Main execution loop for a task."""
        
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.utcnow().isoformat()
        
        try:
            # Execution loop
            while task.status == TaskStatus.RUNNING:
                # Get nodes ready to run
                ready = task.ready_nodes()
                
                if not ready:
                    # Check if task is done
                    failed = task.get_failed_nodes()
                    if failed:
                        task.status = TaskStatus.FAILED
                        logger.error(f"Task {task.task_id} failed: {len(failed)} nodes failed")
                        break
                    
                    # Check if all nodes are done
                    done = [n for n in task.nodes.values() if n.status == NodeStatus.DONE]
                    if len(done) == len(task.nodes):
                        task.status = TaskStatus.COMPLETED
                        logger.info(f"Task {task.task_id} completed")
                        break
                    
                    # Check for pending checkpoints
                    pending_checkpoints = [c for c in task.pending_checkpoints if c.decision is None]
                    if pending_checkpoints:
                        task.status = TaskStatus.PAUSED
                        logger.info(f"Task {task.task_id} paused for checkpoint")
                        break
                    
                    # Safety: avoid infinite loop
                    logger.warning(f"Task {task.task_id} has no ready nodes but not completed")
                    break
                
                # Execute ready nodes (bounded concurrency)
                max_workers = min(3, len(ready))
                for node in ready[:max_workers]:
                    await self._execute_node(
                        task, node,
                        policy_check, router_select, executor_fn, ledger_emit
                    )
            
            task.completed_at = datetime.utcnow().isoformat()
        
        except Exception as e:
            logger.error(f"Task execution error: {e}", exc_info=True)
            task.status = TaskStatus.FAILED
        
        # Persist final state
        await self.persistence.save(task)
        
        # Emit completion event
        if self.event_sink:
            await self.event_sink('task.completed', {
                'task_id': task.task_id,
                'status': task.status.value,
                'total_attempts': task.total_attempts,
            })
        
        return task
    
    async def _execute_node(
        self,
        task: TaskGraph,
        node: TaskNode,
        policy_check: Callable[[str, TaskNode], Awaitable[bool]],
        router_select: Callable[[TaskNode], Awaitable[str]],
        executor_fn: Callable[[TaskNode, str], Awaitable[Dict[str, Any]]],
        ledger_emit: Callable[[str, Dict[str, Any]], Awaitable[None]],
    ) -> None:
        """Execute a single node with retries and checkpoints."""
        
        node.mark_running()
        
        try:
            # Policy check
            allowed = await policy_check(task.user_id, node)
            if not allowed:
                raise PermissionError(f"User {task.user_id} not allowed to execute {node.id}")
            
            # Route to model
            model_id = await router_select(node)
            node.model_used = model_id
            
            # Execute with retries
            retry_feedback = None
            while node.attempts < task.limits.max_retries:
                node.attempts += 1
                task.total_attempts += 1
                
                try:
                    # Call executor (model or tool)
                    output = await executor_fn(node, model_id, retry_feedback)
                    
                    # Verify output
                    critic_result = await self.critic.verify(
                        node.id,
                        node.type.value,
                        output,
                        node.success_criteria,
                    )
                    
                    if critic_result.passed:
                        node.mark_done(output)
                        node.verification = {
                            'passed': True,
                            'notes': critic_result.notes,
                        }
                        break
                    else:
                        # Retry logic
                        should_retry, feedback = RetryPolicy.should_retry(
                            node.type.value,
                            node.attempts,
                            task.limits.max_retries,
                            critic_result,
                        )
                        
                        if should_retry:
                            retry_feedback = feedback
                            node.verification = {
                                'passed': False,
                                'notes': critic_result.notes,
                                'will_retry': True,
                            }
                        else:
                            node.mark_failed(f"Verification failed: {critic_result.notes}")
                            break
                
                except Exception as e:
                    logger.error(f"Node {node.id} execution error: {e}", exc_info=True)
                    should_retry, feedback = RetryPolicy.should_retry(
                        node.type.value,
                        node.attempts,
                        task.limits.max_retries,
                        None,  # No critic result; treat as retryable
                    )
                    if should_retry:
                        retry_feedback = str(e)
                    else:
                        node.mark_failed(str(e))
                        break
            
            # Checkpoint gate
            if node.status == NodeStatus.DONE and node.requires_human_review:
                checkpoint = Checkpoint(
                    id=f"c-{task.task_id}-{node.id}",
                    task_id=task.task_id,
                    node_id=node.id,
                    reason=f"Review required for {node.type.value}",
                    requested_at=datetime.utcnow().isoformat(),
                )
                task.pending_checkpoints.append(checkpoint)
                node.mark_needs_review()
                
                # Emit event
                if self.event_sink:
                    await self.event_sink('checkpoint.required', {
                        'checkpoint_id': checkpoint.id,
                        'node_id': node.id,
                        'reason': checkpoint.reason,
                    })
            
            # Emit node completion event
            if self.event_sink:
                await self.event_sink('node.completed', {
                    'node_id': node.id,
                    'status': node.status.value,
                    'model_used': node.model_used,
                    'attempts': node.attempts,
                })
            
            # Log to ledger
            await ledger_emit('node.executed', {
                'task_id': task.task_id,
                'node_id': node.id,
                'node_type': node.type.value,
                'model_used': node.model_used,
                'attempts': node.attempts,
                'status': node.status.value,
            })
        
        except Exception as e:
            logger.error(f"Node execution failed: {e}", exc_info=True)
            node.mark_failed(str(e))
    
    async def handle_checkpoint_decision(
        self,
        task_id: str,
        checkpoint_id: str,
        decision: str,  # approve | edit | reject
        edit_data: Optional[Dict[str, Any]] = None,
    ) -> TaskGraph:
        """Handle a human checkpoint decision (approve/edit/reject)."""
        
        task = await self.persistence.load(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        
        # Find checkpoint
        checkpoint = next((c for c in task.pending_checkpoints if c.id == checkpoint_id), None)
        if not checkpoint:
            raise ValueError(f"Checkpoint {checkpoint_id} not found")
        
        checkpoint.decision = decision
        checkpoint.decided_at = datetime.utcnow().isoformat()
        
        # Handle decision
        if decision == 'approve':
            node = task.nodes[checkpoint.node_id]
            node.status = NodeStatus.DONE
        elif decision == 'edit':
            # Store edited data as new output
            node = task.nodes[checkpoint.node_id]
            node.output_data = edit_data
            node.status = NodeStatus.DONE
        elif decision == 'reject':
            node = task.nodes[checkpoint.node_id]
            node.mark_failed("Rejected by human")
        
        task.pending_checkpoints.remove(checkpoint)
        task.checkpoint_decisions[checkpoint_id] = checkpoint
        
        # Resume execution if paused
        if task.status == TaskStatus.PAUSED:
            task.status = TaskStatus.RUNNING
        
        await self.persistence.save(task)
        
        return task
