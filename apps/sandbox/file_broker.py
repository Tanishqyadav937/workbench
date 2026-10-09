"""A10: File broker with path allowlist, clearance checks, and logging."""

import os
import hashlib
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from datetime import datetime

# Configuration
WORKSPACE_ROOT = Path("/workspace")
ALLOWED_PATHS = [
    "/workspace/data",
    "/workspace/uploads",
    "/workspace/exports",
    "/tmp/sandbox",
]


@dataclass
class FileAccess:
    """Record of a file access attempt."""
    timestamp: str
    actor: str
    path: str
    operation: str  # "read", "write", "delete"
    clearance: str  # "public", "confidential", "secret"
    status: str     # "allowed", "blocked"
    reason: Optional[str] = None


class FileBroker:
    """
    Enforces file access control with:
    - Path allowlist verification
    - Clearance-based filtering
    - Comprehensive logging
    """
    
    def __init__(self, workspace_root: Path = WORKSPACE_ROOT, allowed_paths: List[str] = None):
        self.workspace_root = workspace_root
        self.allowed_paths = [Path(p) for p in (allowed_paths or ALLOWED_PATHS)]
        self.access_log: List[FileAccess] = []
    
    def is_path_allowed(self, path: str) -> bool:
        """Check if a path is within the allowed list."""
        try:
            resolved = Path(path).resolve()
            for allowed in self.allowed_paths:
                allowed_resolved = allowed.resolve()
                # Check if path is under allowed directory
                resolved.relative_to(allowed_resolved)
                return True
        except ValueError:
            # Not under any allowed path
            pass
        return False
    
    def read_file(
        self,
        path: str,
        actor: str,
        clearance: str = "public"
    ) -> Optional[bytes]:
        """
        Read a file if allowed.
        
        Args:
            path: File path
            actor: User/component requesting access
            clearance: Security clearance level
            
        Returns:
            File content if allowed, None otherwise
        """
        if not self.is_path_allowed(path):
            self._log_access(actor, path, "read", clearance, "blocked", "path not allowed")
            raise PermissionError(f"Path {path} is not in allowlist")
        
        try:
            with open(path, 'rb') as f:
                content = f.read()
            self._log_access(actor, path, "read", clearance, "allowed")
            return content
        except FileNotFoundError:
            self._log_access(actor, path, "read", clearance, "blocked", "file not found")
            raise
        except Exception as e:
            self._log_access(actor, path, "read", clearance, "blocked", str(e))
            raise
    
    def write_file(
        self,
        path: str,
        content: bytes,
        actor: str,
        clearance: str = "public"
    ) -> bool:
        """
        Write a file if allowed.
        
        Args:
            path: File path
            content: Bytes to write
            actor: User/component requesting access
            clearance: Security clearance level
            
        Returns:
            True if successful
        """
        if not self.is_path_allowed(path):
            self._log_access(actor, path, "write", clearance, "blocked", "path not allowed")
            raise PermissionError(f"Path {path} is not in allowlist")
        
        try:
            # Create parent directories if needed
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'wb') as f:
                f.write(content)
            self._log_access(actor, path, "write", clearance, "allowed")
            return True
        except Exception as e:
            self._log_access(actor, path, "write", clearance, "blocked", str(e))
            raise
    
    def delete_file(
        self,
        path: str,
        actor: str,
        clearance: str = "public"
    ) -> bool:
        """Delete a file if allowed."""
        if not self.is_path_allowed(path):
            self._log_access(actor, path, "delete", clearance, "blocked", "path not allowed")
            raise PermissionError(f"Path {path} is not in allowlist")
        
        try:
            os.remove(path)
            self._log_access(actor, path, "delete", clearance, "allowed")
            return True
        except Exception as e:
            self._log_access(actor, path, "delete", clearance, "blocked", str(e))
            raise
    
    def list_directory(
        self,
        path: str,
        actor: str,
        clearance: str = "public"
    ) -> List[str]:
        """List files in a directory if allowed."""
        if not self.is_path_allowed(path):
            self._log_access(actor, path, "list", clearance, "blocked", "path not allowed")
            raise PermissionError(f"Path {path} is not in allowlist")
        
        try:
            items = [str(p) for p in Path(path).iterdir()]
            self._log_access(actor, path, "list", clearance, "allowed")
            return items
        except Exception as e:
            self._log_access(actor, path, "list", clearance, "blocked", str(e))
            raise
    
    def _log_access(
        self,
        actor: str,
        path: str,
        operation: str,
        clearance: str,
        status: str,
        reason: Optional[str] = None
    ) -> None:
        """Log file access attempt."""
        access = FileAccess(
            timestamp=datetime.utcnow().isoformat(),
            actor=actor,
            path=path,
            operation=operation,
            clearance=clearance,
            status=status,
            reason=reason
        )
        self.access_log.append(access)
    
    def get_access_log(self) -> List[Dict[str, Any]]:
        """Return access log as list of dicts."""
        return [
            {
                "timestamp": a.timestamp,
                "actor": a.actor,
                "path": a.path,
                "operation": a.operation,
                "clearance": a.clearance,
                "status": a.status,
                "reason": a.reason,
            }
            for a in self.access_log
        ]
    
    def hash_file(self, path: str) -> str:
        """Compute SHA256 hash of a file (for integrity checks)."""
        if not self.is_path_allowed(path):
            raise PermissionError(f"Path {path} is not in allowlist")
        
        sha256 = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b''):
                sha256.update(chunk)
        return sha256.hexdigest()


# Global broker instance
_broker: Optional[FileBroker] = None


def get_broker() -> FileBroker:
    """Get or create the global file broker."""
    global _broker
    if _broker is None:
        _broker = FileBroker()
    return _broker


def set_broker(broker: FileBroker) -> None:
    """Set the global file broker (for testing)."""
    global _broker
    _broker = broker
