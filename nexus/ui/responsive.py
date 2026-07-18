from dataclasses import dataclass


@dataclass(frozen=True)
class ResponsiveLayout:
    sidebar: bool
    preview: bool
    activity: bool
    prompt_height: int
    density: str


def layout_for_width(width: int) -> ResponsiveLayout:
    if width < 100:
        return ResponsiveLayout(False, False, False, 4, "compact")
    if width < 140:
        return ResponsiveLayout(True, False, True, 6, "medium")
    return ResponsiveLayout(True, True, True, 6, "wide")
