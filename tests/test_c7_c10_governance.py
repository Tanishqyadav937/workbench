"""Tests for C7-C10: Governance (PII redaction, classification, consent)."""

import unittest
import asyncio
from typing import List

from apps.governance import (
    PIIRedactor,
    PIIType,
    RedactionReport,
    ClassificationLevel,
    ClassificationEngine,
    ClassificationPolicy,
    ConsentRegistry,
    ConsentStatus,
    Purpose,
)


class TestPIIType(unittest.TestCase):
    """Test PII type enum."""
    
    def test_pii_type_values(self):
        """Test PII type values."""
        self.assertEqual(PIIType.PERSON_NAME.value, "person_name")
        self.assertEqual(PIIType.EMAIL.value, "email")
        self.assertEqual(PIIType.AADHAAR.value, "aadhaar")


class TestPIIRedactor(unittest.TestCase):
    """Test PII redaction."""
    
    def setUp(self):
        self.redactor = PIIRedactor(use_ner=False)  # Regex only for testing
    
    def test_redact_email(self):
        """Test email detection and redaction."""
        text = "Contact me at john@example.com for details."
        
        loop = asyncio.get_event_loop()
        redacted, report = loop.run_until_complete(
            self.redactor.redact(text, pii_types=[PIIType.EMAIL])
        )
        
        self.assertNotIn("john@example.com", redacted)
        self.assertIn("[EMA_", redacted)  # Placeholder
        self.assertEqual(report.total_redactions, 1)
    
    def test_redact_phone(self):
        """Test phone number detection."""
        text = "Call me at (555) 123-4567 or +1-555-987-6543."
        
        loop = asyncio.get_event_loop()
        redacted, report = loop.run_until_complete(
            self.redactor.redact(text, pii_types=[PIIType.PHONE])
        )
        
        self.assertEqual(report.total_redactions, 2)
        self.assertIn("[PHO_", redacted)
    
    def test_redact_aadhaar(self):
        """Test Aadhaar number detection (Indian ID)."""
        text = "My Aadhaar is 1234 5678 9012 and PAN is ABCDE1234F."
        
        loop = asyncio.get_event_loop()
        redacted, report = loop.run_until_complete(
            self.redactor.redact(text, pii_types=[PIIType.AADHAAR])
        )
        
        self.assertEqual(report.total_redactions, 1)
        self.assertNotIn("1234 5678 9012", redacted)
    
    def test_redact_pan(self):
        """Test PAN detection."""
        text = "Tax ID: ABCDE1234F"
        
        loop = asyncio.get_event_loop()
        redacted, report = loop.run_until_complete(
            self.redactor.redact(text, pii_types=[PIIType.PAN])
        )
        
        self.assertEqual(report.total_redactions, 1)
        self.assertNotIn("ABCDE1234F", redacted)
    
    def test_redact_multiple_types(self):
        """Test redacting multiple PII types."""
        text = "Contact john@example.com or (555) 123-4567. Aadhaar: 1234 5678 9012"
        
        loop = asyncio.get_event_loop()
        redacted, report = loop.run_until_complete(
            self.redactor.redact(text)
        )
        
        self.assertEqual(report.total_redactions, 3)
        self.assertNotIn("john@example.com", redacted)
        self.assertNotIn("(555) 123-4567", redacted)
        self.assertNotIn("1234 5678 9012", redacted)
    
    def test_redaction_report(self):
        """Test redaction report structure."""
        text = "Email: test@example.com"
        
        loop = asyncio.get_event_loop()
        redacted, report = loop.run_until_complete(
            self.redactor.redact(text, pii_types=[PIIType.EMAIL])
        )
        
        self.assertGreater(report.original_text_length, 0)
        self.assertGreater(report.redacted_text_length, 0)
        self.assertEqual(report.total_redactions, 1)
        
        counts = report.pii_counts()
        self.assertEqual(counts['email'], 1)


