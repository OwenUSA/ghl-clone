"""AI Agents in the browser (2026-09-15): executed where it can be.

**Executed under node.** `lib/aiAgents.ts` is import-free, so the shipped code that decides
who sees the module and which tabs, which modes a role may choose, how an unknown cost is
written, which files may be uploaded, what a connection form needs and what a Try-it
"Would" card says runs here for real.

**Asserted against source.** The screens are React, which this project has no runner for;
each assertion names the failure it would catch. The screens were also driven in a real
headless Chromium against a throwaway database — `tests/browser_ai_agents.py`.
"""
# The JS executed under node is written inline, as the other *_ui.py files do, and reads
# better unwrapped; the strings it compares are the screen's own typography (dashes, quotes).
# ruff: noqa: E501, RUF001
import json
import re

from tests.test_opportunity_card_ui import FRONTEND, _read, node, run_js

js = json.dumps

OWEN = {"role": "ADMIN", "only_assigned_data": True}      # ignored for an admin
DANA = {"role": "DISPATCHER", "only_assigned_data": False}
RITA = {"role": "DISPATCHER", "only_assigned_data": True}
TESS = {"role": "TECH", "only_assigned_data": False}


@node
def test_who_opens_the_module_and_which_tabs_they_get():
    got = run_js("aiAgents.ts", """
        const us = %s
        out({ open: us.map(m.canOpenAiAgents), tabs: us.map((u) => m.aiTabs(u).map((t) => t.label)),
              none: [m.canOpenAiAgents(null), m.aiTabs(undefined)] })
    """ % js([OWEN, DANA, RITA, TESS]))
    assert got["open"] == [True, True, False, False]
    assert got["tabs"][0] == ["Agents", "Knowledge Base", "Templates", "Agent Logs"]
    assert got["tabs"][1] == ["Agents", "Knowledge Base", "Templates"], (
        "a dispatcher must not be offered Agent Logs — logs are ADMIN-only")
    assert got["tabs"][2] == [] and got["tabs"][3] == []
    assert got["none"] == [False, []]


@node
def test_a_dispatcher_may_only_switch_an_agent_off_and_auto_pilot_asks_first():
    got = run_js("aiAgents.ts", """
        out({ modes: [m.modesFor(%s), m.modesFor(%s), m.modesFor(%s)],
              labels: ['off', 'suggest', 'auto'].map(m.modeLabel),
              confirm: [m.needsConfirm('off', 'auto'), m.needsConfirm('suggest', 'auto'),
                        m.needsConfirm('auto', 'auto'), m.needsConfirm('auto', 'off'),
                        m.needsConfirm('off', 'suggest')] })
    """ % (js(OWEN), js(DANA), js(TESS)))
    assert got["modes"] == [["off", "suggest", "auto"], ["off"], []]
    assert got["labels"] == ["Off", "Suggest", "Auto-pilot"]
    assert got["confirm"] == [True, True, False, False, False]


@node
def test_an_unknown_cost_is_a_dash_never_zero_dollars():
    got = run_js("aiAgents.ts", """
        out([m.formatCost(null), m.formatCost(undefined), m.formatCost(''), m.formatCost('0.000000'),
             m.formatCost('0.001825'), m.formatCost('12.5'),
             m.formatLatency(null), m.formatLatency(850), m.formatLatency(2300),
             m.formatTokens({input: 600, output: 50, cache_write: 50, cache_read: 0}),
             m.formatTokens(null)])
    """)
    assert got[:3] == ["—", "—", "—"], "an unknown price must not read as free"
    assert got[3] == "$0.00" and got[4] == "$0.0018" and got[5] == "$12.50"
    assert got[6:9] == ["—", "850 ms", "2.3 s"]
    assert got[9] == "600 in · 50 out · 50 cache" and got[10] == "—"


@node
def test_only_pdf_and_docx_up_to_ten_megabytes_are_offered_for_upload():
    got = run_js("aiAgents.ts", """
        out([m.fileProblem('Warranty.PDF', 1000), m.fileProblem('faq.docx', 10 * 1024 * 1024),
             m.fileProblem('faq.docx', 10 * 1024 * 1024 + 1), m.fileProblem('notes.txt', 10),
             m.fileProblem('scan.pdf', 0), m.MAX_FILE_BYTES])
    """)
    assert got[0] is None and got[1] is None
    assert "10 MB" in got[2]
    assert "PDF and Word" in got[3]
    assert "empty" in got[4]
    assert got[5] == 10 * 1024 * 1024


