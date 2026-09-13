"""One GoHighLevel tab bar on every module, and GHL's pipeline dropdown.

**Executed.** `frontend/src/lib/pageTabs.ts` imports nothing, so node runs the shipped
code: the tab colours, the dropdown's rows, which roles see "+ New pipeline", and the
keyboard highlight that must never land on "All pipelines". The access test feeds the
REAL `GET /api/pipelines` answer for a user who is denied a pipeline into those rows,
and the create test builds its body with the Create pipeline modal's own `createBody`.

**Asserted against source.** The React screens, which this project has no runner for:
each page renders `<PageTabs>` with exactly the tab list it had before (the owner:
restyle only, keep every tab doing what it does today).
"""
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Pipeline, PipelinePermission, Role, Stage, User
from fastapi.testclient import TestClient
from sqlalchemy import func, select

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
PAGE_TABS_TS = FRONTEND / "lib" / "pageTabs.ts"
PIPELINES_TS = FRONTEND / "lib" / "pipelines.ts"
NODE = shutil.which("node")

node = pytest.mark.skipif(
    NODE is None,
    reason="node is not on PATH, so lib/pageTabs.ts cannot be executed. CI's backend "
           "job installs it precisely so this file is never skipped there.")


def _read(*parts: str) -> str:
    return FRONTEND.joinpath(*parts).read_text(encoding="utf-8")