class TestClassificationLevel(unittest.TestCase):
    """Test classification level hierarchy."""
    
    def test_classification_ordering(self):
        """Test classification level ordering."""
        self.assertTrue(ClassificationLevel.PUBLIC < ClassificationLevel.INTERNAL)
        self.assertTrue(ClassificationLevel.INTERNAL < ClassificationLevel.CONFIDENTIAL)
        self.assertTrue(ClassificationLevel.CONFIDENTIAL < ClassificationLevel.BOARD_ONLY)
    
    def test_classification_equality(self):
        """Test classification equality."""
        self.assertEqual(ClassificationLevel.PUBLIC, ClassificationLevel.PUBLIC)
        self.assertFalse(ClassificationLevel.PUBLIC == ClassificationLevel.INTERNAL)
    
    def test_highest_classification(self):
        """Test finding highest classification."""
        levels = [
            ClassificationLevel.INTERNAL,
            ClassificationLevel.PUBLIC,
            ClassificationLevel.CONFIDENTIAL,
        ]
        highest = ClassificationLevel.highest(levels)
        self.assertEqual(highest, ClassificationLevel.CONFIDENTIAL)
    
    def test_can_read_permission(self):
        """Test read permission check."""
        # User with CONFIDENTIAL clearance can read INTERNAL and below
        self.assertTrue(ClassificationLevel.INTERNAL.can_read(ClassificationLevel.CONFIDENTIAL))
        self.assertTrue(ClassificationLevel.PUBLIC.can_read(ClassificationLevel.CONFIDENTIAL))
        
        # But cannot read BOARD_ONLY
        self.assertFalse(ClassificationLevel.BOARD_ONLY.can_read(ClassificationLevel.CONFIDENTIAL))
        
        # User with BOARD_ONLY clearance can read everything
        self.assertTrue(ClassificationLevel.CONFIDENTIAL.can_read(ClassificationLevel.BOARD_ONLY))
        self.assertTrue(ClassificationLevel.BOARD_ONLY.can_read(ClassificationLevel.BOARD_ONLY))


class TestClassificationEngine(unittest.TestCase):
    """Test automatic classification."""
    
    def test_classify_by_keywords_board_only(self):
        """Test detecting board-only content."""
        text = "Board approved M&A strategy for acquisition target."
        
        result = ClassificationEngine.classify_by_keywords(text)
        
        self.assertEqual(result.classification, ClassificationLevel.BOARD_ONLY)
        self.assertGreater(result.confidence, 0)
        self.assertIn("board", result.detected_indicators)
    
    def test_classify_by_keywords_confidential(self):
        """Test detecting confidential content."""
        text = "This is proprietary information about our trade secrets."
        
        result = ClassificationEngine.classify_by_keywords(text)
        
        self.assertEqual(result.classification, ClassificationLevel.CONFIDENTIAL)
        self.assertIn("proprietary", result.detected_indicators)
    
    def test_classify_by_keywords_default(self):
        """Test default classification."""
        text = "This is regular information about processes."
        
        result = ClassificationEngine.classify_by_keywords(text)
        
        self.assertEqual(result.classification, ClassificationLevel.INTERNAL)
    
    def test_inherit_classification(self):
        """Test classification inheritance from inputs."""
        inputs = [
            {'classification': 'internal'},
            {'classification': 'confidential'},
            {'classification': 'public'},
        ]
        
        highest = ClassificationEngine.inherit_classification(inputs)
        
        self.assertEqual(highest, ClassificationLevel.CONFIDENTIAL)
    
    def test_inherit_classification_empty(self):
        """Test inheritance with empty inputs."""
        highest = ClassificationEngine.inherit_classification([])
        self.assertEqual(highest, ClassificationLevel.INTERNAL)


class TestClassificationPolicy(unittest.TestCase):
    """Test classification access policy."""
    
    def test_can_retrieve_same_level(self):
        """Test retrieving data at same clearance level."""
        result = ClassificationPolicy.can_retrieve(
            ClassificationLevel.CONFIDENTIAL,
            ClassificationLevel.CONFIDENTIAL,
        )
        self.assertTrue(result)
    
    def test_can_retrieve_lower_classification(self):
        """Test retrieving lower-classified data."""
        result = ClassificationPolicy.can_retrieve(
            ClassificationLevel.INTERNAL,
            ClassificationLevel.CONFIDENTIAL,
        )
        self.assertTrue(result)
    
    def test_cannot_retrieve_higher_classification(self):
        """Test cannot retrieve higher-classified data."""
        result = ClassificationPolicy.can_retrieve(
            ClassificationLevel.BOARD_ONLY,
            ClassificationLevel.CONFIDENTIAL,
        )
        self.assertFalse(result)
    
    def test_filter_chunks(self):
        """Test filtering chunks by classification."""
        chunks = [
            {'text': 'public', 'classification': 'public'},
            {'text': 'internal', 'classification': 'internal'},
            {'text': 'confidential', 'classification': 'confidential'},
            {'text': 'board', 'classification': 'board_only'},
        ]
        
        filtered = ClassificationPolicy.filter_chunks(
            chunks,
            ClassificationLevel.CONFIDENTIAL,
        )
        
        self.assertEqual(len(filtered), 3)  # public, internal, confidential
        texts = {c['text'] for c in filtered}
        self.assertNotIn('board', texts)