@node
def test_the_connection_form_needs_a_base_url_only_for_a_compatible_server():
    got = run_js("aiAgents.ts", """
        const base = {name: 'Claude', provider: 'anthropic', base_url: 'https://ignored', api_key: 'sk-x',
                      default_model: 'claude-sonnet-5', price_input: '', price_output: ''}
        out({
          anthropic: m.connectionProblems(base, false),
          body: m.connectionBody(base, false),
          compatible: m.connectionProblems({...base, provider: 'openai_compatible', base_url: ''}, false),
          badUrl: m.connectionProblems({...base, provider: 'openai_compatible', base_url: 'ftp://x'}, false),
          compatibleBody: m.connectionBody({...base, provider: 'openai_compatible', base_url: ' https://llm.local/v1 '}, false),
          noKeyNew: m.connectionProblems({...base, api_key: ''}, false),
          noKeyEdit: m.connectionProblems({...base, api_key: ''}, true),
          editBody: m.connectionBody({...base, api_key: '', price_input: '2.50', price_output: '10'}, true),
          badPrice: m.connectionProblems({...base, price_input: 'two'}, false),
          known: [m.knownPrice({'claude-sonnet-5': {input: '2.000000', output: '10.000000'}}, ' claude-sonnet-5 '),
                  m.knownPrice({}, 'my-local-model')],
        })
    """)
    assert got["anthropic"] == []
    assert got["body"]["base_url"] is None, "a base URL must not be sent for Anthropic"
    assert got["body"]["price_input"] is None, "a blank price is unknown, not zero"
    assert got["body"]["provider"] == "anthropic" and got["body"]["api_key"] == "sk-x"
    assert any("base URL" in p for p in got["compatible"])
    assert any("https://" in p for p in got["badUrl"])
    assert got["compatibleBody"]["base_url"] == "https://llm.local/v1"
    assert any("API key" in p for p in got["noKeyNew"]) and got["noKeyEdit"] == []
    assert "api_key" not in got["editBody"], "a blank key on an edit must keep the stored one"
    assert "provider" not in got["editBody"]
    assert got["editBody"]["price_input"] == "2.50"
    assert any("price" in p for p in got["badPrice"])
    assert got["known"] == [{"input": "2", "output": "10"}, None]


@node
def test_try_it_cards_triggers_schedules_and_list_editing():
    got = run_js("aiAgents.ts", """
        out({
          would: [m.wouldText({summary: 'Move “Roof” to stage “Inspection”', label: 'Move stage'}),
                  m.wouldText({label: 'Send text'})],
          triggers: [m.triggerSummary({type: 'inbound_text', minutes: 15}),
                     m.triggerSummary({type: 'stage_entered', stage_id: 7}, (id) => id === 7 ? 'AHS › Inspection' : undefined),
                     m.triggerSummary({type: 'manual'}), m.newTrigger('inbound_text'), m.newTrigger('manual')],
          schedule: [m.scheduleSummary({enabled: false}),
                     m.scheduleSummary({enabled: true, timezone: 'America/New_York', days: ['mon','tue','wed','thu','fri'], start: '08:00', end: '18:00'}),
                     m.scheduleSummary({enabled: true, days: ['sat'], start: '09:30', end: '12:00'}),
                     m.scheduleSummary({enabled: true, days: [], start: '09:00', end: '10:00'})],
          lists: [m.setItem(['a','b'], 1, 'c'), m.addItem(['a']), m.removeItem(['a','b','c'], 1),
                  m.moveItem(['a','b','c'], 0, -1), m.moveItem(['a','b','c'], 2, -1),
                  m.toggle(['search_knowledge'], 'get_context', ['get_context', 'search_knowledge', 'send_text']),
                  m.toggle(['get_context', 'send_text'], 'send_text')],
          save: m.draftForSave({goals: [' Book ', ''], rules_do: [''], rules_dont: ['No prices']}),
          outcomes: m.outcomeRows({skipped: 2, completed: 5, error: 0}),
          bars: m.bars([{d: 'a', n: 2}, {d: 'b', n: 4}, {d: 'c', n: 0}], (r) => r.d, (r) => r.n).map((b) => b.pct),
          empty: m.bars([{d: 'a', n: 0}], (r) => r.d, (r) => r.n).map((b) => b.pct),
        })
    """)
    assert got["would"] == ["Would: Move “Roof” to stage “Inspection”", "Would: Send text"]
    assert got["triggers"][:3] == ["Inbound text unanswered for 15 min",
                                   "Opportunity enters stage: AHS › Inspection", "Manual"]
    assert got["triggers"][3] == {"type": "inbound_text", "minutes": 10}
    assert got["triggers"][4] == {"type": "manual"}
    assert got["schedule"] == ["Any time", "Mon–Fri, 8:00 AM – 6:00 PM Eastern",
                               "Sat, 9:30 AM – 12:00 PM Eastern", "Never (no days selected)"]
    assert got["lists"] == [["a", "c"], ["a", ""], ["a", "c"], ["a", "b", "c"], ["a", "c", "b"],
                            ["get_context", "search_knowledge"], ["get_context"]]
    assert got["save"] == {"goals": ["Book"], "rules_do": [], "rules_dont": ["No prices"]}
    assert [r["key"] for r in got["outcomes"]] == ["completed", "skipped"]
    assert got["bars"] == [50, 100, 0] and got["empty"] == [0]


