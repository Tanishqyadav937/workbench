"""P2.6: approval gate. Export is refused until a human approval is recorded."""


class ApprovalRequired(Exception):
    pass


def require_approval(state: dict) -> None:
    if not state.get("approved_by"):
        raise ApprovalRequired("draft has not been approved by a human")
