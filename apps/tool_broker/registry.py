"""A11: Tool registry with schema validation and availability tracking."""

import json
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum
from jsonschema import validate, ValidationError
import yaml


class ToolStatus(Enum):
    """Tool availability status."""
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"
    MAINTENANCE = "maintenance"


@dataclass
class Tool:
    """Tool definition with schema and metadata."""
    name: str
    description: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    status: ToolStatus = ToolStatus.AVAILABLE
    tags: List[str] = None
    allowed_roles: List[str] = None  # If empty, all roles allowed
    
    def __post_init__(self):
        if self.tags is None:
            self.tags = []
        if self.allowed_roles is None:
            self.allowed_roles = []
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "status": self.status.value,
            "tags": self.tags,
            "allowed_roles": self.allowed_roles,
        }


class ToolRegistry:
    """
    Central registry for available tools with:
    - JSON schema validation
    - Role-based access control
    - Status tracking
    - Health checks
    """
    
    def __init__(self):
        self.tools: Dict[str, Tool] = {}
        self.executors: Dict[str, callable] = {}
    
    def register(
        self,
        name: str,
        description: str,
        input_schema: Dict[str, Any],
        output_schema: Dict[str, Any],
        executor: callable,
        tags: List[str] = None,
        allowed_roles: List[str] = None,
    ) -> None:
        """
        Register a new tool.
        
        Args:
            name: Unique tool name
            description: Human-readable description
            input_schema: JSON schema for inputs
            output_schema: JSON schema for outputs
            executor: Callable that implements the tool
            tags: Optional tags for categorization
            allowed_roles: Optional role allowlist (empty = all)
        """
        tool = Tool(
            name=name,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            tags=tags or [],
            allowed_roles=allowed_roles or [],
        )
        self.tools[name] = tool
        self.executors[name] = executor
    
    def unregister(self, name: str) -> None:
        """Unregister a tool."""
        self.tools.pop(name, None)
        self.executors.pop(name, None)
    
    def list_tools(
        self,
        role: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        List available tools.
        
        Args:
            role: Filter by role (only return tools accessible by this role)
            tag: Filter by tag
            
        Returns:
            List of tool definitions
        """
        result = []
        for tool in self.tools.values():
            # Check status
            if tool.status != ToolStatus.AVAILABLE:
                continue
            
            # Check role
            if role and tool.allowed_roles and role not in tool.allowed_roles:
                continue
            
            # Check tag
            if tag and tag not in tool.tags:
                continue
            
            result.append(tool.to_dict())
        
        return result
    
    def get_tool(self, name: str) -> Optional[Dict[str, Any]]:
        """Get tool definition by name."""
        tool = self.tools.get(name)
        return tool.to_dict() if tool else None
    
    def validate_input(self, tool_name: str, inputs: Dict[str, Any]) -> bool:
        """
        Validate inputs against tool schema.
        
        Args:
            tool_name: Name of the tool
            inputs: Input dictionary
            
        Returns:
            True if valid
            
        Raises:
            ValueError if tool not found or validation fails
        """
        tool = self.tools.get(tool_name)
        if not tool:
            raise ValueError(f"Tool {tool_name} not found")
        
        try:
            validate(instance=inputs, schema=tool.input_schema)
            return True
        except ValidationError as e:
            raise ValueError(f"Invalid input for {tool_name}: {e.message}")
    
    def set_tool_status(self, name: str, status: ToolStatus) -> None:
        """Update tool status."""
        if name in self.tools:
            self.tools[name].status = status
    
    def get_executor(self, name: str) -> Optional[callable]:
        """Get executor function for a tool."""
        return self.executors.get(name)
    
    def load_from_yaml(self, yaml_path: str) -> None:
        """
        Load tool definitions from YAML file.
        
        Expected format:
        tools:
          - name: run_python
            description: Execute Python code
            input_schema: {...}
            output_schema: {...}
            tags: [code, execution]
            allowed_roles: [engineer, analyst]
        """
        with open(yaml_path, 'r') as f:
            config = yaml.safe_load(f)
        
        for tool_def in config.get('tools', []):
            self.register(
                name=tool_def['name'],
                description=tool_def.get('description', ''),
                input_schema=tool_def.get('input_schema', {}),
                output_schema=tool_def.get('output_schema', {}),
                executor=lambda x: x,  # Placeholder
                tags=tool_def.get('tags', []),
                allowed_roles=tool_def.get('allowed_roles', []),
            )
    
    def to_dict(self) -> Dict[str, Any]:
        """Export registry to dict."""
        return {
            name: tool.to_dict()
            for name, tool in self.tools.items()
        }


# Global registry
_registry: Optional[ToolRegistry] = None


def get_registry() -> ToolRegistry:
    """Get or create global tool registry."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry


def set_registry(registry: ToolRegistry) -> None:
    """Set global tool registry."""
    global _registry
    _registry = registry
