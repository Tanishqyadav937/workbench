"""P2.2/P2.3: field flagging and the human-confirmation gate (pure logic, no model calls)."""
from typing import Any, Dict, List

LOW_CONF = 0.85  # tune on the extraction gold set; vision-model confidence is not well calibrated


def flag_fields(fields: List[Dict[str, Any]], threshold: float = LOW_CONF) -> List[Dict[str, Any]]:
    """Each field: {name, value, confidence}. Missing value or low/absent confidence -> needs_review."""
    out = []
    for f in fields:
        conf = f.get("confidence")
        review = f.get("value") in (None, "") or conf is None or float(conf) < threshold
        out.append(dict(f, needs_review=review, confirmed=False))
    return out


def ready_to_draft(fields: List[Dict[str, Any]]) -> bool:
    """Gate: drafting may start only when every flagged field has been confirmed by a human."""
    return all((not f.get("needs_review")) or f.get("confirmed") for f in fields)
