"""The Pipelines tab, the pipeline modal, the tab bar and the Custom Fields move.

Two halves, the project's standing split (see test_custom_fields_ui.py):

**Executed.** `frontend/src/lib/pipelines.ts` and `lib/settingsSections.ts` import
nothing, so node runs the shipped code: search, paging, drag reorder, Move to
position, Copy link and its deep link, the modal's validation and bodies, the
board's colour modes, and which URL opens Settings > Custom Fields.

**Asserted against source.** The React screens, which this project has no runner
for. Each assertion names the failure it would catch.
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
PIPELINES_TS = FRONTEND / "lib" / "pipelines.ts"
SECTIONS_TS = FRONTEND / "lib" / "settingsSections.ts"
NODE = shutil.which("node")

node = pytest.mark.skipif(
    NODE is None,
    reason="node is not on PATH, so lib/pipelines.ts cannot be executed. CI's backend "
           "job installs it precisely so this file is never skipped there.")


def _read(*parts: str) -> str:
    return FRONTEND.joinpath(*parts).read_text(encoding="utf-8")


def run_js(body: str, module: Path = PIPELINES_TS):
    script = textwrap.dedent("""
        import * as f from %s
        const out = (v) => console.log('@@' + JSON.stringify(v))
    """) % json.dumps(module.as_posix()) + textwrap.dedent(body)
    proc = subprocess.run(
        [NODE, "--experimental-strip-types", "--input-type=module", "-"],
        input=script, capture_output=True, text=True,
        env={**os.environ, "TZ": "America/New_York"})
    assert proc.returncode == 0, proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@")]
    assert line, "the script printed no result:\n" + proc.stdout + proc.stderr
    return json.loads(line[-1][2:])


ROWS = """
const rows = [
  { id: 11, name: 'Marketing Pipeline' }, { id: 12, name: 'Dream Team Roofing AHS' },
  { id: 13, name: 'Dream Team Roofing Retail Repairs' }, { id: 14, name: 'Local Garage Door' },
]
"""


# ---------------- executed: the table ----------------

@node
def test_search_narrows_on_the_name_whatever_the_case():
    got = run_js(ROWS + """
        out([f.searchPipelines(rows, '  dream TEAM ').map((r) => r.id),
             f.searchPipelines(rows, '').length, f.searchPipelines(rows, 'zzz').length])
    """)
    assert got == [[12, 13], 4, 0]


@node
def test_paging_reports_the_range_and_clamps_a_page_that_no_longer_exists():
    got = run_js("""
        const rows = Array.from({ length: 23 }, (_, i) => i + 1)
        const a = f.paginate(rows, 2, 10)
        const b = f.paginate(rows, 9, 10)
        const c = f.paginate([], 1, 20)
        out([[a.from, a.to, a.total, a.page, a.pages, a.rows],
             [b.from, b.to, b.page], [c.from, c.to, c.pages, c.rows]])
    """)
    assert got == [[11, 20, 23, 2, 3, list(range(11, 21))], [21, 23, 3], [0, 0, 1, []]]


@node
def test_a_drag_produces_the_whole_new_order_and_a_drop_in_place_sends_nothing():
    got = run_js("""
        const ids = [11, 12, 13, 14]
        out([f.orderAfterDrag(ids, 14, 11), f.orderAfterDrag(ids, 11, 13),
             f.orderAfterDrag(ids, 12, 12), f.orderAfterDrag(ids, 12, null),
             f.orderAfterDrag(ids, 99, 11), ids])
    """)
    assert got == [[14, 11, 12, 13], [12, 13, 11, 14], None, None, None, [11, 12, 13, 14]]


@node
def test_move_to_position_is_one_based_and_clamps():
    got = run_js("""
        const ids = [11, 12, 13, 14]
        out([f.orderForPosition(ids, 14, 1), f.orderForPosition(ids, 11, 3),
             f.orderForPosition(ids, 11, 99), f.orderForPosition(ids, 12, 2),
             f.orderForPosition(ids, 12, 0)])
    """)
    assert got == [[14, 11, 12, 13], [12, 13, 11, 14], [12, 13, 14, 11], None,
                   [12, 11, 13, 14]]


@node
def test_updated_on_is_the_date_and_the_time_the_list_shows():
    got = run_js("""
        out([f.updatedOn('2026-07-14T18:46:00Z'), f.updatedOn(null), f.updatedOn('nope')])
    """)
    assert got == [{"date": "Jul 14, 2026", "time": "2:46 PM"}, None, None]


# ---------------- executed: Copy link and the deep link ----------------

@node
def test_copy_link_round_trips_through_a_fresh_page_load():
    got = run_js("""
        const url = new URL(f.pipelineLink('https://crm.dreamteamroofingfl.com/', 12))
        out([url.href, url.pathname, f.pipelineFromSearch(url.search),
             f.pipelineFromSearch('?pipeline=0'), f.pipelineFromSearch('?pipeline=12abc'),
             f.pipelineFromSearch('?pipeline=-3'), f.pipelineFromSearch('')])
    """)
    assert got == ["https://crm.dreamteamroofingfl.com/opportunities?pipeline=12",
                   "/opportunities", 12, None, None, None, None]


@node
def test_the_deep_link_path_opens_the_opportunities_view_and_settings_sections_resolve():
    got = run_js("""
        out([f.viewFromPath('/opportunities'), f.viewFromPath('/opportunities/'),
             f.viewFromPath('/settings/custom-fields'), f.viewFromPath('/'),
             f.sectionFromPath('/settings/custom-fields'),
             f.sectionFromPath('/settings/CUSTOM-FIELDS/'),
             f.sectionFromPath('/settings'), f.sectionFromPath('/settings/nope'),
             f.SETTINGS_SECTIONS.map((s) => s.label)])
    """, module=SECTIONS_TS)
    assert got == ["opportunities", "opportunities", "settings", None, "custom-fields",
                   "custom-fields", "account", "account",
                   ["My account", "Custom Fields", "CompanyCam", "Zuper", "My Staff",
                    "AI Connections", "Automations"]]


def test_the_app_opens_the_view_a_deep_link_names():
    app = _read("App.tsx")
    initial = app.split("function initialView()", 1)[1].split("\n}\n", 1)[0]
    assert "viewFromPath(path) ?? DEFAULT_VIEW" in initial
    page = _read("pages", "OpportunitiesPage.tsx")
    initial_pipeline = ("useState<number | null>(\n"
                        "    () => pipelineFromSearch(window.location.search))")
    assert initial_pipeline in page, (
        "a fresh load of the copied link does not select its pipeline")
    panel = _read("components", "PipelinesPanel.tsx")
    assert "pipelineLink(window.location.origin, p.id)" in panel
    assert "navigator.clipboard.writeText(url)" in panel and "window.prompt(" in panel


# ---------------- executed: the board's colour modes ----------------

@node
def test_each_display_colour_mode_draws_the_stage_header_differently():
    got = run_js("""
        out([f.stageHeader('none', '#2E90FA', 0), f.stageHeader(undefined, '#2E90FA', 0),
             f.stageHeader('dot', '#12B76A', 1), f.stageHeader('tint', '#F04438', 4),
             f.stageHeader('dot', null, 3).dot, f.tint('#000000', 0.5), f.tint('junk', 1)])
    """)
    white = {"dot": None, "background": "#fff", "border": "rgb(234,236,240)"}
    assert got[0] == white and got[1] == white
    assert got[2] == {"dot": "#12B76A", "background": "#fff", "border": "rgb(234,236,240)"}
    assert got[3] == {"dot": None, "background": "rgba(240,68,56,0.12)",
                      "border": "rgba(240,68,56,0.35)"}
    assert got[4] == "#7A5AF8", "a stage with no stored colour is drawn without one"
    assert got[5:] == ["rgba(0,0,0,0.5)", "transparent"]


def test_the_palette_is_the_one_the_server_assigns():
    from app.main import STAGE_PALETTE
    ts = re.findall(r"'(#[0-9A-F]{6})'", _read("lib", "pipelines.ts").split(
        "export const STAGE_PALETTE", 1)[1].split("]", 1)[0])
    assert tuple(ts) == STAGE_PALETTE


def test_the_board_header_obeys_the_pipelines_colour_mode():
    page = _read("pages", "OpportunitiesPage.tsx")
    column = page.split("function StageColumn(", 1)[1].split("<SortableContext", 1)[0]
    assert "const look = stageHeader(colorMode, stage.color, index)" in column
    assert "backgroundColor: look.background" in column
    assert "border: '1px solid ' + look.border" in column
    assert "{look.dot && (" in column, "Colored dot draws no dot"
    board = page.split("{pipeline?.stages.map((s, i) => (", 1)[1].split("/>", 1)[0]
    assert "colorMode={(pipeline as { color_mode?: ColorMode }).color_mode}" in board
    assert "index={i}" in board


# ---------------- executed: the modal ----------------

DRAFT = """
const stored = { id: 7, name: 'AHS', position: 0, color_mode: 'dot',
  use_opportunity_probability: true, updated_at: null, stages: [
    { id: 3, name: 'Call Back', position: 1, count: 0, value_cents: 0, color: null,
      probability: null, show_in_funnel: true, show_in_pie: false },
    { id: 2, name: 'New Lead', position: 0, count: 5, value_cents: 0, color: '#123456',
      probability: 20, show_in_funnel: false, show_in_pie: true },
    { id: 4, name: 'Call Back', position: 2, count: 1, value_cents: 0, color: null,
      probability: 70, show_in_funnel: true, show_in_pie: true },
  ] }
