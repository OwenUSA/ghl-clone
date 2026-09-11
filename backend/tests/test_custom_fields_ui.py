"""The custom-field screens: the visibility rules executed, the panel asserted.

Two halves, and the split is deliberate.

**Executed.** `frontend/src/lib/customFields.ts` decides which questions a deal is
asked and which answers are kept-but-not-drawn. "Hidden without being lost" is the
whole feature, and a regex saying the file mentions `keptButNotAsked` would pass
against a version that dropped the answers on the floor. The module is therefore
import-free, like `calendarGrid.ts` and `reminders.ts`, so node runs the real
shipped code — the harness here is the same one `test_calendar_grid.py` uses.

**Asserted against source.** The panel and the two forms are React, which this
project has no runner for. Those tests follow the existing idiom, and each one
names a specific failure it would have caught.
"""
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
FIELDS_TS = FRONTEND / "lib" / "customFields.ts"

NODE = shutil.which("node")

node = pytest.mark.skipif(
    NODE is None,
    reason="node is not on PATH, so customFields.ts cannot be executed. CI's "
           "backend job installs it precisely so this file is never skipped "
           "there — see .github/workflows/ci.yml.")


def _read(*parts: str) -> str:
    return FRONTEND.joinpath(*parts).read_text(encoding="utf-8")


def run_js(body: str):
    """Execute `body` with the real module imported as `f`, and return its JSON."""
    script = textwrap.dedent("""
        import * as f from %s
        const out = (v) => console.log('@@' + JSON.stringify(v))
    """) % json.dumps(FIELDS_TS.as_posix()) + textwrap.dedent(body)
    proc = subprocess.run(
        [NODE, "--experimental-strip-types", "--input-type=module", "-"],
        input=script, capture_output=True, text=True,
        env={**os.environ, "TZ": "America/New_York"})
    assert proc.returncode == 0, proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@")]
    assert line, "the script printed no result:\n" + proc.stdout + proc.stderr
    return json.loads(line[-1][2:])


# Three definitions: one on both pipelines, one on AHS only, one archived.
DEFS = """
const defs = [
  { id: 3, key: 'how_old_is_the_roof', label: 'How old is the roof?',
    field_type: 'number', options: [], position: 2, entity: 'opportunity',
    archived: false, pipeline_ids: [1, 2] },
  { id: 1, key: 'ahs_claim_number', label: 'AHS claim number',
    field_type: 'text', options: [], position: 0, entity: 'opportunity',
    archived: false, pipeline_ids: [1] },
  { id: 2, key: 'old_question', label: 'Old question',
    field_type: 'text', options: [], position: 1, entity: 'opportunity',
    archived: true, pipeline_ids: [1] },
]
"""


# ---------------- executed: which questions a deal is asked ----------------

@node
def test_a_field_on_two_pipelines_is_asked_on_both_and_in_display_order():
    got = run_js(DEFS + """
        out({
          ahs: f.askedOn(defs, 1).map((d) => d.key),
          retail: f.askedOn(defs, 2).map((d) => d.key),
          nowhere: f.askedOn(defs, 3).map((d) => d.key),
          noPipeline: f.askedOn(defs, null).map((d) => d.key),
        })
    """)
    # Sorted by position, NOT by the order the API happened to return them.
    assert got["ahs"] == ["ahs_claim_number", "how_old_is_the_roof"]
    assert got["retail"] == ["how_old_is_the_roof"]
    assert got["nowhere"] == [], "a field leaked onto a pipeline it is not on"
    assert got["noPipeline"] == [], "questions were asked with no pipeline chosen"


@node
def test_an_archived_field_is_asked_nowhere():
    got = run_js(DEFS + """
        out(f.askedOn(defs, 1).map((d) => d.key))
    """)
    assert "old_question" not in got, "an archived field is still being asked"