class TestConsentRegistry(unittest.TestCase):
    """Test consent management."""
    
    def setUp(self):
        self.registry = ConsentRegistry()
    
    def test_grant_consent(self):
        """Test granting consent."""
        loop = asyncio.get_event_loop()
        record = loop.run_until_complete(
            self.registry.grant_consent(
                doc_id="d-203",
                granted_by="user-42",
                purposes=["inspection_review", "audit"],
            )
        )
        
        self.assertEqual(record.doc_id, "d-203")
        self.assertEqual(record.status, ConsentStatus.ACTIVE)
        self.assertIn("inspection_review", record.purposes)
    
    def test_check_consent_allowed(self):
        """Test checking allowed purpose."""
        loop = asyncio.get_event_loop()
        
        loop.run_until_complete(
            self.registry.grant_consent(
                doc_id="d-203",
                granted_by="user-42",
                purposes=["inspection_review"],
            )
        )
        
        allowed = loop.run_until_complete(
            self.registry.check_consent("d-203", "inspection_review")
        )
        self.assertTrue(allowed)
    
    def test_check_consent_denied(self):
        """Test checking denied purpose."""
        loop = asyncio.get_event_loop()
        
        loop.run_until_complete(
            self.registry.grant_consent(
                doc_id="d-203",
                granted_by="user-42",
                purposes=["inspection_review"],
            )
        )
        
        allowed = loop.run_until_complete(
            self.registry.check_consent("d-203", "training")
        )
        self.assertFalse(allowed)
    
    def test_revoke_consent(self):
        """Test revoking consent."""
        loop = asyncio.get_event_loop()
        
        loop.run_until_complete(
            self.registry.grant_consent(
                doc_id="d-203",
                granted_by="user-42",
                purposes=["inspection_review", "audit"],
            )
        )
        
        count = loop.run_until_complete(
            self.registry.revoke_consent("d-203", purpose="inspection_review")
        )
        
        self.assertEqual(count, 1)
        
        # Should still be allowed for audit
        allowed = loop.run_until_complete(
            self.registry.check_consent("d-203", "audit")
        )
        self.assertTrue(allowed)
    
    def test_log_usage(self):
        """Test logging data usage."""
        loop = asyncio.get_event_loop()
        
        record = loop.run_until_complete(
            self.registry.log_usage(
                doc_id="d-203",
                session_id="s-1",
                user_id="u-42",
                purpose="inspection_review",
                chunks_accessed=5,
            )
        )
        
        self.assertEqual(record.doc_id, "d-203")
        self.assertEqual(record.session_id, "s-1")
        self.assertEqual(record.chunks_accessed, 5)
    
    def test_get_usage_history(self):
        """Test retrieving usage history."""
        loop = asyncio.get_event_loop()
        
        for i in range(3):
            loop.run_until_complete(
                self.registry.log_usage(
                    doc_id="d-203",
                    session_id=f"s-{i}",
                    user_id="u-42",
                    purpose="inspection_review",
                )
            )
        
        history = loop.run_until_complete(
            self.registry.get_usage_history("d-203")
        )
        
        self.assertEqual(len(history), 3)
    
    def test_get_sessions_accessing_doc(self):
        """Test finding sessions that accessed a document."""
        loop = asyncio.get_event_loop()
        
        for i in range(3):
            loop.run_until_complete(
                self.registry.log_usage(
                    doc_id="d-203",
                    session_id=f"s-{i}",
                    user_id="u-42",
                    purpose="inspection_review",
                )
            )
        
        sessions = loop.run_until_complete(
            self.registry.get_sessions_accessing_doc("d-203")
        )
        
        self.assertEqual(len(sessions), 3)
        self.assertIn("s-0", sessions)


if __name__ == '__main__':
    unittest.main()
