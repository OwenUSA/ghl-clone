"""The board card and the opportunity modal (2026-09-13): executed where it can be.

**Executed under node.** Which tab a card icon opens (`lib/opportunityModal.ts`), the
Notes tab's search / filter / sort (`lib/notesView.ts`) and the shell hook
(`lib/openRecord.ts`) are import-free, so the real shipped code runs here — the
harness `test_custom_fields_ui.py` already uses.

**Asserted against source.** The card and the modal are React, which this project
has no runner for. Each assertion names the failure it would catch. They were
also driven end to end in a real browser against a throwaway database before
this was committed (29 interactions — recorded in .qa/state/oppmodal-done).
"""
import json
import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
NODE = shutil.which("node")

node = pytest.mark.skipif(
    NODE is None,
    reason="node is not on PATH, so the card's modules cannot be executed. CI's "
           "backend job installs it so this file is never skipped there.")


def _read(*parts: str) -> str:
    return FRONTEND.joinpath(*parts).read_text(encoding="utf-8")


def run_js(module: str, body: str):
    script = textwrap.dedent("""
        import * as m from %s
        const out = (v) => console.log('@@' + JSON.stringify(v))
    """) % json.dumps((FRONTEND / "lib" / module).as_posix()) + textwrap.dedent(body)
    proc = subprocess.run(
        [NODE, "--experimental-strip-types", "--input-type=module", "-"],
        input=script, capture_output=True, text=True,
        env={**os.environ, "TZ": "America/New_York"})
    assert proc.returncode == 0, proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@")]
    assert line, "the script printed no result:\n" + proc.stdout + proc.stderr
    return json.loads(line[-1][2:])


# ---------------- executed: which tab an icon opens ----------------

@node
def test_each_card_icon_opens_the_modal_on_its_own_tab():
    got = run_js("opportunityModal.ts", """
        const opened = {}
        for (const icon of ['tags', 'notes', 'task', 'appointment']) {
          m.requestModalTab(7, m.CARD_ICON_REQUEST[icon])
          opened[icon] = m.takeModalTab(7)
        }
        out(opened)
    """)
    assert got == {
        "tags": {"tab": "details", "focus": "tags"},
        "notes": {"tab": "notes"},
        "task": {"tab": "tasks", "adding": True},
        "appointment": {"tab": "appointment"},
    }


@node
def test_a_tab_request_is_used_once_and_never_for_another_deal():
    """A request is one-shot: the palette opening the same deal later must land on
    Opportunity details, and a request left for deal 7 must not steer deal 8."""
    got = run_js("opportunityModal.ts", """
        m.requestModalTab(7, m.CARD_ICON_REQUEST.notes)
        const first = m.takeModalTab(7), second = m.takeModalTab(7)
        m.requestModalTab(7, m.CARD_ICON_REQUEST.task)
        const other = m.takeModalTab(8), after = m.takeModalTab(7)
        out({ first, second, other, after })
    """)
    assert got["first"] == {"tab": "notes"}
    assert got["second"] == {"tab": "details"}, "a used request steered a later open"
    assert got["other"] == {"tab": "details"}, "deal 7's request opened deal 8's tab"
    assert got["after"] == {"tab": "details"}, "a discarded request was kept"


# ---------------- executed: the Notes tab ----------------

NOTES = """
const rows = [
  { kind: 'deal', id: 1, body: 'Skylight flashing loose',
    at: '2026-09-10T10:00:00Z', author: 'Owen' },
  { kind: 'deal', id: 2, body: 'Customer prefers mornings',
    at: '2026-09-12T10:00:00Z', author: 'Dana' },
  { kind: 'contact', id: 9, body: 'Merged from Workiz: skylight job',
    at: '2026-08-01T10:00:00Z', author: null },
]
const ids = (r) => r.map((x) => x.kind + x.id)
"""


@node
def test_notes_search_filter_and_sort_really_narrow_and_order():
    got = run_js("notesView.ts", NOTES + """
        out({
          all: ids(m.filterNotes(rows, { q: '', author: null, sort: 'newest' })),
          oldest: ids(m.filterNotes(rows, { q: '', author: null, sort: 'oldest' })),
          search: ids(m.filterNotes(rows, { q: 'SKYLIGHT', author: null, sort: 'newest' })),
          dana: ids(m.filterNotes(rows, { q: '', author: 'Dana', sort: 'newest' })),
          contact: ids(m.filterNotes(rows, { q: '', author: m.CONTACT_NOTES, sort: 'newest' })),
          none: ids(m.filterNotes(rows, { q: 'gutter', author: null, sort: 'newest' })),
        })
    """)
    # This deal's own notes come first, then the contact's thread notes.
    assert got["all"] == ["deal2", "deal1", "contact9"]
    assert got["oldest"] == ["deal1", "deal2", "contact9"]
    assert got["search"] == ["deal1", "contact9"], "search is case-sensitive or ignored"
    assert got["dana"] == ["deal2"]
    assert got["contact"] == ["contact9"]
    assert got["none"] == []


