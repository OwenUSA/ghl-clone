"""Layout invariants that a broken build still satisfies, so a build gate misses them.

Asserted against source because there is no JS test runner here; the behavioural
verification is a Playwright measurement recorded in orchestrate/findings/fix-log.md
(before the fix, 3 of 6 Sort items and 7 of 15 Filter items were off-screen or covered;
after it, 0 of both).
"""
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"


def test_conversations_dropdown_anchors_itself_vertically():
    """An `absolute` menu with no `top` falls back to its static position.

    Both Conversations menus hang inside a `relative` flex row with `items-center`,
    so an unanchored menu is centred on the 24px icon row and overflows off the top
    of the window — taking `All` and the default sort with it, unreachable.
    """
    source = (FRONTEND / "pages" / "ConversationsPage.tsx").read_text(encoding="utf-8")
    dropdown = source.split("function Dropdown(", 1)[1].split("\n}\n", 1)[0]
    assert 'role="menu"' in dropdown, "Dropdown no longer renders the menu — retarget this"
    assert "absolute" in dropdown, "Dropdown is no longer absolutely positioned"
    style = dropdown.split("style={{", 1)[1].split("} as React.CSSProperties", 1)[0]
    style = re.sub(r"//[^\n]*", "", style)  # the comment explains `top`; don't match it
    assert re.search(r"\btop\s*:", style) or re.search(r"\bbottom\s*:", style), (
        "the Conversations dropdown sets neither top nor bottom, so it renders at its "
        "static position — vertically centred on its trigger and clipped off-screen")