@node
def test_an_answer_the_pipeline_does_not_ask_is_kept_and_labelled():
    """The visible half of "moving a deal never discards anything". A deal in
    Retail holding an AHS claim number must still SHOW it, read-only, with the
    reason — an answer nobody can see is an answer nobody knows they have."""
    got = run_js(DEFS + """
        const answers = {
          how_old_is_the_roof: 14,
          ahs_claim_number: 'AHS-99887',
          old_question: 'still here',
          owen_call_id: 'call-abc-123',
        }
        out({
          retail: f.keptButNotAsked(defs, answers, 2)
                   .map((r) => [r.key, r.value, r.why]),
          ahs: f.keptButNotAsked(defs, answers, 1)
                .map((r) => [r.key, r.value, r.why]),
        })
    """)
    assert got["retail"] == [
        ["ahs_claim_number", "AHS-99887", "Not asked on this pipeline"],
        ["old_question", "still here", "This field is archived"],
    ], got["retail"]
    # On AHS the claim number is a live control, so only the archived one is kept.
    assert [row[0] for row in got["ahs"]] == ["old_question"]
    # owen_* never appears here: it has its own block and its own rules.
    for rows in got.values():
        assert all(not key.startswith("owen_") for key, _v, _w in rows)


@node
def test_an_answer_under_no_definition_at_all_is_still_shown():
    """A key written by something other than this UI — the CLI, an old import —
    is displayed rather than hidden. Dropping it from the form is how it would
    then be dropped from the record."""
    got = run_js(DEFS + """
        out(f.keptButNotAsked(defs, { legacy_note: 'from the old system' }, 1)
             .map((r) => [r.key, r.value, r.why, r.def]))
    """)
    assert got == [["legacy_note", "from the old system",
                    "Recorded against no field definition", None]]


@node
def test_the_telephony_block_holds_every_reserved_key_and_nothing_else():
    got = run_js("""
        out(f.telephonyAnswers({
          how_old_is_the_roof: 14,
          owen_tracking_number: '(941) 555-9999',
          owen_call_id: 'call-abc-123',
          owen_campaign: 'Spring',
          owen_is_new_caller: true,
        }))
    """)
    assert [key for key, _v in got] == [
        "owen_call_id", "owen_campaign", "owen_is_new_caller",
        "owen_tracking_number"]


# ---------------- executed: one answer in and out of a control ----------------

@node
def test_a_boolean_round_trips_through_the_select_without_becoming_a_string():
    """The control's value is a string. Storing "false" would be TRUTHY on the
    way back and turn every No into a Yes."""
    got = run_js("""
        const d = { id: 1, key: 'leaking', label: 'Is it leaking?',
                    field_type: 'boolean', options: [], position: 0,
                    entity: 'opportunity', archived: false, pipeline_ids: [1] }
        out({
          shownTrue: f.inputValue(d, true),
          shownFalse: f.inputValue(d, false),
          shownUnanswered: f.inputValue(d, undefined),
          backTrue: f.submitValue(d, 'true'),
          backFalse: f.submitValue(d, 'false'),
          backBlank: f.submitValue(d, ''),
        })
    """)
    assert got == {"shownTrue": "true", "shownFalse": "false",
                   "shownUnanswered": "", "backTrue": True, "backFalse": False,
                   "backBlank": ""}


@node
def test_a_number_that_is_not_a_number_is_sent_on_for_the_server_to_refuse():
    """Silently dropping it here would save the deal and lose what was typed
    without a word. The server answers with a sentence naming the field."""
    got = run_js("""
        const d = { id: 1, key: 'age', label: 'How old is the roof?',
                    field_type: 'number', options: [], position: 0,
                    entity: 'opportunity', archived: false, pipeline_ids: [1] }
        out({ good: f.submitValue(d, '14'), decimal: f.submitValue(d, '2.5'),
              bad: f.submitValue(d, 'abc'), blank: f.submitValue(d, '') })
    """)
    assert got == {"good": 14, "decimal": 2.5, "bad": "abc", "blank": ""}


