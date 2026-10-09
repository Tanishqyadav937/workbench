"""B8: Planner — decompose user goals into task DAGs using an LLM."""

import json
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from abc import ABC, abstractmethod

from .task_graph import TaskGraph, TaskNode, NodeType, TaskLimits


@dataclass
class PlanRequest:
    """Input to the planner."""
    goal: str
    purpose: str
    user_id: str
    user_clearance: str
    context: Optional[str] = None
    constraints: Optional[Dict[str, Any]] = None


@dataclass
class PlanResponse:
    """LLM-generated plan (JSON-constrained)."""
    nodes: List[Dict[str, Any]]
    reasoning: str


class ModelInterface(ABC):
    """Interface to the model backend (via model-gateway)."""
    
    @abstractmethod
    async def generate(self, prompt: str, schema: Optional[Dict] = None) -> str:
        """Call the model with optional schema-constrained output."""
        pass


class Planner:
    """B8: Converts goals into task graphs."""
    
    # Registered node types that the planner can emit
    ALLOWED_NODE_TYPES = {
        NodeType.OCR_EXTRACT,
        NodeType.RETRIEVE_SOPS,
        NodeType.DRAFT_NOTE,
        NodeType.RUN_PYTHON,
        NodeType.APPROVE_NOTE,
        NodeType.EXPORT_DOCX,
    }
    
    def __init__(self, model: ModelInterface):
        self.model = model
    
    async def plan(self, request: PlanRequest) -> TaskGraph:
        """Generate a task DAG from a user goal."""
        
        # Build the prompt
        prompt = self._build_prompt(request)
        
        # JSON schema for constrained generation
        schema = self._get_output_schema()
        
        # Call model
        response_text = await self.model.generate(prompt, schema)
        
        # Parse response
        try:
            response_data = json.loads(response_text)
            plan_response = PlanResponse(**response_data)
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(f"Planner output not valid JSON: {e}")
        
        # Build task graph from plan
        task = TaskGraph(
            task_id=self._generate_task_id(),
            goal=request.goal,
            purpose=request.purpose,
            user_id=request.user_id,
            limits=TaskLimits(),
        )
        
        # Validate and add nodes
        node_ids = set()
        for node_spec in plan_response.nodes:
            # Validate node type
            try:
                node_type = NodeType(node_spec['type'])
                if node_type not in self.ALLOWED_NODE_TYPES:
                    raise ValueError(f"Unregistered node type: {node_type}")
            except (KeyError, ValueError) as e:
                raise ValueError(f"Invalid node in plan: {e}")
            
            # Build node
            node = TaskNode(
                id=node_spec.get('id', f"n{len(node_ids)+1}"),
                type=node_type,
                depends_on=node_spec.get('depends_on', []),
                inputs=node_spec.get('inputs', {}),
                success_criteria=node_spec.get('success_criteria', ''),
                requires_human_review=node_spec.get('requires_human_review', False),
            )
            
            node_ids.add(node.id)
            task.add_node(node)
        
        # Validate DAG structure
        valid, msg = task.validate_dag()
        if not valid:
            raise ValueError(f"Invalid task DAG: {msg}")
        
        # Validate that all dependencies exist
        for node in task.nodes.values():
            for dep in node.depends_on:
                if dep not in task.nodes:
                    raise ValueError(f"Node {node.id} depends on non-existent {dep}")
        
        return task
    
    def _build_prompt(self, request: PlanRequest) -> str:
        """Construct the planner prompt."""
        return f"""You are a task planner for an AI agent that helps with document analysis and approval workflows.

User goal: {request.goal}
Purpose: {request.purpose}
User clearance: {request.user_clearance}

Available task types:
- ocr_extract: Extract fields from scanned/image documents using OCR+VLM. Output: fields with confidence scores.
- retrieve_sops: Search the knowledge base for relevant SOPs or procedures. Input: keywords. Output: retrieved chunks with citations.
- draft_note: Generate an approval note using retrieved information. Input: extracted fields, SOPs. Output: draft text.
- run_python: Execute Python code in a sandbox. Input: code, files. Output: result, logs.
- approve_note: Human checkpoint to review and approve the draft note.
- export_docx: Render the approved note as a .docx file with proper formatting and citations.

Create a plan as a DAG of nodes. Minimize depth and avoid unnecessary steps.

Respond in this exact JSON format:
{{
  "nodes": [
    {{"id": "n1", "type": "ocr_extract", "depends_on": [], "inputs": {{}}, "success_criteria": "...", "requires_human_review": false}},
    {{"id": "n2", "type": "retrieve_sops", "depends_on": ["n1"], "inputs": {{"keywords": "..."}}, "success_criteria": "...", "requires_human_review": false}},
    {{"id": "n3", "type": "draft_note", "depends_on": ["n1", "n2"], "inputs": {{}}, "success_criteria": "...", "requires_human_review": false}},
    {{"id": "n4", "type": "approve_note", "depends_on": ["n3"], "inputs": {{}}, "success_criteria": "...", "requires_human_review": true}},
    {{"id": "n5", "type": "export_docx", "depends_on": ["n4"], "inputs": {{}}, "success_criteria": "...", "requires_human_review": false}}
  ],
  "reasoning": "The plan follows the standard inspection report workflow..."
}}
"""
    
    def _get_output_schema(self) -> Dict[str, Any]:
        """JSON schema for constrained generation."""
        return {
            "type": "object",
            "properties": {
                "nodes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "type": {
                                "type": "string",
                                "enum": [t.value for t in self.ALLOWED_NODE_TYPES],
                            },
                            "depends_on": {"type": "array", "items": {"type": "string"}},
                            "inputs": {"type": "object"},
                            "success_criteria": {"type": "string"},
                            "requires_human_review": {"type": "boolean"},
                        },
                        "required": ["id", "type", "depends_on"],
                    },
                },
                "reasoning": {"type": "string"},
            },
            "required": ["nodes", "reasoning"],
        }
    
    @staticmethod
    def _generate_task_id() -> str:
        """Generate a unique task ID."""
        import uuid
        return f"t-{uuid.uuid4().hex[:8]}"


class PlanValidator:
    """Validates a plan before execution."""
    
    @staticmethod
    def validate(task: TaskGraph) -> tuple[bool, str]:
        """Run structural and policy checks on the plan."""
        
        # Check DAG structure
        valid, msg = task.validate_dag()
        if not valid:
            return False, msg
        
        # Check that all nodes have registered types
        for node in task.nodes.values():
            try:
                NodeType(node.type.value if hasattr(node.type, 'value') else node.type)
            except ValueError:
                return False, f"Unknown node type: {node.type}"
        
        # Check node count limit
        if len(task.nodes) > task.limits.max_nodes:
            return False, f"Too many nodes: {len(task.nodes)} > {task.limits.max_nodes}"
        
        return True, "Plan is valid"
