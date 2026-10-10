"""B12: Multimodal extraction — OCR, VLM, confidence gating."""

import json
import logging
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Any, Optional, Tuple
from abc import ABC, abstractmethod
import base64

from apps.governance.injection_scanner import scan as scan_injection

try:
    from paddleocr import PaddleOCR
except ImportError:
    PaddleOCR = None


logger = logging.getLogger(__name__)


@dataclass
class BoundingBox:
    """Bounding box in normalized [0..1] coordinates."""
    x1: float
    y1: float
    x2: float
    y2: float
    
    def to_dict(self) -> Dict[str, float]:
        return asdict(self)


@dataclass
class Field:
    """Extracted field with confidence and location."""
    name: str
    value: Optional[str]
    confidence: float
    bbox: Optional[BoundingBox] = None
    source: Optional[str] = None  # "ocr" | "vlm" | "layout"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'value': self.value,
            'confidence': self.confidence,
            'bbox': self.bbox.to_dict() if self.bbox else None,
            'source': self.source,
        }


@dataclass
class ExtractionResult:
    """Result of document extraction."""
    doc_id: str
    page: int
    fields: List[Field]
    confidence_threshold: float = 0.80
    model_used: Optional[str] = None
    processing_time_ms: Optional[float] = None
    quarantined: bool = False
    injection_findings: List[Dict[str, str]] = field(default_factory=list)
    quarantine_cleared_by: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'doc_id': self.doc_id,
            'page': self.page,
            'fields': [f.to_dict() for f in self.fields],
            'confidence_threshold': self.confidence_threshold,
            'model_used': self.model_used,
            'processing_time_ms': self.processing_time_ms,
            'quarantined': self.quarantined,
            'injection_findings': self.injection_findings,
            'quarantine_cleared_by': self.quarantine_cleared_by,
        }
    
    def needs_review_count(self) -> int:
        """Count fields below confidence threshold."""
        return sum(1 for f in self.fields if f.confidence < self.confidence_threshold)
    
    def ready_for_export(self) -> bool:
        """Check if all flagged fields have been confirmed."""
        # This would be checked after human review
        return self.needs_review_count() == 0 and not self.is_blocked()

    def scan_for_injection(self) -> bool:
        """Scan extracted text for instruction-like content; quarantine on 2+ rules."""
        values = [f.value for f in self.fields if f.value]
        found = {}
        for sep in ("\n", " "):
            for finding in scan_injection(sep.join(values)):
                found.setdefault(finding.rule, finding.excerpt)
        self.injection_findings = [{"rule": r, "excerpt": e} for r, e in found.items()]
        self.quarantined = len(self.injection_findings) >= 2
        self.quarantine_cleared_by = None
        return self.quarantined

    def is_blocked(self) -> bool:
        return self.quarantined and not self.quarantine_cleared_by

    def clear_quarantine(self, by: str) -> None:
        """A named human confirms the flagged document is safe to use."""
        if not by:
            raise ValueError("clearing a quarantine requires a reviewer id")
        if self.quarantined:
            self.quarantine_cleared_by = by


class OCRBackend(ABC):
    """Abstract interface for OCR."""
    
    @abstractmethod
    async def extract_text(self, image_path: str) -> Tuple[List[Field], float]:
        """Extract text from image. Returns (fields, processing_time_ms)."""
        pass


class PaddleOCRBackend(OCRBackend):
    """PaddleOCR-based OCR backend (local, zero-egress)."""
    
    def __init__(self, lang: str = "en"):
        if not PaddleOCR:
            raise ImportError("paddleocr not installed; pip install paddleocr")
        
        self.ocr = PaddleOCR(use_angle_cls=True, lang=lang)
        self.lang = lang
    
    async def extract_text(self, image_path: str) -> Tuple[List[Field], float]:
        """Extract text from image using PaddleOCR."""
        import time
        
        start = time.time()
        
        try:
            result = self.ocr.ocr(image_path, cls=True)
        except Exception as e:
            logger.error(f"PaddleOCR extraction failed: {e}")
            return [], 0.0
        
        elapsed_ms = (time.time() - start) * 1000
        
        fields = []
        if result and len(result) > 0:
            for line_idx, line in enumerate(result[0]):
                bbox_coords, text_with_conf = line
                text, conf = text_with_conf
                
                # Normalize bbox to [0..1]
                xs = [p[0] for p in bbox_coords]
                ys = [p[1] for p in bbox_coords]
                bbox = BoundingBox(
                    x1=min(xs) / 1920,  # Assume 1920x1440; normalize
                    y1=min(ys) / 1440,
                    x2=max(xs) / 1920,
                    y2=max(ys) / 1440,
                )
                
                field = Field(
                    name=f"line_{line_idx}",
                    value=text.strip(),
                    confidence=float(conf),
                    bbox=bbox,
                    source="ocr",
                )
                fields.append(field)
        
        return fields, elapsed_ms


