"""Tests for B12-B13: Multimodal extraction (OCR + VLM + confidence gating)."""

import unittest
import asyncio
import json
from typing import List, Tuple
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

from apps.multimodal import (
    ExtractionService,
    ExtractionResult,
    Field,
    BoundingBox,
    OCRBackend,
    VLMBackend,
)
from phase2.extraction import flag_fields, ready_to_draft


class MockOCRBackend(OCRBackend):
    """Mock OCR for testing."""
    
    async def extract_text(self, image_path: str) -> Tuple[List[Field], float]:
        return [
            Field(
                name="line_0",
                value="Equipment: P-101A",
                confidence=0.95,
                source="ocr",
            ),
            Field(
                name="line_1",
                value="Thickness: 6.4 mm",
                confidence=0.88,
                source="ocr",
            ),
        ], 150.0


class MockVLMBackend(VLMBackend):
    """Mock VLM for testing."""
    
    async def extract_fields(
        self,
        image_path: str,
        prompt: str,
        context: None = None,
    ) -> Tuple[List[Field], float]:
        return [
            Field(
                name="equipment_tag",
                value="P-101A",
                confidence=0.97,
                source="vlm",
            ),
            Field(
                name="wall_thickness_mm",
                value="6.4",
                confidence=0.62,  # Below threshold
                source="vlm",
            ),
            Field(
                name="inspection_date",
                value=None,
                confidence=0.3,  # Very low confidence
                source="vlm",
            ),
        ], 280.0


class TestField(unittest.TestCase):
    """Test Field data structure."""
    
    def test_field_creation(self):
        """Test creating a field."""
        field = Field(
            name="equipment_tag",
            value="P-101A",
            confidence=0.97,
        )
        self.assertEqual(field.name, "equipment_tag")
        self.assertEqual(field.value, "P-101A")
        self.assertEqual(field.confidence, 0.97)
    
    def test_field_with_bbox(self):
        """Test field with bounding box."""
        bbox = BoundingBox(x1=0.1, y1=0.2, x2=0.5, y2=0.4)
        field = Field(
            name="tag",
            value="P-101",
            confidence=0.9,
            bbox=bbox,
        )
        self.assertIsNotNone(field.bbox)
        self.assertEqual(field.bbox.x1, 0.1)
    
    def test_field_serialization(self):
        """Test field to_dict()."""
        field = Field(
            name="tag",
            value="P-101",
            confidence=0.9,
            source="ocr",
        )
        d = field.to_dict()
        self.assertEqual(d['name'], "tag")
        self.assertEqual(d['value'], "P-101")
        self.assertEqual(d['confidence'], 0.9)
        self.assertEqual(d['source'], "ocr")


class TestExtractionResult(unittest.TestCase):
    """Test ExtractionResult."""
    
    def test_extraction_result_creation(self):
        """Test creating an extraction result."""
        result = ExtractionResult(
            doc_id="d-203",
            page=1,
            fields=[],
        )
        self.assertEqual(result.doc_id, "d-203")
        self.assertEqual(result.page, 1)
        self.assertEqual(len(result.fields), 0)
    
    def test_needs_review_count(self):
        """Test counting fields needing review."""
        fields = [
            Field(name="f1", value="v1", confidence=0.95),
            Field(name="f2", value="v2", confidence=0.65),
            Field(name="f3", value="v3", confidence=0.50),
        ]
        result = ExtractionResult(
            doc_id="d-203",
            page=1,
            fields=fields,
            confidence_threshold=0.80,
        )
        
        self.assertEqual(result.needs_review_count(), 2)
    
    def test_ready_for_export(self):
        """Test export readiness."""
        # All fields above threshold
        fields = [
            Field(name="f1", value="v1", confidence=0.95),
            Field(name="f2", value="v2", confidence=0.85),
        ]
        result = ExtractionResult(
            doc_id="d-203",
            page=1,
            fields=fields,
            confidence_threshold=0.80,
        )
        self.assertTrue(result.ready_for_export())
        
        # Some fields below threshold
        result.fields.append(Field(name="f3", value="v3", confidence=0.50))
        self.assertFalse(result.ready_for_export())
    
    def test_extraction_result_serialization(self):
        """Test result to_dict()."""
        result = ExtractionResult(
            doc_id="d-203",
            page=1,
            fields=[
                Field(name="tag", value="P-101", confidence=0.9),
            ],
            model_used="qwen2.5vl:7b",
        )
        d = result.to_dict()
        self.assertEqual(d['doc_id'], "d-203")
        self.assertEqual(d['page'], 1)
        self.assertEqual(len(d['fields']), 1)
        self.assertEqual(d['model_used'], "qwen2.5vl:7b")


