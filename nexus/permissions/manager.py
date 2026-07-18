import inspect
from collections.abc import Awaitable, Callable
from typing import Literal

from nexus.permissions.policies import PermissionDecision, decide
from nexus.permissions.risk import RiskLevel

type PermissionResponse = Literal["once", "session", "deny"]


class PermissionManager:
    def __init__(
        self,
        mode: str,
        confirm: Callable[[str], PermissionResponse | bool | Awaitable[PermissionResponse | bool]] | None = None,
    ) -> None:
        self.mode = mode
        self.confirm = confirm or (lambda _: "deny")
        self.allow_for_session = False

    def authorize(self, description: str, risk: RiskLevel) -> PermissionDecision:
        decision = decide(self.mode, risk)
        if not decision.allowed or not decision.requires_confirmation:
            return decision
        if self.allow_for_session:
            return PermissionDecision(allowed=True, reason="Allowed for this session")
        response = self.confirm(description)
        if inspect.isawaitable(response):
            raise RuntimeError("Async permission callback requires authorize_async")
        return self._response_decision(response)

    async def authorize_async(
        self, description: str, risk: RiskLevel, *, always_confirm: bool = False
    ) -> PermissionDecision:
        decision = decide(self.mode, risk)
        if always_confirm and decision.allowed:
            decision.requires_confirmation = True
            decision.reason = "This action always requires confirmation"
        if not decision.allowed or not decision.requires_confirmation:
            return decision
        if self.allow_for_session and not always_confirm:
            return PermissionDecision(allowed=True, reason="Allowed for this session")
        response = self.confirm(description)
        if inspect.isawaitable(response):
            response = await response
        return self._response_decision(response)

    def _response_decision(self, response: PermissionResponse | bool) -> PermissionDecision:
        # Boolean responses remain supported for integrations using the old callback contract.
        if response is True or response == "once":
            return PermissionDecision(allowed=True, reason="User allowed once")
        if response == "session":
            self.allow_for_session = True
            return PermissionDecision(allowed=True, reason="User allowed for this session")
        return PermissionDecision(allowed=False, reason="User denied")
