from pydantic import BaseModel

from nexus.permissions.risk import RiskLevel


class PermissionDecision(BaseModel):
    allowed: bool
    requires_confirmation: bool = False
    reason: str


def decide(mode: str, risk: RiskLevel) -> PermissionDecision:
    if risk is RiskLevel.CRITICAL:
        return PermissionDecision(allowed=False, reason="Critical actions are blocked")
    if risk is RiskLevel.READ_ONLY:
        return PermissionDecision(allowed=True, reason="Read-only action")
    if mode == "safe":
        return PermissionDecision(allowed=True, requires_confirmation=True, reason="Safe mode")
    if mode == "ask":
        return PermissionDecision(allowed=True, requires_confirmation=True, reason="Ask mode")
    if risk >= RiskLevel.HIGH:
        return PermissionDecision(allowed=True, requires_confirmation=True, reason="High-risk action")
    return PermissionDecision(allowed=True, reason="Low-risk action allowed in auto mode")

