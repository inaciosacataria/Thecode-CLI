from nexus.permissions.manager import PermissionManager
from nexus.permissions.risk import RiskLevel


def test_allow_once_prompts_again() -> None:
    responses = iter(["once", "deny"])
    manager = PermissionManager("ask", lambda _: next(responses))

    assert manager.authorize("first write", RiskLevel.MEDIUM).allowed
    assert not manager.authorize("second write", RiskLevel.MEDIUM).allowed


def test_allow_session_skips_later_confirmations() -> None:
    prompts: list[str] = []

    def confirm(description: str) -> str:
        prompts.append(description)
        return "session"

    manager = PermissionManager("ask", confirm)  # type: ignore[arg-type]

    assert manager.authorize("first write", RiskLevel.MEDIUM).allowed
    assert manager.authorize("second write", RiskLevel.HIGH).allowed
    assert prompts == ["first write"]


def test_critical_action_remains_blocked_after_allow_session() -> None:
    manager = PermissionManager("ask", lambda _: "session")

    assert manager.authorize("write", RiskLevel.MEDIUM).allowed
    decision = manager.authorize("critical", RiskLevel.CRITICAL)
    assert not decision.allowed
    assert decision.reason == "Critical actions are blocked"