# ---------------- executed: the shell hook ----------------

@node
def test_page_switching_is_offered_only_once_the_shell_registers():
    got = run_js("openRecord.ts", """
        const before = m.canOpenRecords()
        const calls = []
        const off = m.registerOpenRecord((view, id) => calls.push([view, id]))
        const during = m.canOpenRecords()
        m.openRecord('conversations', 42)
        off()
        m.openRecord('contacts', 1)
        out({ before, during, after: m.canOpenRecords(), calls })
    """)
    assert got == {"before": False, "during": True, "after": False,
                   "calls": [["conversations", 42]]}


def test_the_shell_registers_its_open_record_and_nothing_more():
    """The operator's fence exception: exactly two lines in App.tsx."""
    app = _read("App.tsx")
    assert "import { registerOpenRecord } from './lib/openRecord'" in app
    assert "useEffect(() => registerOpenRecord(openRecord), [openRecord])" in app


# ---------------- the card, against source ----------------

CARD = "components/OpportunityCard.tsx"


def test_the_card_has_no_emoji():
    """The owner: 'the same icons, no emojis'. Every glyph the old card drew and
    any other pictograph in the Unicode emoji blocks is refused."""
    source = _read(*CARD.split("/"))
    emoji = re.compile("[\U0001F300-\U0001FAFF☀-➿⬀-⯿]")
    found = sorted(set(emoji.findall(source)))
    assert not found, "the card still draws emoji: %r" % found


def test_every_icon_is_there_with_the_exact_tooltip():
    source = _read(*CARD.split("/"))
    for label, tip in (("Call", "Call"), ("View conversations", "View conversations"),
                       ("Add task", "Add task"),
                       ("Add an appointment", "Add an appointment")):
        assert re.search(r'label="%s"\s+tip="%s"' % (label, tip), source), (
            "the %s icon or its tooltip text is missing" % label)
    assert 'label="Tags"' in source and "o.tags.join('\\n')" in source, (
        "the tags tooltip does not list the tag names one per line")
    assert 'label="Notes"' in source and "o.note_previews.map(" in source, (
        "the notes tooltip does not list each note's first line")


def test_a_badge_only_appears_above_zero():
    source = _read(*CARD.split("/"))
    assert "badge != null && badge > 0 &&" in source, "a zero count draws a badge"


def test_an_icon_click_never_reaches_the_card_body():
    """Only a click on the BODY opens Opportunity details; an icon does its own thing.
    Without stopPropagation every icon would also open the modal on details."""
    source = _read(*CARD.split("/"))
    icon = source.split("function CardIcon(", 1)[1].split("\n}\n", 1)[0]
    assert "onClick={(e) => { e.stopPropagation(); onClick?.() }}" in icon
    assert "onPointerDown={(e) => e.stopPropagation()}" in icon, (
        "pressing an icon starts a drag of the card")
    owner = source.split("function OwnerButton(", 1)[1].split("\n}\n", 1)[0]
    assert "e.stopPropagation(); setError(null); setOpen" in owner, (
        "assigning an owner also opens the modal")


def test_icons_that_open_the_modal_ask_for_their_tab_first():
    source = _read(*CARD.split("/"))
    open_on = source.split("const openOn = ", 1)[1].split("\n  }\n", 1)[0]
    assert open_on.index("requestModalTab(") < open_on.index("onOpen(o.id)"), (
        "the modal opens before it has been told which tab")
    for icon in ("tags", "notes", "task", "appointment"):
        assert "openOn('%s')" % icon in source, "the %s icon does not open its tab" % icon


def test_call_and_conversations_do_their_own_thing():
    source = _read(*CARD.split("/"))
    call = source.split("const call = async () =>", 1)[1].split("\n  }\n", 1)[0]
    assert "callContact(o.contact_id)" in call and "onOpen" not in call
    convs = source.split("const conversations = async () =>", 1)[1].split("\n  }\n", 1)[0]
    assert "openRecord('conversations'" in convs and "onOpen" not in convs
    assert "canOpenRecords() &&" in source, (
        "View conversations is drawn even when the shell cannot switch pages")


def test_a_tech_never_gets_a_notes_icon():
    """The server leaves note counts out of a TECH's rows; the icon is drawn only
    when the count was sent. `?? 0` here would draw a Notes icon reading 'No notes'
    for a TECH — a lie about notes that exist."""
    source = _read(*CARD.split("/"))
    assert "const notesVisible = o.notes_count !== undefined" in source
    assert "{notesVisible && (" in source


def test_the_selection_mode_still_picks_and_opens_nothing():
    source = _read(*CARD.split("/"))
    assert "onOpen={selectable ? undefined : onOpen}" in source, (
        "in Bulk Actions a card's icons still open the modal")


# ---------------- the modal, against source ----------------

MODAL = "components/OpportunityDetail.tsx"