# ---------------- asserted against source ----------------

def test_the_page_uses_the_shared_tab_bar_and_draws_logs_only_through_ai_tabs():
    page = _read("pages", "AiAgentsPage.tsx")
    assert "<PageTabs" in page and "aiTabs(user)" in page, "the module does not use the shared tab bar"
    assert "if (!canOpenAiAgents(user)) return null" in page
    logs = _read("components", "ai", "LogsTab.tsx")
    assert "if (!isAiAdmin(user)) return null" in logs, "Agent Logs renders for a non-admin"


def test_voice_is_never_offered_as_a_channel_or_its_actions_as_options():
    """Phase 3: a Voice agent cannot be created, so no control offers it — not even disabled."""
    for parts in (("components", "ai", "AgentsTab.tsx"), ("components", "ai", "AgentBuilder.tsx")):
        src = _read(*parts)
        assert "<option value=\"voice\"" not in src and "'voice'" not in src
    builder = _read("components", "ai", "AgentBuilder.tsx")
    assert "a.channels.includes('text')" in builder, "voice-only actions would be offered"
    api = _read("lib", "api.ts")
    assert "{ ...body, channel: 'text' }" in api


def test_a_dispatcher_gets_text_not_disabled_controls_in_the_builder():
    src = _read("components", "ai", "AgentBuilder.tsx")
    assert "const admin = isAiAdmin(user) && agent.can_edit" in src
    assert "<TryItPanel" in src and "{admin && <TryItPanel" in src, "Try-it is drawn for a dispatcher"
    assert "disabled={!admin}" not in src, "a dispatcher is shown decorative disabled inputs"
    assert src.count("<ReadText>") >= 8


def test_the_suggestion_panel_and_bell_are_gated_and_mounted_where_expected():
    panel = _read("components", "AiSuggestions.tsx")
    assert "if (!allowed) return null" in panel
    assert "if (compact && runnable.length === 0 && pending.length === 0) return null" in panel, (
        "the contact panel would grow a section that can do nothing")
    contact = _read("components", "ContactDetailsPanel.tsx")
    assert "<AiAgentPanel user={user} contactId={c.id} compact />" in contact
    modal = _read("components", "OpportunityDetail.tsx")
    assert "canOpenAiAgents(user) && (" in modal and 'label="AI agent"' in modal
    bell = _read("components", "AiAlertBell.tsx")
    assert "if (!allowed) return null" in bell and "refetchInterval: 60000" in bell
    app = _read("App.tsx")
    assert "<AiAlertBell user={user} />" in app
    settings = _read("pages", "SettingsPage.tsx")
    assert "section === 'ai-connections' && user.role === 'ADMIN' ? <AiConnectionsSettings />" in settings
    sections = _read("lib", "settingsSections.ts")
    assert "'ai-connections'" in sections.split("ADMIN_SECTIONS", 1)[1]


def test_the_api_key_is_write_only_in_the_connection_form():
    src = _read("components", "AiConnectionsSettings.tsx")
    assert 'type="password"' in src and 'autoComplete="off"' in src
    assert "api_key: ''" in src, "an edit prefilled the stored key"
    assert "connection.api_key" in src   # only the masked "•••• last4" the API returns


_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]")


def test_no_emoji_in_the_new_screens():
    files = [*(FRONTEND / "components" / "ai").glob("*.tsx"),
             FRONTEND / "pages" / "AiAgentsPage.tsx", FRONTEND / "components" / "AiSuggestions.tsx",
             FRONTEND / "components" / "AiAlertBell.tsx", FRONTEND / "components" / "AiConnectionsSettings.tsx",
             FRONTEND / "lib" / "aiAgents.ts"]
    for f in files:
        hits = _EMOJI.findall(f.read_text(encoding="utf-8"))
        assert not hits, "%s uses emoji %r — icons come from the outline set" % (f.name, hits)
