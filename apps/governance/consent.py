"""C10: Consent registry and usage tracking."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set
from datetime import datetime
import logging


logger = logging.getLogger(__name__)


class ConsentStatus(str, Enum):
    """Consent status."""
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class Purpose(str, Enum):
    """Predefined purposes for data use."""
    INSPECTION_REVIEW = "inspection_review"
    AUDIT = "audit"
    TRAINING = "training"
    RESEARCH = "research"
    COMPLIANCE = "compliance"
    CUSTOMER_SERVICE = "customer_service"


@dataclass
class ConsentRecord:
    """Record of consent for a document and set of purposes."""
    doc_id: str
    granted_by: str  # user_id or org_id
    purposes: List[str] = field(default_factory=list)
    granted_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    revoked_at: Optional[str] = None
    status: ConsentStatus = ConsentStatus.ACTIVE
    reason: Optional[str] = None
    
    def is_active(self) -> bool:
        return self.status == ConsentStatus.ACTIVE
    
    def allows(self, purpose: str) -> bool:
        """Check if this consent covers the purpose."""
        return self.is_active() and purpose in self.purposes


@dataclass
class UsageRecord:
    """Record of data usage (audit trail)."""
    doc_id: str
    session_id: str
    user_id: str
    purpose: str
    accessed_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    chunks_accessed: int = 0
    action: str = "retrieve"  # retrieve, redact, analyze, export


class ConsentRegistry:
    """C10: Manages consent and usage tracking."""
    
    def __init__(self):
        # In-memory storage (backed by Postgres in production)
        self.consents: Dict[str, List[ConsentRecord]] = {}  # doc_id -> [records]
        self.usage_log: List[UsageRecord] = []
    
    async def grant_consent(
        self,
        doc_id: str,
        granted_by: str,
        purposes: List[str],
        reason: Optional[str] = None,
    ) -> ConsentRecord:
        """Grant consent for data use."""
        record = ConsentRecord(
            doc_id=doc_id,
            granted_by=granted_by,
            purposes=purposes,
            reason=reason,
            status=ConsentStatus.ACTIVE,
        )
        
        if doc_id not in self.consents:
            self.consents[doc_id] = []
        
        self.consents[doc_id].append(record)
        logger.info(f"Consent granted for {doc_id}: {purposes}")
        
        return record
    
    async def revoke_consent(
        self,
        doc_id: str,
        purpose: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> int:
        """Revoke consent. If purpose is None, revoke all."""
        count = 0
        
        if doc_id not in self.consents:
            return 0
        
        for record in self.consents[doc_id]:
            if not record.is_active():
                continue
            
            if purpose is None:
                # Revoke all purposes
                record.status = ConsentStatus.REVOKED
                record.revoked_at = datetime.utcnow().isoformat()
                record.reason = reason
                count += 1
            elif purpose in record.purposes:
                # Revoke only this purpose; remove it from the list
                record.purposes.remove(purpose)
                if not record.purposes:
                    # No purposes left, mark entire record as revoked
                    record.status = ConsentStatus.REVOKED
                    record.revoked_at = datetime.utcnow().isoformat()
                count += 1
        
        logger.info(f"Revoked {count} consent record(s) for {doc_id}")
        return count
    
    async def check_consent(
        self,
        doc_id: str,
        purpose: str,
    ) -> bool:
        """Check if a document can be used for a purpose."""
        if doc_id not in self.consents:
            # No consent record = no permission
            return False
        
        for record in self.consents[doc_id]:
            if record.allows(purpose):
                return True
        
        return False
    
    async def log_usage(
        self,
        doc_id: str,
        session_id: str,
        user_id: str,
        purpose: str,
        chunks_accessed: int = 1,
        action: str = "retrieve",
    ) -> UsageRecord:
        """Log data access for audit trail."""
        record = UsageRecord(
            doc_id=doc_id,
            session_id=session_id,
            user_id=user_id,
            purpose=purpose,
            chunks_accessed=chunks_accessed,
            action=action,
        )
        
        self.usage_log.append(record)
        logger.debug(f"Usage logged: {doc_id} by {user_id} for {purpose}")
        
        return record
    
    async def get_usage_history(
        self,
        doc_id: str,
        limit: int = 100,
    ) -> List[UsageRecord]:
        """Get usage history for a document."""
        return [r for r in self.usage_log if r.doc_id == doc_id][-limit:]
    
    async def get_sessions_accessing_doc(
        self,
        doc_id: str,
    ) -> Set[str]:
        """Get all session IDs that accessed this document."""
        sessions = set()
        for record in self.usage_log:
            if record.doc_id == doc_id:
                sessions.add(record.session_id)
        
        return sessions
    
    async def get_documents_accessed_by_session(
        self,
        session_id: str,
    ) -> Set[str]:
        """Get all documents accessed in a session."""
        docs = set()
        for record in self.usage_log:
            if record.session_id == session_id:
                docs.add(record.doc_id)
        
        return docs