def test_the_modal_left_nav_is_ghls_without_payments():
    source = _read(*MODAL.split("/"))
    nav = source.split('aria-label="Opportunity sections"', 1)[1].split("</nav>", 1)[0]
    order = ["Opportunity details", "sections.groups.map(", "Book or update appointment",
             'label="Tasks"', 'label="Notes"', "Associated objects", "Manage fields"]
    at = [nav.index(item) for item in order]
    assert at == sorted(at), "the left nav is out of GoHighLevel's order"
    assert "Payments" not in nav, "Payments is out of the product (2026-09-10)"


def test_the_notes_tab_is_not_offered_to_a_tech():
    source = _read(*MODAL.split("/"))
    assert "const seesNotes = user.role !== 'TECH'" in source
    assert "{seesNotes && (" in source and "tab === 'notes' && seesNotes" in source


def test_no_audit_log_and_no_recorded_earlier_box():
    source = _read(*MODAL.split("/"))
    component = source.split("export function OpportunityDetail", 1)[1]
    assert "Audit log" not in component, "a fake audit id is rendered"
    assert "Recorded earlier" not in source
    assert "Hide empty fields" in component
    assert "Add and edit opportunity details, tasks, notes and appointments." in component
    assert 'Edit "{o.title}"' in component


def test_a_pipeline_move_cannot_be_saved_without_a_stage():
    source = _read(*MODAL.split("/"))
    assert "stageId: Number(v) === o.pipeline_id ? o.stage_id : null" in source, (
        "changing the pipeline keeps a stage from the old one")
    assert "form.stageId == null ? `Choose a stage in" in source
    assert "!!problem" in source, "Update is live while the stage is missing"


def test_notes_card_draws_no_association_chip():
    """GoHighLevel's chip counts a note's associations. Nothing here counts them, so
    it must not be drawn with a made-up number."""
    source = _read("components", "opportunity", "NotesTab.tsx")
    body = source.split("function NoteCard(", 1)[1].split("\n}\n", 1)[0]
    assert "association" not in body.lower()
    assert "Show more" in source and "Show less" in source
    assert "Created by: " in body


def test_probability_is_drawn_only_for_opportunity_level_probability():
    """§7: GoHighLevel shows a deal's Probability when its pipeline weighs each deal
    on its own probability. A field drawn on a stage-probability pipeline would
    save a number the Forecast never reads."""
    source = _read(*MODAL.split("/"))
    assert "const probabilityShown = !!current?.use_opportunity_probability" in source
    assert "{probabilityShown && (" in source and 'aria-label="Probability"' in source
    assert "changes(o, form, probabilityShown)" in source, (
        "a hidden Probability field still sends a value on Update")
    save = source.split("const save = useMutation({", 1)[1].split("\n  })", 1)[0]
    assert "changes(o, form, shown)" in save and "use_opportunity_probability" in save, (
        "Update is enabled by a typed probability but the save does not send it — "
        "found in the browser: the modal closed and nothing was written")
    changes = source.split("function changes(", 1)[1].split("\n}\n", 1)[0]
    assert "if (probabilityShown) {" in changes


def test_the_pipeline_dropdown_lists_what_the_server_says_this_user_can_access():
    """The dropdown is fed by GET /api/pipelines, which omits any pipeline the user
    cannot access (test_opportunity_workspace.py proves the list and the refusal).
    It must not be fed from anything that lists every pipeline."""
    source = _read(*MODAL.split("/"))
    assert "queryFn: listPipelines" in source
    assert "pipelineList.map((p) => <option" in source


@node
def test_manage_fields_links_to_the_settings_custom_fields_deep_link():
    """Brief §2: "⚙ Manage fields" LINKS TO Settings → Custom Fields. It navigates to
    the path SETTINGS_SECTIONS names, and this executes the shipped resolvers App.tsx
    and SettingsPage use on load to prove that path lands on that section."""
    source = _read(*MODAL.split("/"))
    assert ("SETTINGS_SECTIONS.find((s) => s.key === 'custom-fields')!.path"
            in source), "the link is not taken from the Settings sections table"
    nav = source.split('aria-label="Opportunity sections"', 1)[1].split("</nav>", 1)[0]
    button = nav.rsplit("<button", 1)[1]
    assert "window.location.assign(CUSTOM_FIELDS_PATH)" in button and \
        "Manage fields" in button, "Manage fields does not navigate to Settings"
    assert "if (dirty && !window.confirm(" in button, (
        "leaving for Settings silently discards an unsaved edit")
    assert "<CustomFieldsPanel" not in source, "the old overlay is still rendered"

    proc = subprocess.run(
        [NODE, "--experimental-strip-types", "--input-type=module", "-"],
        input=textwrap.dedent("""
            import * as m from %s
            const path = m.SETTINGS_SECTIONS.find((s) => s.key === 'custom-fields').path
            console.log('@@' + JSON.stringify(
              [path, m.viewFromPath(path), m.sectionFromPath(path)]))
        """) % json.dumps((FRONTEND / "lib" / "settingsSections.ts").as_posix()),
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    got = json.loads([ln for ln in proc.stdout.splitlines() if ln.startswith("@@")][-1][2:])
    assert got == ["/settings/custom-fields", "settings", "custom-fields"]
