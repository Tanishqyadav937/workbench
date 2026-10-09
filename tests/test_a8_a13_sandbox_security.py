"""Tests for A8-A13: Sandbox security, file broker, tool registry, and model verification."""

import unittest
import tempfile
from pathlib import Path
import sys
import os

# Add apps to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'apps'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'libs'))

from sandbox.file_broker import FileBroker
from sandbox.runner import SandboxRunner, ExecutionStatus
from tool_broker.registry import ToolRegistry, Tool, ToolStatus
from model_verifier import ModelVerifier, ModelManifest
from exfiltration_test import run_exfiltration_tests


class TestFileBroker(unittest.TestCase):
    """Test A10: File broker with allowlist and clearance checks."""
    
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.broker = FileBroker(
            workspace_root=Path(self.tmpdir.name),
            allowed_paths=[self.tmpdir.name]
        )
    
    def tearDown(self):
        self.tmpdir.cleanup()
    
    def test_allowed_path_read_write(self):
        """Test reading/writing within allowed path."""
        test_file = os.path.join(self.tmpdir.name, "test.txt")
        
        # Write
        self.broker.write_file(test_file, b"hello", "test_actor")
        
        # Read
        content = self.broker.read_file(test_file, "test_actor")
        self.assertEqual(content, b"hello")
        
        # Check log
        log = self.broker.get_access_log()
        self.assertGreaterEqual(len(log), 2)
        self.assertEqual(log[-2]["operation"], "write")
        self.assertEqual(log[-1]["operation"], "read")
    
    def test_blocked_outside_allowlist(self):
        """Test that paths outside allowlist are blocked."""
        blocked_path = "/etc/passwd"
        
        with self.assertRaises(PermissionError):
            self.broker.read_file(blocked_path, "test_actor")
        
        # Check log
        log = self.broker.get_access_log()
        self.assertEqual(log[-1]["status"], "blocked")
    
    def test_file_operations_logged(self):
        """Test that all file operations are logged."""
        test_file = os.path.join(self.tmpdir.name, "logged.txt")
        
        self.broker.write_file(test_file, b"data", "actor_a", "confidential")
        self.broker.read_file(test_file, "actor_b", "public")
        self.broker.delete_file(test_file, "actor_a")
        
        log = self.broker.get_access_log()
        self.assertGreaterEqual(len(log), 3)
        self.assertEqual(log[-3]["operation"], "write")
        self.assertEqual(log[-2]["operation"], "read")
        self.assertEqual(log[-1]["operation"], "delete")


class TestSandboxRunner(unittest.TestCase):
    """Test A8: Sandbox runner with isolation and resource limits."""
    
    def test_sandbox_runner_init_requires_docker(self):
        """Test that sandbox runner requires Docker."""
        try:
            runner = SandboxRunner()
            # If we got here, Docker is available - skip this test
            self.skipTest("Docker is available")
        except RuntimeError as e:
            # Expected when Docker SDK not installed
            self.assertIn("Docker", str(e))