@node
def test_a_dropdown_keeps_an_answer_whose_option_was_retired():
    """Precedent: the appointment panel keeps an unlisted status as an option so
    opening a booking cannot silently rewrite it. Retiring "Tile" must not turn
    every deal holding it into a deal holding "Shingle"."""
    got = run_js("""
        const d = { id: 1, key: 'roof', label: 'What type of roof?',
                    field_type: 'dropdown', options: ['Shingle', 'Metal'],
                    position: 0, entity: 'opportunity', archived: false,
                    pipeline_ids: [1] }
        out({ retired: f.optionsFor(d, 'Tile'),
              current: f.optionsFor(d, 'Metal'),
              unanswered: f.optionsFor(d, null) })
    """)
    assert got["retired"] == ["Shingle", "Metal", "Tile"], (
        "the stored answer is not offered, so opening the deal would change it")
    assert got["current"] == ["Shingle", "Metal"]
    assert got["unanswered"] == ["Shingle", "Metal"]


@node
def test_a_booking_is_named_the_way_the_deal_shows_it():
    got = run_js("""
        out(f.describeBooking('Inspection', '2026-09-15T13:00:00Z'))
    """)
    assert got.startswith("Inspection — "), got
    assert "9:00" in got, got          # 13:00Z is 9am in America/New_York


# ---------------- asserted against source ----------------

def test_one_component_asks_the_questions_on_both_forms():
    """Two copies would drift: a field added to one form and not the other, or
    validated differently by each. The detail form and the Add dialog use the
    SAME component."""
    for where, parts in (("detail", ("components", "OpportunityDetail.tsx")),
                         ("add dialog", ("pages", "OpportunitiesPage.tsx"))):
        source = _read(*parts)
        assert "<CustomFieldAnswers" in source, (
            "the %s does not render the job questions" % where)


def test_the_board_card_does_not_render_custom_fields():
    """The owner was explicit: not on the board card. It is already dense and has
    to stay scannable."""
    source = _read("pages", "OpportunitiesPage.tsx")
    card = source.split("function CardFace(", 1)[1].split("\nfunction ", 1)[0]
    for leak in ("CustomFieldAnswers", "custom_fields", "customFields"):
        assert leak not in card, (
            "the board card renders custom fields (%s)" % leak)


def test_every_control_on_the_panel_is_dead_for_a_role_that_cannot_use_it():
    """Precedent d1f7c50 / b943f4b: a disabled control with a reason, never a
    form that 403s on submit. Every write under /api/custom-fields is ADMIN."""
    source = _read("components", "CustomFieldsPanel.tsx")
    assert "const canManage = user.role === 'ADMIN'" in source, (
        "the panel never asks whether this role can manage fields")
    assert "Only an admin can define, edit or archive custom fields" in source, (
        "nothing tells the user why a control is dead")
    # Each of the five writes is gated.
    for control in ("add.mutate", "save.mutate", "archive.mutate",
                    "restore.mutate", "reorder.mutate"):
        assert control in source, "retarget this test -- %s moved" % control
    assert source.count("!canManage") >= 5, (
        "fewer controls are gated than there are writes on the screen")


def test_the_panel_reports_a_refusal_instead_of_swallowing_it():
    source = _read("components", "CustomFieldsPanel.tsx")
    assert source.count('role="alert"') >= 2, (
        "the panel has no error surface for a failed write and a failed read")
    assert "onError: failed" in source, "a refused write is dropped on the floor"
    assert "fields.error" in source, (
        "a refused or failed read renders as an empty field list, which reads as "
        "'there are no custom fields'")


def test_delete_says_archive_on_the_screen_and_says_the_answers_are_kept():
    """The button must not say Delete. It archives, the answers survive, and a
    user who believes otherwise will avoid a control they should be using."""
    source = _read("components", "CustomFieldsPanel.tsx")
    # The rendered component only -- the docstring above it is allowed to use the
    # word "Delete" while explaining why the screen does not.
    body = source.split("export function CustomFieldsPanel", 1)[1]
    assert "Archive" in body and "Really archive" in body, (
        "the archive control does not say what it does")
    assert "Delete" not in body, (
        "the panel offers a Delete, but nothing here deletes anything")
    # JSX prose wraps across source lines; the sentence is what matters, not the
    # line breaks a formatter happened to put in it.
    flat = " ".join(body.split())
    assert "keeps every answer already recorded" in flat, (
        "the screen never says the answers are kept")
    assert "Restore" in body, "an archived field cannot be brought back"


