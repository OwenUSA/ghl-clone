"""Retell voice agents in the browser (2026-10-06): executed where it can be.

`lib/aiAgents.ts` is import-free and runs under node here (sections per engine, the Retell-only
action, the six switches all off, the office-hours rules with no invented default). The screens
are asserted against source, each assertion naming the failure it would catch.
"""
# ruff: noqa: E501
import json

from tests.test_opportunity_card_ui import _read, node, run_js

js = json.dumps


@node
def test_a_retell_agent_drops_what_retell_owns_and_keeps_customer_information():
    got = run_js("aiAgents.ts", """
        out({ retell: m.sectionsFor('voice', 'retell').map((s) => s.key),
              owen: m.sectionsFor('voice', 'owen_voice').map((s) => s.key),
              plain: m.sectionsFor('voice').map((s) => s.key),
              text: m.sectionsFor('text', 'retell').map((s) => s.key) })
    """)
    for owned in ("persona", "rules", "knowledge", "advanced", "prompt"):
        assert owned not in got["retell"], owned
        assert owned in got["owen"], owned
    for kept in ("basics", "call", "customer", "actions", "versions"):
        assert kept in got["retell"] and kept in got["owen"], kept
    assert got["plain"] == got["owen"]
    assert "customer" not in got["text"]


@node
def test_request_change_is_offered_to_retell_only_and_every_switch_starts_off():
    got = run_js("aiAgents.ts", """
        const cat = [{name: 'end_call'}, {name: 'request_change'}]
        out({ retell: m.voiceActionsFor(cat, 'retell').map((a) => a.name),
              owen: m.voiceActionsFor(cat, '').map((a) => a.name),
              keys: m.CONTEXT_SOURCES.map((s) => s.key),
              empty: m.contextSources({}), partial: m.contextSources({context_sources: {texts: true, x: true}}),
              address: m.CONTEXT_SOURCES.find((s) => s.key === 'address').help,
              vars: m.RETELL_VARIABLES })
    """)
    assert got["retell"] == ["end_call", "request_change"] and got["owen"] == ["end_call"]
    assert got["keys"] == ["crm_card", "zuper_job", "call_summaries", "texts", "ai_calls", "address"]
    assert not any(got["empty"].values()) and set(got["empty"]) == set(got["keys"])
    assert got["partial"]["texts"] is True and "x" not in got["partial"]
    assert "Retell" in got["address"] and "never to read it aloud" in got["address"]
    assert got["vars"] == ["{{customer_brief}}", "{{customer_first_name}}", "{{customer_known}}"]


@node
def test_office_hours_are_never_invented_and_must_make_sense():
    got = run_js("aiAgents.ts", """
        out({ none: m.hoursProblems({}),
              backwards: m.hoursProblems({mon: [['17:00', '08:00']]}),
              overlap: m.hoursProblems({tue: [['08:00', '12:00'], ['11:00', '13:00']]}),
              ok: m.hoursProblems({mon: [['08:00', '12:00'], ['13:00', '17:00']]}),
              body: m.hoursBody({mon: [['08:00', '17:00']], tue: []}),
              spend: m.spendLine({today_usd: 21.5, daily_cap_usd: 25, used_pct: 86}),
              unknown: m.spendLine({today_usd: null, daily_cap_usd: 25, used_pct: null}) })
    """)
    assert got["none"] and "at least one day" in got["none"][0]
    assert "ends before it starts" in got["backwards"][0]
    assert "overlap" in got["overlap"][0]
    assert got["ok"] == []
    assert got["body"] == {"tz": "America/New_York", "days": {"mon": [["08:00", "17:00"]]}}
    assert got["spend"] == "$21.50 of $25.00 today (86%)"
    assert got["unknown"] == "—"


def test_the_builder_has_the_engine_the_retell_note_and_the_switches():
    builder = _read("components", "ai", "AgentBuilder.tsx")
    assert "aria-label=\"Engine\"" in builder, "the engine cannot be chosen"
    assert "Retell agent id" in builder, "a Retell agent's id cannot be entered"
    assert "RETELL_VARIABLES" in builder, "the prompt variables a Retell prompt needs are not shown"
    assert "Retell's dashboard" in builder, "it does not say where the prompt is edited"
    assert "CONTEXT_SOURCES.map" in builder, "the customer information switches are not drawn"
    assert "sectionsFor(agent.channel, draft.engine)" in builder, "a Retell agent shows the owen-voice sections"
    assert "voiceActionsFor(" in builder, "request_change would be offered to an owen-voice agent"


def test_the_phone_numbers_tab_confirms_a_hand_built_flow_and_invents_no_hours():
    tab = _read("components", "ai", "PhoneNumbersTab.tsx")
    assert "e.status === 409" in tab and "save.mutate(true)" in tab, (
        "a hand-built flow would be replaced without asking, or never")
    assert "hoursProblems(days)" in tab and "emptyDays()" in tab, "office hours must start empty"
    assert "unassignAiPhoneNumber" in tab and "SpendCard" in tab
    page = _read("pages", "AiAgentsPage.tsx")
    assert "<PhoneNumbersTab" in page


def test_the_call_shows_retells_summary_cost_and_version():
    record = _read("components", "AiCallRecord.tsx")
    assert "call.summary" in record and "costLabel(call.cost_cents)" in record
    assert "retell_agent_version" in record and "' · Retell'" in record
    assert "Answered by AI" in record


def test_a_live_ai_call_is_a_desktop_notification_once_with_the_bells_opt_in():
    live = _read("components", "LiveAgentCall.tsx")
    assert "freshCalls(seen.current" in live and "showCall(" in live
    alerts = _read("lib", "desktopAlerts.ts")
    assert "AI is on a call with" in alerts
    assert alerts.count("permission() !== 'granted'") >= 2, "the call notification ignores the opt-in"


@node
def test_a_call_is_announced_once_and_not_on_the_first_look():
    got = run_js("desktopAlerts.ts", """
        const seen = {primed: false, ids: new Set()}
        const a = m.freshCalls(seen, [{linkedid: '1'}])
        const b = m.freshCalls(seen, [{linkedid: '1'}, {linkedid: '2'}])
        const c = m.freshCalls(seen, [{linkedid: '2'}])
        out({a: a.map((x) => x.linkedid), b: b.map((x) => x.linkedid), c: c.map((x) => x.linkedid)})
    """)
    assert got == {"a": [], "b": ["2"], "c": []}
