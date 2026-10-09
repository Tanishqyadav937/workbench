"""A8: Sandbox runner for ephemeral code execution with isolation and resource limits."""

import json
import tempfile
import time
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

try:
    import docker
    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False


class ExecutionStatus(Enum):
    """Execution status codes."""
    SUCCESS = "success"
    TIMEOUT = "timeout"
    OUT_OF_MEMORY = "out_of_memory"
    NETWORK_BLOCKED = "network_blocked"
    EXECUTION_ERROR = "execution_error"
    SANDBOX_ERROR = "sandbox_error"


@dataclass
class SandboxResult:
    """Result of sandbox execution."""
    status: ExecutionStatus
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: float
    memory_used_mb: Optional[float] = None


class SandboxRunner:
    """
    Runs untrusted code in an ephemeral Docker container with:
    - Network isolation (--network none)
    - Read-only rootfs (except /tmp)
    - Resource limits (CPU, memory, timeout)
    - Seccomp profile (blocks connect, ptrace, mount)
    """
    
    def __init__(
        self,
        image: str = "python:3.12-slim",
        memory_limit_mb: int = 512,
        cpu_shares: int = 512,
        timeout_seconds: int = 30,
        seccomp_profile: Optional[Dict[str, Any]] = None,
    ):
        if not DOCKER_AVAILABLE:
            raise RuntimeError("Docker Python SDK not available. Install: pip install docker")
        
        self.client = docker.from_env()
        self.image = image
        self.memory_limit_mb = memory_limit_mb
        self.cpu_shares = cpu_shares
        self.timeout_seconds = timeout_seconds
        self.seccomp_profile = seccomp_profile
        self._load_seccomp_profile()
    
    def _load_seccomp_profile(self) -> None:
        """Load the default seccomp profile from file."""
        if self.seccomp_profile is None:
            profile_path = Path(__file__).parent / "seccomp.json"
            if profile_path.exists():
                with open(profile_path, 'r') as f:
                    self.seccomp_profile = json.load(f)
    
    def run(
        self,
        code: str,
        files: Optional[Dict[str, bytes]] = None,
        stdin: Optional[str] = None,
    ) -> SandboxResult:
        """
        Run code in a sandboxed container.
        
        Args:
            code: Python code to execute
            files: Optional dict of {filename: content} to make available
            stdin: Optional stdin input
            
        Returns:
            SandboxResult with execution status and output
        """
        start_time = time.time()
        container = None
        
        try:
            # Create a temporary directory for the code
            with tempfile.TemporaryDirectory() as tmpdir:
                script_path = Path(tmpdir) / "script.py"
                script_path.write_text(code)
                
                # Write any provided files
                if files:
                    for filename, content in files.items():
                        file_path = Path(tmpdir) / filename
                        file_path.parent.mkdir(parents=True, exist_ok=True)
                        if isinstance(content, str):
                            file_path.write_text(content)
                        else:
                            file_path.write_bytes(content)
                
                # Prepare container configuration
                container_config = {
                    "image": self.image,
                    "command": ["python3", "/code/script.py"],
                    "stdin_open": bool(stdin),
                    "network_mode": "none",  # No network access
                    "read_only": True,  # Read-only rootfs
                    "tmpfs": {
                        "/tmp": "size=100M,noexec,nosuid,nodev",
                    },
                    "volumes": {
                        tmpdir: {"bind": "/code", "mode": "ro"}
                    },
                }
                
                if DOCKER_AVAILABLE:
                    container_config["host_config"] = docker.types.HostConfig(
                        mem_limit=f"{self.memory_limit_mb}m",
                        memswap_limit=f"{self.memory_limit_mb}m",
                        cpu_shares=self.cpu_shares,
                        read_only=True,
                        tmpfs={"/tmp": "size=100M,noexec,nosuid,nodev"},
                        volumes={tmpdir: {"bind": "/code", "mode": "ro"}},
                        security_opt=self._build_security_opts(),
                    )
                
                # Create and run the container
                container = self.client.containers.create(**container_config)
                
                # Start the container with optional stdin
                container.start()
                
                # Wait for completion with timeout
                try:
                    exit_code = container.wait(timeout=self.timeout_seconds)
                    status = ExecutionStatus.SUCCESS
                except Exception:
                    # Container didn't exit in time
                    container.kill()
                    status = ExecutionStatus.TIMEOUT
                    exit_code = 124
                
                # Collect output
                output = container.logs(stdout=True, stderr=True, stream=False)
                if isinstance(output, bytes):
                    output = output.decode('utf-8', errors='replace')
                
                stdout_bytes, stderr_bytes = b"", b""
                try:
                    # Try to get separate stdout/stderr
                    stdout_bytes = container.logs(stdout=True, stderr=False) or b""
                    stderr_bytes = container.logs(stdout=False, stderr=True) or b""
                except:
                    # Fallback to combined output
                    stdout_bytes = output.encode('utf-8')
                
                stdout = stdout_bytes.decode('utf-8', errors='replace')
                stderr = stderr_bytes.decode('utf-8', errors='replace')
                
                # Get memory usage
                stats = container.stats(stream=False)
                memory_used_mb = (
                    stats.get("memory_stats", {}).get("usage", 0) / (1024 * 1024)
                )
                
                elapsed_ms = (time.time() - start_time) * 1000
                
                return SandboxResult(
                    status=status,
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=exit_code,
                    duration_ms=elapsed_ms,
                    memory_used_mb=memory_used_mb,
                )
        
        except Exception as e:
            elapsed_ms = (time.time() - start_time) * 1000
            return SandboxResult(
                status=ExecutionStatus.SANDBOX_ERROR,
                stdout="",
                stderr=str(e),
                exit_code=1,
                duration_ms=elapsed_ms,
            )
        
        finally:
            # Clean up container
            if container:
                try:
                    container.remove(force=True)
                except:
                    pass
    
    def _build_security_opts(self) -> list:
        """Build Docker security options."""
        opts = [
            "no-new-privileges=true",
        ]
        
        # Add seccomp if available
        if self.seccomp_profile:
            with tempfile.NamedTemporaryFile(
                mode='w', suffix='.json', delete=False
            ) as f:
                json.dump(self.seccomp_profile, f)
                opts.append(f"seccomp={f.name}")
        
        return opts


def run_code_safe(
    code: str,
    files: Optional[Dict[str, bytes]] = None,
    stdin: Optional[str] = None,
    memory_mb: int = 512,
    timeout_sec: int = 30,
) -> SandboxResult:
    """
    Convenience function to run code safely in sandbox.
    
    Example:
        result = run_code_safe("print('hello')")
        print(result.stdout)  # hello
    """
    runner = SandboxRunner(
        memory_limit_mb=memory_mb,
        timeout_seconds=timeout_sec,
    )
    return runner.run(code, files, stdin)