def test_the_type_and_the_key_are_not_offered_as_editable_controls():
    """Both are fixed at creation because answers are already filed against them.
    A control that silently does nothing is worse than none."""
    source = _read("components", "CustomFieldsPanel.tsx")
    patch = source.split("const save = useMutation({", 1)[1].split("})", 1)[0]
    assert "field_type" not in patch, "the edit sends a type change the API ignores"
    assert "key" not in patch, "the edit sends a key change, which orphans answers"
    assert "The type is fixed once the field exists" in source, (
        "nothing on screen says the type cannot be changed")


def test_a_field_attached_to_no_pipeline_says_it_will_be_asked_nowhere():
    """An empty attachment set means NOWHERE, not everywhere. Silence here would
    let an admin define a field and never see it again."""
    source = _read("components", "CustomFieldsPanel.tsx")
    assert "asked on no deal at all" in source, (
        "nothing warns that a field with no pipeline is invisible")


def test_booking_from_a_deal_reuses_the_create_dialog():
    """Not a second dialog: the existing one already validates the times and
    schedules the customer's reminders, and a booking made from a deal has to be
    identical to one made on the calendar."""
    source = _read("components", "OpportunityDetail.tsx")
    assert "<NewAppointmentDialog" in source, (
        "the deal opens something other than the shared create dialog")
    assert "lockedOpportunity={{ id: o.id" in source, (
        "the booking is not bound to the deal it was opened from")
    assert "initialContact=" in source and "initialTitle=" in source, (
        "the contact and title are not prefilled from the deal")


def test_the_booking_dialog_is_not_nested_inside_the_deals_backdrop():
    """The detail's backdrop closes the deal on any click that reaches it. With
    the booking dialog inside it, every click in that dialog bubbled up and shut
    the deal behind it — the dialog vanished mid-typing."""
    source = _read("components", "OpportunityDetail.tsx")
    body = source.split("return (", 1)[1]
    backdrop = body.split("onClick={onClose}", 1)[1]
    before_dialog = backdrop.split("<NewAppointmentDialog", 1)[0]
    assert before_dialog.count("</div>") > before_dialog.count("<div"), (
        "the booking dialog is still inside the backdrop that closes the deal")


def test_the_calendar_can_bind_a_booking_to_an_open_deal():
    """The same link from the other direction, and only OPEN deals are offered."""
    dialog = _read("components", "NewAppointmentDialog.tsx")
    assert "listOpenOpportunities" in dialog, (
        "the calendar's create dialog cannot offer a deal to bind to")
    assert "Not linked to a deal" in dialog, (
        "the link is not optional, or does not say so")
    panel = _read("components", "AppointmentDetailDialog.tsx")
    assert "opportunity_id" in panel, "the panel cannot rebind a booking"
    assert "opportunity_title" in panel, "the panel never names the deal"


def test_binding_a_deal_is_sent_on_its_own_and_not_as_a_reschedule():
    """Only `starts_at` and the status touch the reminder queue. A bind that
    dragged the times along would churn the customer's reminders for nothing."""
    source = _read("components", "AppointmentDetailDialog.tsx")
    changes = source.split("function changes(): AppointmentPatch | null {", 1)[1]
    changes = changes.split("\n  }", 1)[0]
    bind = changes.split("body.opportunity_id", 1)[0].rsplit("if (", 1)[1]
    assert "opportunityId" in bind, "retarget this test -- the bind check moved"
    assert "startDate" not in bind and "starts_at" not in bind, (
        "binding a deal is bundled with the times, so it reads as a reschedule")