"""


@node
def test_the_create_modal_opens_with_ghls_four_starter_stages():
    got = run_js("""
        const d = f.newDraft()
        out([d.name, d.color_mode, d.use_opportunity_probability,
             d.stages.map((s) => [s.id, s.name, s.probability, s.color,
                                  s.show_in_funnel, s.show_in_pie]),
             f.validateDraft(d, []).ok, f.validateDraft(d, []).problems.name])
    """)
    assert got[:3] == ["", "none", False]
    assert got[3] == [[None, "New Lead", "20", "#2E90FA", True, True],
                      [None, "Contacted", "40", "#12B76A", True, True],
                      [None, "Proposal Sent", "60", "#F79009", True, True],
                      [None, "Closed", "80", "#7A5AF8", True, True]]
    assert got[4] is False and got[5] == "Pipeline name is required", (
        "Create is enabled with no name")


@node
def test_the_modal_refuses_a_taken_name_in_any_case_and_a_bad_probability():
    got = run_js(DRAFT + """
        const d = f.draftFrom(stored)
        const v = (patch, names = ['Retail']) => f.validateDraft({ ...d, ...patch }, names)
        const bad = (p) => v({
          stages: d.stages.map((s, i) => i === 0 ? { ...s, probability: p } : s) })
        out([v({}).ok, v({ name: ' retail ' }).problems.name, v({ name: '' }).ok,
             bad('101').ok, bad('7.5').ok, bad('100').ok, bad('').ok,
             v({ stages: [] }).problems.general,
             v({ stages: d.stages.map((s) => ({ ...s, name: 'Same' })) }).ok,
             v({ stages: d.stages.map((s, i) => i === 1 ? { ...s, name: '  ' } : s) }).ok])
    """)
    assert got == [True, "A pipeline with this name already exists", False, False, False, True,
                   True, "A pipeline needs at least one stage", True, False]


@node
def test_edit_populates_in_stage_order_and_update_sends_the_whole_list_with_moves():
    got = run_js(DRAFT + """
        const d = f.draftFrom(stored)
        const removed = f.removeStage(f.removeStage(d, 'stage-2', 4), 'stage-3')
        const body = f.updateBody({ ...removed, name: ' AHS 2 ' })
        const made = f.createBody(f.newDraft())
        out([d.stages.map((s) => [s.id, s.name, s.probability, s.color]),
             [d.color_mode, d.use_opportunity_probability],
             body, f.removedStages(stored, removed).map((s) => s.id),
             made.stages.every((s) => !('id' in JSON.parse(JSON.stringify(s)))),
             f.removeStage(f.removeStage(d, 'stage-2', 4), 'stage-4').moves])
    """)
    assert got[0] == [[2, "New Lead", "20", "#123456"], [3, "Call Back", "", "#12B76A"],
                      [4, "Call Back", "70", "#F79009"]]
    assert got[1] == ["dot", True]
    assert got[2] == {"name": "AHS 2", "use_opportunity_probability": True, "color_mode": "dot",
                      "stages": [{"id": 4, "name": "Call Back", "color": "#F79009",
                                  "probability": 70, "show_in_funnel": True,
                                  "show_in_pie": True}],
                      "stage_moves": {"2": 4}}
    assert got[3] == [3, 2]
    assert got[4] is True, "a new pipeline's stages were sent with ids"
    assert got[5] == {}, "a move still points at a stage that is itself being removed"


# ---------------- the tab bar ----------------

def test_the_tab_bar_is_exactly_ghls_four():
    page = _read("pages", "OpportunitiesPage.tsx")
    tabs = page.split("const TABS = [", 1)[1].split("]", 1)[0]
    assert re.findall(r"'([^']+)'", tabs) == ["Opportunities", "Forecast", "Pipelines",
                                              "Bulk Actions"]
    assert "Custom fields" not in page.split("const TABS", 1)[1]
    assert "CustomFieldsPanel" not in page, "the Custom fields screen is still on this page"


def test_the_active_tab_is_blue_and_underlined_like_ghl():
    # The bar is the shared PageTabs since 2026-09-13; the colours themselves are
    # executed in test_page_tabs.py.
    page = _read("pages", "OpportunitiesPage.tsx")
    assert '<PageTabs title="Opportunities" label="Opportunities views" active={tab}' in page
    row = _read("components", "PageTabs.tsx")
    assert 'role="tab"' in row and "aria-selected={on}" in row
    assert "...tabLook(on, !!t.blocked)" in row
    assert "fontSize: 14" in row and "fontWeight: 500" in row


def test_the_pipelines_tab_has_its_own_header_instead_of_the_board_row():
    page = _read("pages", "OpportunitiesPage.tsx")
    row = page.split("{/* pipeline row */}", 1)[1].split("<PipelinePicker", 1)[0]
    assert "display: tab === 'Pipelines' ? 'none' : undefined" in row
    panel = _read("components", "PipelinesPanel.tsx")
    for text in ("Pipelines</div>",
                 "Use pipelines to track opportunities and sales progress across stages.",
                 "Create pipeline", 'placeholder="Search"', "Pipeline name", "Total stages",
                 "Updated on", "Actions", "Rows per page", "Previous", "Next",
                 "Page {slice.page} of"):
        assert text in panel, "the Pipelines screen is missing %r" % text


# ---------------- Custom Fields lives in Settings now ----------------

def test_custom_fields_is_reachable_from_settings():
    settings = _read("pages", "SettingsPage.tsx")
    assert "import { CustomFieldsPanel } from '../components/CustomFieldsPanel'" in settings
    assert "<CustomFieldsPanel user={user} />" in settings
    branch = settings.split("{section === 'custom-fields'", 1)[1].split(": <AccountSettings", 1)[0]
    assert "<CustomFieldsPanel user={user} />" in branch
    assert "SETTINGS_SECTIONS.map((s) => (" in settings
    assert "onClick={() => choose(s.key)}" in settings
    assert "sectionFromPath(window.location.pathname)" in settings
    app = _read("App.tsx")
    assert "<SettingsPage user={user} />" in app
    sidebar = _read("components", "Sidebar.tsx")
    assert "settings" in sidebar.lower()


def test_the_settings_account_section_still_holds_tokens_and_password():
    settings = _read("pages", "SettingsPage.tsx")
    account = settings.split("function AccountSettings()", 1)[1]
    assert "API tokens" in account and "<PasswordPanel />" in account


# ---------------- the modal's controls are all there and all wired ----------------

def test_every_control_in_the_modal_is_wired_to_the_draft():
    modal = _read("components", "PipelineModal.tsx")
    for needle in (
            'Pipeline name <span style={{ color: \'rgb(217,45,32)\' }}>*</span>',
            "Use a unique, descriptive name so you can find this pipeline later",
            'role="switch" aria-checked={draft.use_opportunity_probability}',
            "set({ use_opportunity_probability: !draft.use_opportunity_probability })",
            'role="radiogroup" aria-label="Pipeline display colors"',
            "onClick={() => set({ color_mode: mode })}",
            "'Default (no color)'", "'Colored dot'", "'Background tint'",
            "Pipeline stages ({draft.stages.length})", "Add stage",
            "Show in reports", "Probability (%)",
            "onChange({ show_in_funnel: !stage.show_in_funnel })",
            "onChange({ show_in_pie: !stage.show_in_pie })",
            "onChange({ color: c })", "onDragEnd={onDragEnd}",
            "disabled={!ok || save.isPending}", "editing ? 'Update' : 'Create'",
            "updatePipeline(pipeline.id, updateBody(draft))",
            "createPipelineWithSettings(createBody(draft))"):
        assert needle in modal, "the modal is missing %r" % needle


def test_the_modal_shows_a_refused_save_instead_of_closing():
    modal = _read("components", "PipelineModal.tsx")
    assert "onError: (e: Error) => setError(e.message)" in modal
    assert 'role="alert"' in modal


def test_manage_permissions_says_nobody_means_everyone_and_admins_always():
    panel = _read("components", "PipelinesPanel.tsx")
    dialog = panel.split("function PermissionsDialog", 1)[1].split(
        "function MoveToPositionDialog", 1)[0]
    assert "With nobody selected" in dialog and "Admins always can" in dialog
    assert "checked={admin || picked.has(u.id)} disabled={admin}" in dialog
    assert "setPipelinePermissions(pipeline.id, [...picked])" in dialog


def test_the_forecast_labels_which_probability_weighted_each_stage():
    panel = _read("components", "ForecastPanel.tsx")
    assert "s.weighting === 'opportunity'" in panel and "s.weighting === 'stage'" in panel
    assert "`${s.probability}%`" in panel
    assert "use_opportunity_probability" in panel


def test_new_api_helpers_are_appended_at_the_end_of_api_ts():
    api = _read("lib", "api.ts")
    tail = api.split("export const reorderCustomFields", 1)[1]
    for helper in ("listPipelineRows", "createPipelineWithSettings", "updatePipeline",
                   "duplicatePipeline", "reorderPipelines", "getPipelinePermissions",
                   "setPipelinePermissions", "deletePipelineMovingDeals"):
        assert "export const %s" % helper in tail, "%s is not appended at the end" % helper