class VLMBackend(ABC):
    """Abstract interface for Vision-Language Models."""
    
    @abstractmethod
    async def extract_fields(
        self,
        image_path: str,
        prompt: str,
        context: Optional[str] = None,
    ) -> Tuple[List[Field], float]:
        """Extract structured fields using VLM. Returns (fields, processing_time_ms)."""
        pass


class OllamaVLMBackend(VLMBackend):
    """Ollama-based VLM backend (local, via model-gateway)."""
    
    def __init__(self, model_id: str = "qwen2.5vl:7b", gateway_url: str = "http://localhost:11434"):
        self.model_id = model_id
        self.gateway_url = gateway_url
    
    async def extract_fields(
        self,
        image_path: str,
        prompt: str,
        context: Optional[str] = None,
    ) -> Tuple[List[Field], float]:
        """Extract fields using Ollama VLM."""
        import time
        import httpx
        
        start = time.time()
        
        # Read image
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode('utf-8')
        
        # Call Ollama
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.gateway_url}/api/generate",
                    json={
                        "model": self.model_id,
                        "prompt": prompt,
                        "images": [image_data],
                        "stream": False,
                    },
                    timeout=60.0,
                )
            
            if response.status_code != 200:
                logger.error(f"VLM request failed: {response.status_code}")
                return [], 0.0
            
            result = response.json()
            text = result.get("response", "")
        
        except Exception as e:
            logger.error(f"VLM extraction failed: {e}")
            return [], 0.0
        
        elapsed_ms = (time.time() - start) * 1000
        
        # Parse JSON response (assume VLM returns JSON)
        try:
            parsed = json.loads(text)
            fields = []
            
            if 'fields' in parsed:
                for field_dict in parsed['fields']:
                    field = Field(
                        name=field_dict.get('name', ''),
                        value=field_dict.get('value'),
                        confidence=float(field_dict.get('confidence', 0.5)),
                        source="vlm",
                    )
                    fields.append(field)
            
            return fields, elapsed_ms
        
        except json.JSONDecodeError:
            logger.error(f"VLM response not JSON: {text[:200]}")
            return [], elapsed_ms


class ExtractionService:
    """B12: Main extraction service combining OCR + VLM."""
    
    def __init__(self, ocr_backend: Optional[OCRBackend] = None, vlm_backend: Optional[VLMBackend] = None):
        self.ocr = ocr_backend or (PaddleOCRBackend() if PaddleOCR else None)
        self.vlm = vlm_backend
    
    async def extract_document(
        self,
        image_path: str,
        doc_id: str,
        page: int = 1,
        use_ocr: bool = True,
        use_vlm: bool = True,
        confidence_threshold: float = 0.80,
    ) -> ExtractionResult:
        """Extract fields from a document image using OCR and/or VLM."""
        
        import time
        start = time.time()
        
        result = ExtractionResult(
            doc_id=doc_id,
            page=page,
            fields=[],
            confidence_threshold=confidence_threshold,
        )
        
        # OCR pass
        if use_ocr and self.ocr:
            try:
                ocr_fields, ocr_time = await self.ocr.extract_text(image_path)
                result.fields.extend(ocr_fields)
                logger.info(f"OCR extracted {len(ocr_fields)} fields in {ocr_time:.0f}ms")
            except Exception as e:
                logger.error(f"OCR failed: {e}")
        
        # VLM pass (higher-level understanding)
        if use_vlm and self.vlm:
            try:
                vlm_prompt = """Extract structured fields from this document. Return JSON with:
{
  "fields": [
    {"name": "field_name", "value": "extracted_value", "confidence": 0.95},
    ...
  ]
}

Be specific and extract exact values. Set confidence to 1.0 if certain, 0.5 if uncertain, 0.2 if very uncertain."""
                
                vlm_fields, vlm_time = await self.vlm.extract_fields(image_path, vlm_prompt)
                result.fields.extend(vlm_fields)
                result.model_used = self.vlm.model_id if hasattr(self.vlm, 'model_id') else 'unknown'
                logger.info(f"VLM extracted {len(vlm_fields)} fields in {vlm_time:.0f}ms")
            except Exception as e:
                logger.error(f"VLM extraction failed: {e}")
        
        if result.scan_for_injection():
            logger.warning(f"Document {doc_id} quarantined: "
                           f"{[f['rule'] for f in result.injection_findings]}")

        result.processing_time_ms = (time.time() - start) * 1000
        
        return result
    
    async def extract_and_gate(
        self,
        image_path: str,
        doc_id: str,
        page: int = 1,
        confidence_threshold: float = 0.80,
    ) -> Tuple[ExtractionResult, List[Field]]:
        """Extract and return flagged fields separately."""
        
        result = await self.extract_document(
            image_path,
            doc_id,
            page,
            confidence_threshold=confidence_threshold,
        )
        
        # Separate flagged fields
        flagged = [f for f in result.fields if f.confidence < confidence_threshold]
        
        return result, flagged
