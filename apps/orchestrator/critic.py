"""B9: Critic — deterministic verification of node outputs."""

from dataclasses import dataclass
from typing import Dict, List, Any, Optional, Tuple
from enum import Enum
import json
import re


class VerificationLevel(str, Enum):
    """Verification rigor."""
    DETERMINISTIC = "deterministic"
    LLM_VERIFIER = "llm_verifier"  # P2


@dataclass
class CriticResult:
    """Output of critic check."""
    passed: bool
    notes: str
    required_fixes: List[str]  # Suggestions for retry
    retry_prompt_delta: Optional[str] = None  # Feedback for the model


class Critic:
    """B9: Verifies node outputs against success criteria."""
    
    def __init__(self, verification_level: VerificationLevel = VerificationLevel.DETERMINISTIC):
        self.level = verification_level
    
    async def verify(
        self,
        node_id: str,
        node_type: str,
        output: Dict[str, Any],
        success_criteria: str,
    ) -> CriticResult:
        """Check a node's output against its success criteria."""
        
        # Dispatch by node type
        if node_type == "ocr_extract":
            return await self._verify_ocr_extract(output, success_criteria)
        elif node_type == "retrieve_sops":
            return await self._verify_retrieve_sops(output, success_criteria)
        elif node_type == "draft_note":
            return await self._verify_draft_note(output, success_criteria)
        elif node_type == "run_python":
            return await self._verify_run_python(output, success_criteria)
        elif node_type == "approve_note":
            return await self._verify_approve_note(output, success_criteria)
        elif node_type == "export_docx":
            return await self._verify_export_docx(output, success_criteria)
        else:
            return CriticResult(
                passed=False,
                notes=f"Unknown node type: {node_type}",
                required_fixes=[],
            )
    
    async def _verify_ocr_extract(self, output: Dict[str, Any], criteria: str) -> CriticResult:
        """Verify OCR extraction output."""
        issues = []
        
        # Check required fields
        if 'fields' not in output:
            issues.append("Missing 'fields' in output")
        else:
            fields = output['fields']
            if not isinstance(fields, list):
                issues.append("'fields' must be a list")
            elif len(fields) == 0:
                issues.append("No fields extracted")
            else:
                # Check each field has name, value, confidence
                for i, field in enumerate(fields):
                    if not isinstance(field, dict):
                        issues.append(f"Field {i} is not a dict")
                    else:
                        if 'name' not in field:
                            issues.append(f"Field {i} missing 'name'")
                        if 'confidence' not in field:
                            issues.append(f"Field {i} missing 'confidence'")
                        if 'value' in field and field['value'] is None and 'needs_review' not in field:
                            issues.append(f"Field {i} has null value but no needs_review flag")
        
        if issues:
            return CriticResult(
                passed=False,
                notes="; ".join(issues),
                required_fixes=[
                    "Ensure all fields have {name, value, confidence}",
                    "Set needs_review=true for fields with confidence < 0.80",
                ],
            )
        
        return CriticResult(
            passed=True,
            notes="OCR output has valid structure",
            required_fixes=[],
        )
    
    async def _verify_retrieve_sops(self, output: Dict[str, Any], criteria: str) -> CriticResult:
        """Verify retrieval output."""
        issues = []
        
        # Check required fields
        if 'chunks' not in output:
            issues.append("Missing 'chunks' in output")
        else:
            chunks = output['chunks']
            if not isinstance(chunks, list):
                issues.append("'chunks' must be a list")
            elif len(chunks) == 0:
                return CriticResult(
                    passed=False,
                    notes="No chunks retrieved (low confidence search)",
                    required_fixes=["Try different keywords or be more specific"],
                )
            else:
                # Check each chunk has citations
                for i, chunk in enumerate(chunks):
                    if not isinstance(chunk, dict):
                        issues.append(f"Chunk {i} is not a dict")
                    else:
                        if 'text' not in chunk:
                            issues.append(f"Chunk {i} missing 'text'")
                        if 'doc_id' not in chunk or 'page' not in chunk:
                            issues.append(f"Chunk {i} missing citation (doc_id, page)")
        
        if issues:
            return CriticResult(
                passed=False,
                notes="; ".join(issues),
                required_fixes=["Retrieve again with better search parameters"],
            )
        
        return CriticResult(
            passed=True,
            notes="Retrieval has valid structure and citations",
            required_fixes=[],
        )
    
    async def _verify_draft_note(self, output: Dict[str, Any], criteria: str) -> CriticResult:
        """Verify draft note output."""
        issues = []
        
        # Check required fields
        if 'note_text' not in output:
            issues.append("Missing 'note_text' in output")
        else:
            note_text = output['note_text']
            if not isinstance(note_text, str) or len(note_text.strip()) == 0:
                issues.append("note_text is empty")
        
        if 'citations' in output:
            if not isinstance(output['citations'], list):
                issues.append("'citations' must be a list")
        else:
            issues.append("Missing 'citations' (grounded generation required)")
        
        if issues:
            return CriticResult(
                passed=False,
                notes="; ".join(issues),
                required_fixes=[
                    "Generate a substantive draft with clear sections",
                    "Cite retrieved information (doc_id, page number)",
                ],
            )
        
        return CriticResult(
            passed=True,
            notes="Draft note has valid structure and citations",
            required_fixes=[],
        )
    
    async def _verify_run_python(self, output: Dict[str, Any], criteria: str) -> CriticResult:
        """Verify Python code execution output."""
        issues = []
        
        # Check required fields
        if 'exit_code' not in output:
            issues.append("Missing 'exit_code' in output")
        elif output['exit_code'] != 0:
            issues.append(f"Code failed with exit_code {output['exit_code']}")
            if 'stderr' in output and output['stderr']:
                issues.append(f"Error: {output['stderr'][:200]}")
        
        if 'stdout' not in output:
            issues.append("Missing 'stdout' in output")
        
        if issues:
            return CriticResult(
                passed=False,
                notes="; ".join(issues),
                required_fixes=["Debug the code execution; check stdin, file paths and imports"],
                retry_prompt_delta=f"Previous attempt had errors:\n{'; '.join(issues)}\nFix and retry.",
            )
        
        return CriticResult(
            passed=True,
            notes="Code executed successfully",
            required_fixes=[],
        )
    
    async def _verify_approve_note(self, output: Dict[str, Any], criteria: str) -> CriticResult:
        """Verify checkpoint approval."""
        # This is a human decision; critic just checks the format
        if 'approved' not in output or not isinstance(output['approved'], bool):
            return CriticResult(
                passed=False,
                notes="Missing or invalid 'approved' field",
                required_fixes=[],
            )
        
        return CriticResult(
            passed=True,
            notes="Approval checkpoint recorded",
            required_fixes=[],
        )
    
    async def _verify_export_docx(self, output: Dict[str, Any], criteria: str) -> CriticResult:
        """Verify .docx export."""
        issues = []
        
        # Check required fields
        if 'path' not in output:
            issues.append("Missing 'path' (file location)")
        if 'size_bytes' not in output:
            issues.append("Missing 'size_bytes'")
        if 'hash' not in output:
            issues.append("Missing 'hash' (SHA-256)")
        
        if issues:
            return CriticResult(
                passed=False,
                notes="; ".join(issues),
                required_fixes=["Regenerate the .docx file"],
            )
        
        return CriticResult(
            passed=True,
            notes=".docx file generated and verified",
            required_fixes=[],
        )


class RetryPolicy:
    """Manages retry logic with feedback."""
    
    @staticmethod
    def should_retry(
        node_type: str,
        attempts: int,
        max_retries: int,
        critic_result: Optional[CriticResult],
    ) -> Tuple[bool, Optional[str]]:
        """Decide whether to retry and suggest feedback.
        
        Args:
            critic_result: Verification result, or None if executor raised an exception
        
        Returns:
            (should_retry, feedback_message)
        """
        
        if critic_result is None:
            # Executor raised an exception; retry if attempts left
            return attempts < max_retries, None
        
        if not critic_result.passed and attempts < max_retries:
            feedback = critic_result.retry_prompt_delta or "\n".join(critic_result.required_fixes)
            return True, feedback
        
        return False, None