class TestConfidenceGating(unittest.TestCase):
    """Test B13: Confidence gating logic."""
    
    def test_flag_fields_below_threshold(self):
        """Test flagging low-confidence fields."""
        fields = [
            {'name': 'tag', 'value': 'P-101', 'confidence': 0.95},
            {'name': 'thickness', 'value': '6.4', 'confidence': 0.65},
            {'name': 'date', 'value': None, 'confidence': 0.30},
        ]
        
        flagged = flag_fields(fields, threshold=0.80)
        
        self.assertEqual(len(flagged), 3)
        self.assertFalse(flagged[0]['needs_review'])  # Above threshold
        self.assertTrue(flagged[1]['needs_review'])   # Below threshold
        self.assertTrue(flagged[2]['needs_review'])   # Null value
    
    def test_flag_fields_missing_confidence(self):
        """Test flagging fields with missing confidence."""
        fields = [
            {'name': 'tag', 'value': 'P-101'},  # No confidence
            {'name': 'thickness', 'value': '6.4', 'confidence': None},
        ]
        
        flagged = flag_fields(fields, threshold=0.80)
        
        self.assertTrue(flagged[0]['needs_review'])
        self.assertTrue(flagged[1]['needs_review'])
    
    def test_ready_to_draft_gate(self):
        """Test drafting gate logic."""
        fields = [
            {'name': 'tag', 'value': 'P-101', 'confidence': 0.95, 'needs_review': False, 'confirmed': True},
            {'name': 'thickness', 'value': '6.4', 'confidence': 0.65, 'needs_review': True, 'confirmed': False},
        ]
        
        # Not ready because thickness is flagged but not confirmed
        self.assertFalse(ready_to_draft(fields))
        
        # Confirm the flagged field
        fields[1]['confirmed'] = True
        self.assertTrue(ready_to_draft(fields))
    
    def test_ready_to_draft_gate_no_flagged_fields(self):
        """Test drafting gate with no flagged fields."""
        fields = [
            {'name': 'tag', 'value': 'P-101', 'confidence': 0.95, 'needs_review': False},
            {'name': 'thickness', 'value': '6.4', 'confidence': 0.85, 'needs_review': False},
        ]
        
        self.assertTrue(ready_to_draft(fields))


class TestExtractionService(unittest.TestCase):
    """Test extraction service."""
    
    def setUp(self):
        self.service = ExtractionService(
            ocr_backend=MockOCRBackend(),
            vlm_backend=MockVLMBackend(),
        )
    
    def test_extraction_service_init(self):
        """Test service initialization."""
        self.assertIsNotNone(self.service.ocr)
        self.assertIsNotNone(self.service.vlm)
    
    def test_extract_document_ocr_only(self):
        """Test extraction with OCR only."""
        service = ExtractionService(ocr_backend=MockOCRBackend(), vlm_backend=None)
        
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(
            service.extract_document(
                image_path="dummy.jpg",
                doc_id="d-203",
                use_ocr=True,
                use_vlm=False,
            )
        )
        
        self.assertEqual(result.doc_id, "d-203")
        self.assertEqual(len(result.fields), 2)  # From MockOCRBackend
        self.assertGreater(result.processing_time_ms, 0)
    
    def test_extract_document_vlm_only(self):
        """Test extraction with VLM only."""
        service = ExtractionService(ocr_backend=None, vlm_backend=MockVLMBackend())
        
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(
            service.extract_document(
                image_path="dummy.jpg",
                doc_id="d-203",
                use_ocr=False,
                use_vlm=True,
            )
        )
        
        self.assertEqual(result.doc_id, "d-203")
        self.assertEqual(len(result.fields), 3)  # From MockVLMBackend
        self.assertEqual(result.model_used, "unknown")  # Mock has no model_id
    
    def test_extract_and_gate(self):
        """Test extraction with gating."""
        loop = asyncio.get_event_loop()
        result, flagged = loop.run_until_complete(
            self.service.extract_and_gate(
                image_path="dummy.jpg",
                doc_id="d-203",
                confidence_threshold=0.80,
            )
        )
        
        # MockVLMBackend returns 3 fields; 2 below 0.80 threshold
        self.assertEqual(result.needs_review_count(), 2)
        self.assertEqual(len(flagged), 2)
        
        # Flagged fields should have names
        flag_names = {f.name for f in flagged}
        self.assertIn("wall_thickness_mm", flag_names)
        self.assertIn("inspection_date", flag_names)
    
    def test_extract_combined_ocr_vlm(self):
        """Test extraction with both OCR and VLM."""
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(
            self.service.extract_document(
                image_path="dummy.jpg",
                doc_id="d-203",
                use_ocr=True,
                use_vlm=True,
            )
        )
        
        # Should have fields from both backends
        self.assertGreater(len(result.fields), 2)
        
        # Check mix of sources
        sources = {f.source for f in result.fields}
        self.assertIn("ocr", sources)
        self.assertIn("vlm", sources)


class TestExtractionPipeline(unittest.TestCase):
    """Integration test for full extraction pipeline."""
    
    def test_full_pipeline_with_gating(self):
        """Test full pipeline: extract -> flag -> gate."""
        service = ExtractionService(
            ocr_backend=MockOCRBackend(),
            vlm_backend=MockVLMBackend(),
        )
        
        loop = asyncio.get_event_loop()
        result, flagged = loop.run_until_complete(
            service.extract_and_gate(
                image_path="dummy.jpg",
                doc_id="d-203",
                confidence_threshold=0.80,
            )
        )
        
        # Check result structure
        self.assertEqual(result.doc_id, "d-203")
        self.assertGreater(len(result.fields), 0)
        
        # Check flagged fields
        self.assertGreater(len(flagged), 0)
        for field in flagged:
            self.assertLess(field.confidence, 0.80)
        
        # Check export readiness
        ready = result.ready_for_export()
        self.assertFalse(ready)  # Should have flagged fields


if __name__ == '__main__':
    unittest.main()