class TestToolRegistry(unittest.TestCase):
    """Test A11: Tool registry with schema validation."""
    
    def setUp(self):
        self.registry = ToolRegistry()
    
    def test_register_tool(self):
        """Test registering a tool."""
        schema = {"type": "object", "properties": {"x": {"type": "number"}}}
        
        self.registry.register(
            name="test_tool",
            description="Test tool",
            input_schema=schema,
            output_schema={"type": "object"},
            executor=lambda x: x,
        )
        
        tool = self.registry.get_tool("test_tool")
        self.assertIsNotNone(tool)
        self.assertEqual(tool["name"], "test_tool")
    
    def test_validate_input(self):
        """Test input validation against schema."""
        schema = {"type": "object", "properties": {"x": {"type": "number"}}}
        
        self.registry.register(
            name="math_tool",
            description="Math tool",
            input_schema=schema,
            output_schema={"type": "number"},
            executor=lambda x: x["x"],
        )
        
        # Valid input
        self.assertTrue(self.registry.validate_input("math_tool", {"x": 42}))
        
        # Invalid input
        with self.assertRaises(ValueError):
            self.registry.validate_input("math_tool", {"x": "not_a_number"})
    
    def test_role_based_access(self):
        """Test role-based tool access control."""
        self.registry.register(
            name="admin_tool",
            description="Admin tool",
            input_schema={},
            output_schema={},
            executor=lambda: None,
            allowed_roles=["admin"],
        )
        
        self.registry.register(
            name="public_tool",
            description="Public tool",
            input_schema={},
            output_schema={},
            executor=lambda: None,
            allowed_roles=[],  # All roles
        )
        
        # Admin sees both
        admin_tools = self.registry.list_tools(role="admin")
        self.assertEqual(len(admin_tools), 2)
        
        # User sees only public
        user_tools = self.registry.list_tools(role="user")
        self.assertEqual(len(user_tools), 1)
        self.assertEqual(user_tools[0]["name"], "public_tool")


class TestModelVerifier(unittest.TestCase):
    """Test A13: Model weight verification."""
    
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.manifest_dir = Path(self.tmpdir.name) / "manifests"
        self.verifier = ModelVerifier(self.manifest_dir)
    
    def tearDown(self):
        self.tmpdir.cleanup()
    
    def test_create_manifest(self):
        """Test creating a model manifest."""
        # Create a dummy model file
        model_file = Path(self.tmpdir.name) / "model.bin"
        model_file.write_bytes(b"model_content")
        
        manifest = self.verifier.create_manifest(
            model_name="test_model",
            version="1.0",
            file_path=str(model_file)
        )
        
        self.assertEqual(manifest.model_name, "test_model")
        self.assertEqual(manifest.version, "1.0")
        self.assertEqual(manifest.file_size_bytes, 13)
        self.assertIsNotNone(manifest.sha256_hash)
    
    def test_manifest_save_and_verify(self):
        """Test saving and verifying a manifest."""
        model_file = Path(self.tmpdir.name) / "model.bin"
        model_file.write_bytes(b"model_content")
        
        # Create and save manifest
        manifest = self.verifier.create_manifest(
            model_name="test_model",
            version="1.0",
            file_path=str(model_file)
        )
        self.verifier.save_manifest(manifest)
        
        # Verify
        is_valid, msg = self.verifier.verify_model(
            "test_model", "1.0", str(model_file)
        )
        self.assertTrue(is_valid)
        self.assertIn("verified successfully", msg)
    
    def test_detect_tampering(self):
        """Test detection of tampered model file."""
        model_file = Path(self.tmpdir.name) / "model.bin"
        model_file.write_bytes(b"original_content")
        
        # Create and save manifest
        manifest = self.verifier.create_manifest(
            model_name="test_model",
            version="1.0",
            file_path=str(model_file)
        )
        self.verifier.save_manifest(manifest)
        
        # Tamper with file
        model_file.write_bytes(b"tampered_content")
        
        # Verify should fail
        is_valid, msg = self.verifier.verify_model(
            "test_model", "1.0", str(model_file)
        )
        self.assertFalse(is_valid)
        self.assertIn("Hash mismatch", msg)


class TestExfiltrationPrevention(unittest.TestCase):
    """Test A12: Exfiltration prevention."""
    
    def test_exfiltration_tests_run(self):
        """Test that exfiltration tests can run (without Docker)."""
        results = run_exfiltration_tests(sandbox_runner=None)
        
        # Should have test results
        self.assertIn("socket", results)
        self.assertIn("http", results)
        self.assertIn("dns", results)
        self.assertIn("file_write", results)
        
        # All should be ExfiltrationResult objects
        for result in results.values():
            self.assertIsNotNone(result.attempt_type)
            self.assertIsNotNone(result.message)


if __name__ == '__main__':
    unittest.main()
