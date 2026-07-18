from nexus.ui.themes import CUSTOM_THEMES, NEXUS_TOKENS


def test_aurora_exposes_complete_semantic_token_set() -> None:
    required = {
        "background", "surface", "surface-alt", "border", "border-focus",
        "text-primary", "text-secondary", "text-muted", "accent", "success",
        "warning", "error", "info", "diff-add", "diff-remove",
    }
    assert required <= NEXUS_TOKENS.keys()
    for theme in CUSTOM_THEMES:
        assert required <= theme.variables.keys()
