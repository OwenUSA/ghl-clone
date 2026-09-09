"""A refused write must be visible to the user.

The backend's role and CSRF checks are correct and well covered by test_auth.py.
What was not covered is the other half: the browser has to *say* that the write was
refused. Several screens dropped a 403 on the floor, and one of them cleared its
input as it did so, which reads as success.

Asserted against source because the project has no JS test runner; the behavioural
verification for each of these is a Playwright run recorded in
orchestrate/findings/fix-log.md.
"""
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"


def _read(*parts: str) -> str:
    return FRONTEND.joinpath(*parts).read_text(encoding="utf-8")


def test_contact_panel_renders_a_failed_write():
    """A TECH's PATCH and tag POST both 403; the panel used to show nothing at all."""
    source = _read("components", "ContactDetailsPanel.tsx")
    assert 'role="alert"' in source, "the panel has no error surface"
    for mutation in ("patch", "tagAdd", "tagRemove"):
        assert f"{mutation}.error" in source, (
            f"{mutation}'s failure is never read, so it cannot be shown")


def test_contact_panel_clears_the_tag_input_only_once_the_add_lands():
    """Clearing on keystroke made a refused add look like a successful one."""
    source = _read("components", "ContactDetailsPanel.tsx")
    # The tag input's own handlers: everything between its value and its placeholder.
    # (An inline-edit field earlier in the file has an onKeyDown too.)
    tag_input = source.split("value={newTag}", 1)[1].split('placeholder="Add tag"', 1)[0]
    assert "tagAdd.mutate" in tag_input, "retarget this test — the handler moved"
    assert "setNewTag('')" not in tag_input, (
        "the tag input is cleared on keystroke, so a refused add still reads as success")
    tag_add = source.split("const tagAdd = useMutation({", 1)[1].split("})", 1)[0]
    assert "setNewTag('')" in tag_add, "the input is never cleared on a successful add"
