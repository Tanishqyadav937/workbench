"""B12: Multimodal extraction pipeline (OCR + VLM)."""

from .extraction import (
    ExtractionService,
    ExtractionResult,
    Field,
    BoundingBox,
    OCRBackend,
    PaddleOCRBackend,
    VLMBackend,
    OllamaVLMBackend,
)

__all__ = [
    'ExtractionService',
    'ExtractionResult',
    'Field',
    'BoundingBox',
    'OCRBackend',
    'PaddleOCRBackend',
    'VLMBackend',
    'OllamaVLMBackend',
]
