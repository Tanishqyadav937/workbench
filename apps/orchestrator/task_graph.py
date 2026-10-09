"""B7: Task graph model, state machine and Postgres persistence."""

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, List, Optional, Any, Set
from datetime import datetime
import json
import uuid
from abc import ABC, abstractmethod


class TaskStatus(str, Enum):
    """Task-level status."""
    PLANNING = "planning"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class NodeStatus(str, Enum):
    """Node-level status in the task graph."""
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class NodeType(str, Enum):
    """Registered node types."""
    OCR_EXTRACT = "ocr_extract"
    RETRIEVE_SOPS = "retrieve_sops"
    DRAFT_NOTE = "draft_note"
    RUN_PYTHON = "run_python"
    APPROVE_NOTE = "approve_note"
    EXPORT_DOCX = "export_docx"


@dataclass
class TaskLimits:
    """Execution limits (bounded autonomy, FR-2.5)."""
    max_nodes: int = 12
    max_retries: int = 3
    max_tool_calls: int = 30
    timeout_s: int = 1800
    max_depth: int = 4


@dataclass
class RouterDecision:
    """Router decision record (visible in trace)."""
    task_id: str
    node_id: str
    classification: Dict[str, Any]
    rule_matched: str
    candidates: List[str]
    selected: str
    reason: str
    latency_ms: float


@dataclass
class TaskNode:
    """Single node in the task DAG."""
    id: str
    type: NodeType
    depends_on: List[str] = field(default_factory=list)
    
    status: NodeStatus = NodeStatus.PENDING
    model_used: Optional[str] = None
    inputs: Dict[str, Any] = field(default_factory=dict)
    output_ref: Optional[str] = None  # artifact://node_id
    output_data: Optional[Dict[str, Any]] = None
    
    success_criteria: str = ""
    verification: Dict[str, Any] = field(default_factory=dict)
    requires_human_review: bool = False
    
    attempts: int = 0
    last_error: Optional[str] = None
    
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    
    def is_ready(self, all_nodes: Dict[str, 'TaskNode']) -> bool:
        """Check if all dependencies are done."""
        if self.status != NodeStatus.PENDING:
            return False
        return all(all_nodes[dep_id].status == NodeStatus.DONE for dep_id in self.depends_on)
    
    def mark_running(self) -> None:
        self.status = NodeStatus.RUNNING
        self.updated_at = datetime.utcnow().isoformat()
    
    def mark_done(self, output_data: Optional[Dict[str, Any]] = None) -> None:
        self.status = NodeStatus.DONE
        self.output_data = output_data
        self.updated_at = datetime.utcnow().isoformat()
    
    def mark_failed(self, error: str) -> None:
        self.status = NodeStatus.FAILED
        self.last_error = error
        self.updated_at = datetime.utcnow().isoformat()
    
    def mark_needs_review(self) -> None:
        self.status = NodeStatus.NEEDS_REVIEW
        self.updated_at = datetime.utcnow().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Checkpoint:
    """Human-in-the-loop checkpoint (B10)."""
    id: str
    task_id: str
    node_id: str
    reason: str
    requested_at: str
    
    decided_by: Optional[str] = None
    decision: Optional[str] = None  # approve | edit | reject
    decided_at: Optional[str] = None
    edit_data: Optional[Dict[str, Any]] = None


