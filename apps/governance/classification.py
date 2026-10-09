"""C9: Classification levels and inheritance."""

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Dict, Any


class ClassificationLevel(str, Enum):
    """Document and data classification levels (FR-7.6)."""
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    BOARD_ONLY = "board_only"
    
    def __lt__(self, other: 'ClassificationLevel') -> bool:
        """Compare levels: public < internal < confidential < board_only."""
        order = [
            ClassificationLevel.PUBLIC,
            ClassificationLevel.INTERNAL,
            ClassificationLevel.CONFIDENTIAL,
            ClassificationLevel.BOARD_ONLY,
        ]
        return order.index(self) < order.index(other)
    
    def __le__(self, other: 'ClassificationLevel') -> bool:
        return self < other or self == other
    
    def __gt__(self, other: 'ClassificationLevel') -> bool:
        """Greater than."""
        return not self <= other
    
    def __ge__(self, other: 'ClassificationLevel') -> bool:
        """Greater or equal."""
        return self > other or self == other
    
    @staticmethod
    def highest(levels: List['ClassificationLevel']) -> 'ClassificationLevel':
        """Return the highest classification level (most restrictive)."""
        if not levels:
            return ClassificationLevel.PUBLIC
        
        return max(levels, key=lambda x: [
            ClassificationLevel.PUBLIC,
            ClassificationLevel.INTERNAL,
            ClassificationLevel.CONFIDENTIAL,
            ClassificationLevel.BOARD_ONLY,
        ].index(x))
    
    def can_read(self, user_clearance: 'ClassificationLevel') -> bool:
        """Check if a user with given clearance can read this data.
        
        User can read data at or below their clearance level.
        Example: a user with CONFIDENTIAL clearance can read INTERNAL, CONFIDENTIAL, but not BOARD_ONLY.
        """
        return self <= user_clearance


@dataclass
class ClassifyRequest:
    """Request to classify content."""
    content: str
    doc_id: Optional[str] = None
    department: Optional[str] = None
    owner_id: Optional[str] = None


@dataclass
class ClassifyResponse:
    """Classification result."""
    classification: ClassificationLevel
    confidence: float
    reasoning: str
    detected_indicators: List[str]  # PII, sensitive keywords, etc.


class ClassificationEngine:
    """C9: Automatic and explicit classification."""
    
    # Keywords indicating higher classification
    SENSITIVE_KEYWORDS = {
        ClassificationLevel.CONFIDENTIAL: [
            "confidential", "proprietary", "internal use only",
            "trade secret", "strategic", "board decision",
            "merger", "acquisition", "layoff", "restructuring",
        ],
        ClassificationLevel.BOARD_ONLY: [
            "board", "cfo", "ceo", "executive", "merger", "ipo",
            "acquisition", "investment decision", "shareholder",
        ],
    }
    
    @staticmethod
    def classify_by_keywords(content: str) -> ClassifyResponse:
        """Classify by detecting sensitive keywords."""
        indicators = []
        level = ClassificationLevel.INTERNAL  # Default
        
        content_lower = content.lower()
        
        # Check for board-only keywords first
        for keyword in ClassificationEngine.SENSITIVE_KEYWORDS[ClassificationLevel.BOARD_ONLY]:
            if keyword in content_lower:
                indicators.append(keyword)
                level = ClassificationLevel.BOARD_ONLY
                break
        
        # Check for confidential keywords
        if level != ClassificationLevel.BOARD_ONLY:
            for keyword in ClassificationEngine.SENSITIVE_KEYWORDS[ClassificationLevel.CONFIDENTIAL]:
                if keyword in content_lower:
                    indicators.append(keyword)
                    level = ClassificationLevel.CONFIDENTIAL
        
        return ClassifyResponse(
            classification=level,
            confidence=0.7 if indicators else 0.5,
            reasoning=f"Detected keywords: {', '.join(indicators)}" if indicators else "No sensitive keywords detected; default to internal",
            detected_indicators=indicators,
        )
    
    @staticmethod
    def inherit_classification(inputs: List[Dict[str, Any]]) -> ClassificationLevel:
        """Inherit classification from input documents (highest).
        
        Args:
            inputs: List of input documents/chunks with 'classification' field
        
        Returns:
            Highest classification level found
        """
        levels = []
        for inp in inputs:
            if 'classification' in inp:
                try:
                    level = ClassificationLevel(inp['classification'])
                    levels.append(level)
                except ValueError:
                    pass
        
        return ClassificationLevel.highest(levels) if levels else ClassificationLevel.INTERNAL


class ClassificationPolicy:
    """Policy enforcement for classification."""
    
    @staticmethod
    def can_retrieve(
        chunk_classification: ClassificationLevel,
        user_clearance: ClassificationLevel,
    ) -> bool:
        """Check if a user can retrieve a chunk based on classification."""
        return chunk_classification.can_read(user_clearance)
    
    @staticmethod
    def filter_chunks(
        chunks: List[Dict[str, Any]],
        user_clearance: ClassificationLevel,
    ) -> List[Dict[str, Any]]:
        """Filter chunks to only those the user can read."""
        allowed = []
        for chunk in chunks:
            try:
                chunk_class = ClassificationLevel(chunk.get('classification', 'internal'))
                if ClassificationPolicy.can_retrieve(chunk_class, user_clearance):
                    allowed.append(chunk)
            except ValueError:
                # Unknown classification, filter to internal by default
                pass
        
        return allowed
