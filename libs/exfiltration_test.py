"""A12: Exfiltration test fixture - verify no egress from isolated network."""

import socket
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Tuple
from dataclasses import dataclass


@dataclass
class ExfiltrationResult:
    """Result of an exfiltration attempt."""
    attempt_type: str  # "socket", "http", "dns", "file_write"
    success: bool      # True = blocked, False = leaked
    message: str


def test_socket_exfiltration() -> ExfiltrationResult:
    """
    Attempt to connect to an external address.
    Expected: blocked by network isolation or seccomp.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        # Try to connect to 8.8.8.8 (Google DNS)
        sock.connect(("8.8.8.8", 53))
        sock.close()
        return ExfiltrationResult(
            attempt_type="socket",
            success=False,
            message="Socket connection succeeded (SECURITY ISSUE!)"
        )
    except (socket.error, OSError) as e:
        return ExfiltrationResult(
            attempt_type="socket",
            success=True,
            message=f"Socket connection blocked: {e}"
        )


def test_http_exfiltration() -> ExfiltrationResult:
    """
    Attempt to make HTTP request.
    Expected: blocked.
    """
    try:
        import urllib.request
        urllib.request.urlopen("http://example.com", timeout=2)
        return ExfiltrationResult(
            attempt_type="http",
            success=False,
            message="HTTP request succeeded (SECURITY ISSUE!)"
        )
    except Exception as e:
        return ExfiltrationResult(
            attempt_type="http",
            success=True,
            message=f"HTTP request blocked: {e}"
        )


def test_dns_exfiltration() -> ExfiltrationResult:
    """
    Attempt to resolve external domain.
    Expected: blocked or fails.
    """
    try:
        socket.gethostbyname("example.com")
        return ExfiltrationResult(
            attempt_type="dns",
            success=False,
            message="DNS resolution succeeded (SECURITY ISSUE!)"
        )
    except socket.gaierror as e:
        return ExfiltrationResult(
            attempt_type="dns",
            success=True,
            message=f"DNS blocked: {e}"
        )


def test_file_write_outside_workspace() -> ExfiltrationResult:
    """
    Attempt to write outside the workspace.
    Expected: blocked by file broker.
    """
    try:
        test_file = "/tmp/test_exfil_outside_workspace.txt"
        with open(test_file, 'w') as f:
            f.write("leaked data")
        return ExfiltrationResult(
            attempt_type="file_write",
            success=False,
            message=f"Write to {test_file} succeeded (SECURITY ISSUE!)"
        )
    except Exception as e:
        return ExfiltrationResult(
            attempt_type="file_write",
            success=True,
            message=f"Write blocked: {e}"
        )


def run_exfiltration_tests(sandbox_runner=None) -> Dict[str, ExfiltrationResult]:
    """
    Run all exfiltration tests.
    
    Args:
        sandbox_runner: Optional SandboxRunner to run tests in (for true isolation test)
        
    Returns:
        Dict of {test_name: result}
    """
    results = {}
    
    if sandbox_runner:
        # Run in sandbox for true isolation test
        tests = [
            ("socket", _SOCKET_TEST_CODE),
            ("http", _HTTP_TEST_CODE),
            ("dns", _DNS_TEST_CODE),
            ("file_write", _FILE_WRITE_TEST_CODE),
        ]
        
        for test_name, code in tests:
            sandbox_result = sandbox_runner.run(code)
            # Check if code exited with error (connection blocked)
            if sandbox_result.exit_code != 0:
                results[test_name] = ExfiltrationResult(
                    attempt_type=test_name,
                    success=True,
                    message=f"Sandbox: {sandbox_result.stderr[:100]}"
                )
            else:
                results[test_name] = ExfiltrationResult(
                    attempt_type=test_name,
                    success=False,
                    message=f"Sandbox exfiltration succeeded (ISSUE!)"
                )
    else:
        # Run in current process
        results["socket"] = test_socket_exfiltration()
        results["http"] = test_http_exfiltration()
        results["dns"] = test_dns_exfiltration()
        results["file_write"] = test_file_write_outside_workspace()
    
    return results


def print_exfiltration_report(results: Dict[str, ExfiltrationResult]) -> None:
    """Print a formatted report of exfiltration test results."""
    print("\n" + "=" * 70)
    print("EXFILTRATION TEST RESULTS")
    print("=" * 70)
    
    all_passed = True
    for test_name, result in results.items():
        status = "✅ BLOCKED" if result.success else "❌ LEAKED"
        print(f"{status:12} {test_name:15} {result.message}")
        if not result.success:
            all_passed = False
    
    print("=" * 70)
    if all_passed:
        print("✅ ALL EXFILTRATION TESTS PASSED (0 EGRESS)")
    else:
        print("❌ SOME TESTS FAILED - SECURITY ISSUE DETECTED")
    print("=" * 70)
    
    return all_passed


# Test code snippets to run in sandbox
_SOCKET_TEST_CODE = """
import socket
try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    sock.connect(("8.8.8.8", 53))
    sock.close()
    print("LEAK: Socket connection succeeded")
    exit(0)
except (socket.error, OSError) as e:
    print(f"BLOCKED: {e}")
    exit(1)
"""

_HTTP_TEST_CODE = """
try:
    import urllib.request
    urllib.request.urlopen("http://example.com", timeout=2)
    print("LEAK: HTTP request succeeded")
    exit(0)
except Exception as e:
    print(f"BLOCKED: {e}")
    exit(1)
"""

_DNS_TEST_CODE = """
import socket
try:
    result = socket.gethostbyname("example.com")
    print(f"LEAK: DNS resolution succeeded: {result}")
    exit(0)
except socket.gaierror as e:
    print(f"BLOCKED: {e}")
    exit(1)
"""

_FILE_WRITE_TEST_CODE = """
try:
    with open("/tmp/exfil_test.txt", "w") as f:
        f.write("leaked")
    print("LEAK: File write succeeded")
    exit(0)
except Exception as e:
    print(f"BLOCKED: {e}")
    exit(1)
"""