@dataclass
class TaskGraph:
    """Root task state (Postgres-backed)."""
    task_id: str
    goal: str
    purpose: str
    user_id: str
    
    status: TaskStatus = TaskStatus.PLANNING
    nodes: Dict[str, TaskNode] = field(default_factory=dict)
    limits: TaskLimits = field(default_factory=TaskLimits)
    
    # Audit trail
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    
    # Checkpoint queue
    pending_checkpoints: List[Checkpoint] = field(default_factory=list)
    checkpoint_decisions: Dict[str, Checkpoint] = field(default_factory=dict)
    
    # Execution stats
    total_attempts: int = 0
    tool_calls_made: int = 0
    
    def add_node(self, node: TaskNode) -> None:
        """Add a node to the graph (from planner)."""
        if node.id in self.nodes:
            raise ValueError(f"Node {node.id} already exists")
        self.nodes[node.id] = node
        self.updated_at = datetime.utcnow().isoformat()
    
    def ready_nodes(self) -> List[TaskNode]:
        """Return all nodes that are ready to execute."""
        return [n for n in self.nodes.values() if n.is_ready(self.nodes)]
    
    def get_failed_nodes(self) -> List[TaskNode]:
        """Return nodes that failed (not retryable)."""
        return [n for n in self.nodes.values() if n.status == NodeStatus.FAILED]
    
    def validate_dag(self) -> tuple[bool, str]:
        """Check for cycles and other structural issues."""
        visited: Set[str] = set()
        rec_stack: Set[str] = set()
        
        def has_cycle(node_id: str) -> bool:
            visited.add(node_id)
            rec_stack.add(node_id)
            
            node = self.nodes.get(node_id)
            if not node:
                return False
            
            for dep in node.depends_on:
                if dep not in visited:
                    if has_cycle(dep):
                        return True
                elif dep in rec_stack:
                    return True
            
            rec_stack.remove(node_id)
            return False
        
        # Check for cycles
        for node_id in self.nodes:
            if node_id not in visited:
                if has_cycle(node_id):
                    return False, f"Cycle detected in task graph"
        
        # Check node count
        if len(self.nodes) > self.limits.max_nodes:
            return False, f"Node count {len(self.nodes)} exceeds limit {self.limits.max_nodes}"
        
        # Check depth
        def get_depth(node_id: str) -> int:
            node = self.nodes[node_id]
            if not node.depends_on:
                return 1
            return 1 + max(get_depth(dep) for dep in node.depends_on)
        
        max_depth = max((get_depth(nid) for nid in self.nodes), default=1)
        if max_depth > self.limits.max_depth:
            return False, f"Graph depth {max_depth} exceeds limit {self.limits.max_depth}"
        
        return True, "DAG valid"
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON for storage."""
        return {
            'task_id': self.task_id,
            'goal': self.goal,
            'purpose': self.purpose,
            'user_id': self.user_id,
            'status': self.status.value,
            'nodes': {nid: n.to_dict() for nid, n in self.nodes.items()},
            'limits': asdict(self.limits),
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'started_at': self.started_at,
            'completed_at': self.completed_at,
            'total_attempts': self.total_attempts,
            'tool_calls_made': self.tool_calls_made,
            'pending_checkpoints': [asdict(c) for c in self.pending_checkpoints],
            'checkpoint_decisions': {k: asdict(v) for k, v in self.checkpoint_decisions.items()},
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TaskGraph':
        """Deserialize from JSON."""
        task = cls(
            task_id=data['task_id'],
            goal=data['goal'],
            purpose=data['purpose'],
            user_id=data['user_id'],
            status=TaskStatus(data['status']),
            created_at=data['created_at'],
            updated_at=data['updated_at'],
            started_at=data.get('started_at'),
            completed_at=data.get('completed_at'),
            total_attempts=data.get('total_attempts', 0),
            tool_calls_made=data.get('tool_calls_made', 0),
        )
        
        # Rebuild nodes
        for nid, node_data in data.get('nodes', {}).items():
            node_data_copy = dict(node_data)
            node_data_copy['type'] = NodeType(node_data_copy['type'])
            node_data_copy['status'] = NodeStatus(node_data_copy['status'])
            node_data_copy['inputs'] = node_data_copy.get('inputs', {})
            node = TaskNode(**node_data_copy)
            task.nodes[nid] = node
        
        # Rebuild limits
        if 'limits' in data:
            task.limits = TaskLimits(**data['limits'])
        
        # Rebuild pending checkpoints
        for c in data.get('pending_checkpoints', []):
            task.pending_checkpoints.append(Checkpoint(**c))
        
        # Rebuild checkpoint decisions
        for k, c in data.get('checkpoint_decisions', {}).items():
            task.checkpoint_decisions[k] = Checkpoint(**c)
        
        return task


class PersistenceBackend(ABC):
    """Abstract interface for task graph persistence."""
    
    @abstractmethod
    async def save(self, task: TaskGraph) -> None:
        """Persist task graph to storage."""
        pass
    
    @abstractmethod
    async def load(self, task_id: str) -> Optional[TaskGraph]:
        """Load task graph from storage."""
        pass
    
    @abstractmethod
    async def create(self, task: TaskGraph) -> None:
        """Create a new task in storage."""
        pass
    
    @abstractmethod
    async def list_tasks(self, user_id: str, limit: int = 10) -> List[TaskGraph]:
        """List recent tasks for a user."""
        pass
