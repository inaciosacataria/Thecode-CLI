from textual.theme import Theme

NEXUS_AURORA = Theme(
    name="nexus-aurora",
    primary="#E8B84C",
    secondary="#A970FF",
    accent="#66C7FF",
    foreground="#F2F2F2",
    background="#12071B",
    surface="#1B1025",
    panel="#1B1025",
    success="#79D279",
    warning="#FFC857",
    error="#FF6666",
    dark=True,
    variables={
        "footer-background": "#1B1025",
        "footer-foreground": "#AFAFAF",
        "footer-key-foreground": "#FFC857",
        "input-cursor-background": "#E8B84C",
        "input-cursor-foreground": "#12071B",
        "input-selection-background": "#A970FF 45%",
        "scrollbar-color": "#E8B84C 45%",
        "border": "#E8B84C",
    },
)


def _theme(name: str, background: str, panel: str, primary: str, accent: str, foreground: str = "#F2F2F2") -> Theme:
    return Theme(
        name=name,
        primary=primary,
        secondary=accent,
        accent=accent,
        foreground=foreground,
        background=background,
        surface=panel,
        panel=panel,
        success="#79D279",
        warning="#FFC857",
        error="#FF6666",
        dark=name != "github-light",
        variables={"border": primary, "footer-key-foreground": primary},
    )


CUSTOM_THEMES = (
    NEXUS_AURORA,
    _theme("nexus-dark", "#0D0D12", "#17171F", "#E8B84C", "#A970FF"),
    _theme("nexus-night", "#080B16", "#111827", "#66C7FF", "#A970FF"),
    _theme("dracula", "#282A36", "#343746", "#BD93F9", "#FF79C6"),
    _theme("catppuccin", "#1E1E2E", "#313244", "#CBA6F7", "#89B4FA"),
    _theme("tokyo-night", "#1A1B26", "#24283B", "#7AA2F7", "#BB9AF7"),
    _theme("gruvbox", "#282828", "#3C3836", "#FABD2F", "#83A598"),
    _theme("nord", "#2E3440", "#3B4252", "#88C0D0", "#B48EAD"),
    _theme("solarized", "#002B36", "#073642", "#B58900", "#268BD2"),
    _theme("github-dark", "#0D1117", "#161B22", "#D29922", "#58A6FF"),
    _theme("github-light", "#FFFFFF", "#F6F8FA", "#9A6700", "#0969DA", "#1F2328"),
)

THEME_NAMES = tuple(theme.name for theme in CUSTOM_THEMES)