def run_js(body: str):
    script = textwrap.dedent("""
        import * as f from %s
        import * as pl from %s
        const out = (v) => console.log('@@' + JSON.stringify(v))
    """) % (json.dumps(PAGE_TABS_TS.as_posix()), json.dumps(PIPELINES_TS.as_posix())) \
        + textwrap.dedent(body)
    proc = subprocess.run(
        [NODE, "--experimental-strip-types", "--input-type=module", "-"],
        input=script, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@")]
    assert line, "the script printed no result:\n" + proc.stdout + proc.stderr
    return json.loads(line[-1][2:])


ROWS = """
const pipes = [
  { id: 11, name: 'Marketing Pipeline' }, { id: 12, name: 'Dream Team Roofing AHS' },
  { id: 13, name: 'Dream Team Roofing Retail Repairs' }, { id: 14, name: 'Local Garage Door' },
]
"""


# ---------------- the shared tab bar ----------------

@node
def test_the_active_tab_is_blue_and_underlined_and_the_rest_are_ink():
    got = run_js("""
        out([f.tabLook(true, false), f.tabLook(false, false), f.tabLook(false, true)])
    """)
    assert got == [
        {"color": "rgb(56,160,219)", "borderBottom": "2px solid rgb(56,160,219)"},
        {"color": "rgb(71,84,103)", "borderBottom": "2px solid transparent"},
        {"color": "rgb(152,162,179)", "borderBottom": "2px solid transparent"},
    ]


def _tabs_block(page: str, title: str) -> str:
    source = _read("pages", page)
    assert source.count("<PageTabs ") == 1, "%s does not render the shared bar once" % page
    block = source.split('<PageTabs title="%s"' % title, 1)[1].split("/>", 1)[0]
    # No second, hand-drawn bar left behind.
    assert "'rgb(56,160,219)'" not in source, "%s still draws its own active blue" % page
    return block


def test_every_module_renders_the_one_shared_tab_bar():
    bar = _read("components", "PageTabs.tsx")
    assert 'role="tablist"' in bar and "tabLook(on, !!t.blocked)" in bar
    for page in ("OpportunitiesPage.tsx", "ContactsPage.tsx", "ConversationsPage.tsx",
                 "CalendarsPage.tsx"):
        assert "import { PageTabs } from '../components/PageTabs'" in _read("pages", page)


def test_opportunities_keeps_its_four_tabs_and_what_each_does():
    block = _tabs_block("OpportunitiesPage.tsx", "Opportunities")
    source = _read("pages", "OpportunitiesPage.tsx")
    assert "const TABS = ['Opportunities', 'Forecast', 'Pipelines', 'Bulk Actions']" in source
    assert "tabs={TABS.map((t) => ({" in block and "onSelect: () => setTab(t)" in block
    assert "active={tab}" in block
    assert "blocked: t === 'Forecast' && !canForecast ? 'Your role cannot view the forecast'" \
        in block


def test_contacts_keeps_its_five_tabs_as_the_labels_they_were():
    block = _tabs_block("ContactsPage.tsx", "Contacts")
    source = _read("pages", "ContactsPage.tsx")
    assert ("const TABS = ['Smart Lists', 'Bulk Actions', 'Custom Fields', 'Tasks', "
            "'Companies']") in source
    # Inert before, inert now: no handler was added, Smart Lists stays the lit one.
    assert "tabs={TABS.map((t) => ({ key: t, label: t }))}" in block
    assert "active={TABS[0]}" in block and "onSelect" not in block


def test_conversations_keeps_its_six_tabs_as_the_labels_they_were():
    block = _tabs_block("ConversationsPage.tsx", "Conversations")
    assert ("['Conversations', 'Manual Actions', 'Snippets', 'Trigger Links', 'Analytics', "
            "'Settings']") in block
    assert 'active="Conversations"' in block and "onSelect" not in block


def test_calendars_keeps_its_three_tabs_two_live_one_label():
    block = _tabs_block("CalendarsPage.tsx", "Calendars")
    assert "[['calendar', 'Calendar view'], ['list', 'Appointment list view']]" in block
    assert "onSelect: () => setTab(k)" in block and "active={tab}" in block
    assert "{ key: 'settings', label: 'Calendar settings' }" in block


def test_a_tab_with_nothing_to_do_is_not_rendered_as_a_button():
    bar = _read("components", "PageTabs.tsx")
    label = bar.split("if (!t.onSelect && !t.blocked) {", 1)[1].split("}\n", 1)[0]
    assert "<span" in label and "<button" not in label and "cursor: 'default'" in label


# ---------------- the pipeline dropdown: rows ----------------

@node
def test_the_list_is_label_then_pipelines_then_divider_then_new_with_a_check_on_one():
    got = run_js(ROWS + "out(f.pickerRows(pipes, 12, 'DISPATCHER'))")
    assert got[0] == {"kind": "label", "text": "All pipelines"}
    assert [(r["id"], r["selected"]) for r in got[1:5]] == [
        (11, False), (12, True), (13, False), (14, False)]
    assert got[5:] == [{"kind": "divider"}, {"kind": "new", "text": "New pipeline"}]


@node
def test_new_pipeline_is_offered_to_dispatcher_and_admin_but_not_a_tech():
    got = run_js(ROWS + """
        out(['ADMIN', 'DISPATCHER', 'TECH'].map((r) =>
          f.pickerRows(pipes, 11, r).map((x) => x.kind)))
    """)
    assert got[0] == got[1] == ["label"] + ["pipeline"] * 4 + ["divider", "new"]
    assert got[2] == ["label"] + ["pipeline"] * 4, "a TECH is offered + New pipeline"


@node
def test_all_pipelines_can_never_be_highlighted_or_chosen():
    got = run_js(ROWS + """
        const rows = f.pickerRows(pipes, 11, 'ADMIN')
        const up = []
        let h = 1
        for (let i = 0; i < 4; i++) { h = f.moveHighlight(rows, h, -1); up.push(h) }
        const down = []
        h = f.initialHighlight(rows)
        for (let i = 0; i < 8; i++) { h = f.moveHighlight(rows, h, 1); down.push(h) }
        out({ up, down, fresh: f.moveHighlight(rows, -1, 1), freshUp: f.moveHighlight(rows, -1, -1),
              label: f.choosable(rows[0]), divider: f.choosable(rows[5]),
              pipeline: f.choosable(rows[2]), create: f.choosable(rows[6]),
              initial: f.initialHighlight(rows) })
    """)
    assert got["up"] == [1, 1, 1, 1], "Up from the first pipeline moved onto the label"
    # 1 -> 2 -> 3 -> 4 -> (5 is the divider, skipped) 6 -> stays at the end.
    assert got["down"] == [2, 3, 4, 6, 6, 6, 6, 6]
    assert got["fresh"] == 1 and got["freshUp"] == 6
    assert got["label"] is False and got["divider"] is False
    assert got["pipeline"] is True and got["create"] is True
    assert got["initial"] == 1


def test_the_picker_only_acts_on_choosable_rows_and_closes_on_escape_and_outside():
    src = _read("components", "PipelinePicker.tsx")
    choose = src.split("const choose = (i: number) => {", 1)[1].split("\n  }\n", 1)[0]
    assert "if (!choosable(row)) return" in choose
    assert "onSelect(row.id)" in choose and "onNew()" in choose
    label = src.split("if (r.kind === 'label') {", 1)[1].split("}\n", 1)[0]
    assert "onClick" not in label, "All pipelines reacts to a click"
    keys = src.split("const onKey = ", 1)[1].split("\n  }\n", 1)[0]
    for key in ("'ArrowDown'", "'ArrowUp'", "'Enter'", "'Escape'"):
        assert key in keys
    assert "document.addEventListener('mousedown', onDown)" in src
    assert "!box.current.contains(e.target as Node)) setOpen(false)" in src
    assert 'className="min-w-0 flex-1 truncate"' in src, "long names do not truncate"


def test_the_page_feeds_the_picker_the_boards_own_pipeline_list():
    page = _read("pages", "OpportunitiesPage.tsx")
    assert "const pipelines = useQuery({ queryKey: ['pipelines'], queryFn: listPipelines })" \
        in page
    picker = page.split("<PipelinePicker", 1)[1].split("/>", 1)[0]
    assert "pipelines={pipelines.data ?? []}" in picker
    assert "role={user.role}" in picker
    assert "<select" not in page.split("{/* pipeline row */}", 1)[1].split(
        "{total} opportunities", 1)[0], "the native select is still there"


# ---------------- against the real API ----------------

@pytest.fixture()
def world():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    users = {
        "admin": User(email="a@x.test", name="Owen", role=Role.ADMIN),
        "insider": User(email="i@x.test", name="Insider", role=Role.DISPATCHER),
        "outsider": User(email="o@x.test", name="Outsider", role=Role.DISPATCHER),
        "tech": User(email="t@x.test", name="Tech", role=Role.TECH),
    }
    db.add_all(users.values())
    db.flush()
    open_p = Pipeline(name="Open", position=0)
    secret = Pipeline(name="Secret", position=1)
    db.add_all([open_p, secret])
    db.flush()
    db.add_all([Stage(pipeline_id=open_p.id, name="Lead", position=0),
                Stage(pipeline_id=secret.id, name="S1", position=0)])
    db.add(PipelinePermission(pipeline_id=secret.id, user_id=users["insider"].id))
    clients = {}
    for key, u in users.items():
        plain, token = mint_api_token(u, name="t-" + key)
        db.add(token)
        clients[key] = plain
    db.commit()
    ids = {"open": open_p.id, "secret": secret.id}
    db.close()
    out = {}
    for key, plain in clients.items():
        c = TestClient(app)
        c.headers["Authorization"] = "Bearer " + plain
        out[key] = c
    yield ids, out
    Base.metadata.drop_all(bind=engine)


def _rows_for(client, selected, role):
    pipes = client.get("/api/pipelines").json()
    return run_js("out(f.pickerRows(%s, %s, %s))" % (
        json.dumps(pipes), json.dumps(selected), json.dumps(role)))


@node
def test_the_dropdown_lists_only_the_pipelines_the_user_can_access(world):
    ids, c = world
    outsider = _rows_for(c["outsider"], ids["open"], "DISPATCHER")
    assert [r["name"] for r in outsider if r["kind"] == "pipeline"] == ["Open"]
    tech = _rows_for(c["tech"], ids["open"], "TECH")
    assert [r["name"] for r in tech if r["kind"] == "pipeline"] == ["Open"]
    assert all(r["kind"] != "new" for r in tech)
    insider = _rows_for(c["insider"], ids["secret"], "DISPATCHER")
    assert [(r["name"], r["selected"]) for r in insider if r["kind"] == "pipeline"] == [
        ("Open", False), ("Secret", True)]
    admin = _rows_for(c["admin"], ids["open"], "ADMIN")
    assert [r["name"] for r in admin if r["kind"] == "pipeline"] == ["Open", "Secret"]


@node
def test_creating_from_the_dropdown_selects_the_new_pipeline(world):
    ids, c = world
    body = run_js("""
        const d = pl.newDraft()
        d.name = 'Storm Season'
        out(pl.createBody(d))
    """)
    made = c["outsider"].post("/api/pipelines", json=body)
    assert made.status_code in (200, 201), made.text
    new_id = made.json()["id"]
    # What onSaved does: seed the row and select it. The refetched list must carry it
    # and the dropdown must tick it and nothing else.
    rows = _rows_for(c["outsider"], new_id, "DISPATCHER")
    picked = [r["name"] for r in rows if r["kind"] == "pipeline" and r["selected"]]
    assert picked == ["Storm Season"]
    page = _read("pages", "OpportunitiesPage.tsx")
    modal = page.split("{creatingPipeline && (", 1)[1].split("\n      )}\n", 1)[0]
    assert "<PipelineModal user={user} pipeline={null}" in modal
    saved = modal.split("onSaved={(p) => {", 1)[1]
    assert "setPipelineId(p.id)" in saved
    cancel = modal.split("onClose={", 1)[1].split("\n", 1)[0]
    assert cancel == "() => setCreatingPipeline(false)}", "Cancel does more than close"
    assert "onNew={() => setCreatingPipeline(true)}" in page


def test_a_tech_cannot_create_a_pipeline_and_nothing_is_written(world):
    _, c = world
    db = SessionLocal()
    before = db.scalar(select(func.count()).select_from(Pipeline))
    db.close()
    r = c["tech"].post("/api/pipelines", json={"name": "Nope", "stages": [{"name": "A"}]})
    assert r.status_code == 403
    db = SessionLocal()
    try:
        assert db.scalar(select(func.count()).select_from(Pipeline)) == before
        assert db.scalar(select(Pipeline).where(Pipeline.name == "Nope")) is None
    finally:
        db.close()
    names = [p["name"] for p in c["tech"].get("/api/pipelines").json()]
    assert "Nope" not in names
