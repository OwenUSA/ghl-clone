"""The route plan in the browser (2026-11-19): executed where it can be.

**Executed under node.** `lib/owenAi.ts` and `lib/dispatch.ts` are import-free, so the shipped
helpers run here: the plan link becomes a plan (and nothing else does), the answer's plan is
found from its text or from the step the log kept, the rain chip is a flag, the waiting repairs
are grouped, and the Settings page never sends back the days told in the chat.

**Asserted against source.** The pages are React, which this project has no runner for. Each
assertion names the failure it would catch.
"""
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
NODE = shutil.which("node")

node = pytest.mark.skipif(
    NODE is None, reason="node is not on PATH, so lib/*.ts cannot be executed. CI's backend job "
    "installs it so this file is never skipped there.")


def run_js(lib: str, body: str):
    script = textwrap.dedent("""
        import * as m from %s
        const out = (v) => console.log('@@' + JSON.stringify(v))
    """) % json.dumps((FRONTEND / "lib" / lib).as_uri()) + textwrap.dedent(body)
    proc = subprocess.run([NODE, "--experimental-strip-types", "--input-type=module", "-"],
                          input=script, capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@")]
    assert line, "the script printed no result:\n" + proc.stdout + proc.stderr
    return json.loads(line[-1][2:])


def _read(*parts: str) -> str:
    return (FRONTEND.joinpath(*parts)).read_text(encoding="utf-8")


@node
def test_only_the_plan_address_becomes_a_plan():
    got = run_js("owenAi.ts", """
        out([
          m.safeLink('/dispatch?tab=book&plan=57'),
          m.safeLink('/dispatch?tab=book&plan=57&x=1'),
          m.safeLink('javascript:alert(1)//dispatch?tab=book&plan=57'),
          m.safeLink('/dispatch?tab=book&plan=abc'),
          m.linkSpans('See [Plan #57 — map and list](/dispatch?tab=book&plan=57) now'),
        ])
    """)
    assert got[0] == {"kind": "plan", "plan": 57}
    assert got[1] is None and got[2] is None and got[3] is None   # no extra params, no scheme
    assert got[4][1] == {"kind": "plan", "text": "Plan #57 — map and list",
                         "href": "/dispatch?tab=book&plan=57", "plan": 57}


@node
def test_the_answers_plan_comes_from_its_last_link_or_from_the_kept_step():
    got = run_js("owenAi.ts", """
        out([
          m.lastPlanId('[a](/dispatch?tab=book&plan=3) then [b](/dispatch?tab=book&plan=4)'),
          m.lastPlanId('no link here', [{ tool: 'plan_routes',
            result: 'Plan #9 saved (DRAFT: nothing booked…' }]),
          m.lastPlanId('no link', [{ tool: 'job_dossier', result: 'Plan #9 saved' }]),
          m.lastPlanId(null, [{ tool: 'plan_routes', result: { not: 'text' } }]),
        ])
    """)
    assert got == [4, 9, None, None]


@node
def test_rain_is_a_flag_and_the_waiting_repairs_are_grouped():
    got = run_js("dispatch.ts", """
        out([
          m.rainLabel({ pop: 60, risk: true }),
          m.rainLabel({ pop: 60, risk: true, less_certain: true }),
          m.rainLabel({ pop: 20, risk: false }),
          m.rainLabel({ known: false }),
          m.visitNotes({ existing: false, agreed: 'Proposal DTR-1 accepted', one_day: true,
            tentative: true, call_first_why: 'Nobody has talked',
            weather: { pop: 70, risk: true } }),
          m.visitNotes({ existing: true, agreed: 'x' }),
          m.waitingGroups([{ waiting_on: 'customer', n: 1 }, { waiting_on: 'ahs', n: 2 },
            { waiting_on: 'customer', n: 3 }]).map(([k, rs]) => [k, rs.map((r) => r.n)]),
          m.forPageSave([{ name: 'Owen', max: 2, on_call: true, only: [], off: [], minutes: {} }]),
          m.crewDaysText({ on_call: true, only: [{ from: '2026-11-20', to: '2026-11-20', max: 1 }],
            off: [{ from: '2026-11-27', to: '2026-11-28' }] }),
        ])
    """)
    assert got[0] == "☂ 60%" and got[1] == "☂ 60% (less certain)"
    assert got[2] is None and got[3] is None
    assert got[4] == ["The only day the customer can", "Customer's OK: Proposal DTR-1 accepted",
                      "CALL FIRST: Nobody has talked", "Rain likely (70%) — a flag only"]
    assert got[5] == []                                    # a booked visit carries no notes
    assert got[6] == [["ahs", [2]], ["customer", [1, 3]]]
    assert got[7] == [{"name": "Owen", "max": 2}]          # the chat's keys never go back
    assert got[8] == "On call · works 2026-11-20 (max 1) · Off 2026-11-27 to 2026-11-28"


def test_the_pages_draw_the_plan_and_keep_the_chats_days():
    conv = _read("components", "owenAi", "Conversation.tsx")
    # The map and week under Owen AI's answer: without it the plan link is only a chip.
    assert "lastPlanId(m.content, steps)" in conv and "<PlanCard planId={plan} />" in conv
    md = _read("components", "owenAi", "Markdown.tsx")
    plan_chip = md[md.index("s.kind === 'plan'"):md.index("s.kind === 'link'")]
    assert "target=" not in plan_chip                      # same tab, the CRM's own page
    card = _read("components", "dispatch", "PlanCard.tsx")
    assert "<PlanExtras plan={plan} />" in card and "visitNotes(v)" in card
    week = _read("components", "dispatch", "WeekPlan.tsx")
    assert "<PlanExtras plan={plan} />" in week and "useState<DispatchPlanBoards>('active')" in week
    extras = _read("components", "dispatch", "PlanExtras.tsx")
    assert "Repairs waiting for the customer's OK" in extras and "w.note" in extras
    page = _read("pages", "DispatchPage.tsx")
    # A stale Settings page must never wipe the days told in the chat.
    assert "technicians: forPageSave(techs)" in page and "crewDaysText(t)" in page
