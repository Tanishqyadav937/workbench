"""C7: PII redaction — NER + regex patterns (Aadhaar, PAN, phone, email)."""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Pattern
from enum import Enum
import logging


logger = logging.getLogger(__name__)


class PIIType(str, Enum):
    """Types of personal identifiable information."""
    PERSON_NAME = "person_name"
    EMAIL = "email"
    PHONE = "phone"
    AADHAAR = "aadhaar"  # 12-digit Indian ID
    PAN = "pan"  # Indian tax ID (10 chars)
    PASSPORT = "passport"
    SSN = "ssn"
    CREDIT_CARD = "credit_card"
    IP_ADDRESS = "ip_address"


@dataclass
class RedactionSpan:
    """A span of text identified as PII."""
    start: int
    end: int
    pii_type: PIIType
    original_value: str
    placeholder: str


@dataclass
class RedactionReport:
    """Report of all redactions performed."""
    original_text_length: int
    redacted_text_length: int
    spans: List[RedactionSpan] = field(default_factory=list)
    total_redactions: int = 0
    
    def pii_counts(self) -> Dict[str, int]:
        """Count by PII type."""
        counts = {}
        for span in self.spans:
            counts[span.pii_type.value] = counts.get(span.pii_type.value, 0) + 1
        return counts
    
    def to_dict(self) -> Dict:
        return {
            'original_text_length': self.original_text_length,
            'redacted_text_length': self.redacted_text_length,
            'total_redactions': self.total_redactions,
            'pii_counts': self.pii_counts(),
            'spans': [
                {
                    'start': s.start,
                    'end': s.end,
                    'pii_type': s.pii_type.value,
                    'placeholder': s.placeholder,
                }
                for s in self.spans
            ],
        }


class PIIRedactor:
    """C7: Redacts PII using regex and NER patterns."""
    
    def __init__(self, use_ner: bool = False):
        """
        Args:
            use_ner: If True, use spaCy NER (requires spacy[en] installed).
                     If False, use regex patterns only.
        """
        self.use_ner = use_ner
        self.nlp = None
        
        if use_ner:
            try:
                import spacy
                self.nlp = spacy.load("en_core_web_sm")
            except Exception as e:
                logger.warning(f"spaCy NER not available: {e}. Using regex only.")
                self.use_ner = False
        
        # Compile regex patterns
        self.patterns: Dict[PIIType, Pattern] = {
            # Email: simple pattern
            PIIType.EMAIL: re.compile(
                r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
            ),
            # Phone: various formats (10 digits, with spaces/dashes)
            PIIType.PHONE: re.compile(
                r'\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'
            ),
            # Aadhaar: 12 digits, often in format XXXX XXXX XXXX
            PIIType.AADHAAR: re.compile(
                r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b'
            ),
            # PAN: format AAAAA9999A (5 letters, 4 digits, 1 letter)
            PIIType.PAN: re.compile(
                r'\b[A-Z]{5}\d{4}[A-Z]{1}\b'
            ),
            # Credit card: 16 digits in groups
            PIIType.CREDIT_CARD: re.compile(
                r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b'
            ),
            # IPv4 address
            PIIType.IP_ADDRESS: re.compile(
                r'\b(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b'
            ),
        }
    
    async def redact(
        self,
        text: str,
        pii_types: Optional[List[PIIType]] = None,
    ) -> Tuple[str, RedactionReport]:
        """
        Redact PII from text.
        
        Args:
            text: Input text
            pii_types: List of PII types to redact. If None, redact all.
        
        Returns:
            (redacted_text, report)
        """
        if not text:
            return "", RedactionReport(original_text_length=0, redacted_text_length=0)
        
        if pii_types is None:
            pii_types = list(PIIType)
        
        # Collect spans
        spans: List[RedactionSpan] = []
        
        # Regex-based detection
        for pii_type in pii_types:
            if pii_type not in self.patterns:
                continue
            
            pattern = self.patterns[pii_type]
            for match in pattern.finditer(text):
                original = match.group(0)
                placeholder = self._get_placeholder(pii_type, len(spans))
                
                span = RedactionSpan(
                    start=match.start(),
                    end=match.end(),
                    pii_type=pii_type,
                    original_value=original,
                    placeholder=placeholder,
                )
                spans.append(span)
        
        # NER-based detection (PERSON, ORG, etc.)
        if self.use_ner and self.nlp and PIIType.PERSON_NAME in pii_types:
            try:
                doc = self.nlp(text)
                for ent in doc.ents:
                    if ent.label_ == "PERSON":
                        placeholder = self._get_placeholder(PIIType.PERSON_NAME, len(spans))
                        span = RedactionSpan(
                            start=ent.start_char,
                            end=ent.end_char,
                            pii_type=PIIType.PERSON_NAME,
                            original_value=ent.text,
                            placeholder=placeholder,
                        )
                        spans.append(span)
            except Exception as e:
                logger.error(f"NER detection failed: {e}")
        
        # Sort spans by position (reverse to avoid offset issues)
        spans.sort(key=lambda s: s.start, reverse=True)
        
        # Apply redactions
        redacted = text
        for span in spans:
            redacted = redacted[:span.start] + span.placeholder + redacted[span.end:]
        
        report = RedactionReport(
            original_text_length=len(text),
            redacted_text_length=len(redacted),
            spans=spans[::-1],  # Reverse to maintain order
            total_redactions=len(spans),
        )
        
        return redacted, report
    
    @staticmethod
    def _get_placeholder(pii_type: PIIType, index: int) -> str:
        """Generate a placeholder for redacted PII."""
        type_abbr = pii_type.value.upper()[:3]
        return f"[{type_abbr}_{index}]"
