from pydantic import BaseModel

from nexus.permissions.risk import RiskLevel


class PermissionDecision(BaseModel):
    allowed: bool
    requires_confirmation: bool = False
    reason: str


def decide(mode: str, risk: RiskLevel) -> PermissionDecision:
    mode = "ask" if mode == "safe" else mode
    if risk is RiskLevel.CRITICAL:
        return PermissionDecision(allowed=False, reason="Critical actions are blocked")
    if risk is RiskLevel.READ_ONLY:
        return PermissionDecision(allowed=True, reason="Read-only action")
    if mode == "plan":
        return PermissionDecision(allowed=False, reason="Plan mode is read-only")
    if mode == "ask":
        if risk is RiskLevel.LOW:
            return PermissionDecision(allowed=True, reason="Low-risk action")
        return PermissionDecision(allowed=True, requires_confirmation=True, reason="Ask mode")
    if mode == "agent":
        if risk >= RiskLevel.HIGH:
            return PermissionDecision(
                allowed=True, requires_confirmation=True, reason="High-risk action"
            )
        return PermissionDecision(allowed=True, reason="Agent mode")
    return PermissionDecision(allowed=True, reason="Auto mode")
