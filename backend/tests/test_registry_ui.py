"""The agent registry in the browser (2026-10-21): `lib/registry.ts` executed under node, and the
screens asserted against source (no React runner here; each assertion names what it catches)."""
import json

from tests.test_opportunity_card_ui import _read, node, run_js

js = json.dumps
ROLES = [{"role": 1, "name": "Phone answering & speed to lead"},
         {"role": 2, "name": "Customer service & follow-up"}]


@node
def test_entries_sit_under_their_role_and_an_empty_role_still_shows():
    entries = [{"key": "b", "name": "Zed", "role": 1}, {"key": "a", "name": "Anisa", "role": 1},
               {"key": "c", "name": "Loose", "role": None}, {"key": "d", "name": "Odd", "role": 7}]
    got = run_js("registry.ts", """
        out(m.groupByRole(%s, %s).map((g) => [g.role, g.name, g.entries.map((e) => e.name)]))
    """ % (js(entries), js(ROLES)))
    assert got == [[1, "Phone answering & speed to lead", ["Anisa", "Zed"]],
                   [2, "Customer service & follow-up", []],
                   [None, "Not assigned to a role", ["Loose", "Odd"]]]


@node
def test_only_more_freedom_asks_for_a_reason_and_test_agents_hide_by_default():
    rows = [{"kind": "agent", "is_test": False, "archived": False, "n": 1},
            {"kind": "agent", "is_test": True, "archived": False, "n": 2},
            {"kind": "automation", "is_test": False, "archived": False, "n": 3},
            {"kind": "agent", "is_test": False, "archived": True, "n": 4}]
    got = run_js("registry.ts", """
        const rows = %s
        out({ promo: [m.isPromotion('never', 'shadow'), m.isPromotion('shadow', 'auto'),
                      m.isPromotion('auto', 'shadow'), m.isPromotion('auto', 'never'),
                      m.isPromotion('approve', 'approve')],
              all: m.visible(rows, 'all', false).map((r) => r.n),
              tests: m.visible(rows, 'agents', true).map((r) => r.n),
              autos: m.visible(rows, 'automations', false).map((r) => r.n),
              labels: m.LEVELS.map(m.levelLabel),
              spend: [m.spendLine('$1.20', '$5.00', false), m.spendLine(null, null, false),
                      m.spendLine('$1', null, true)] })
    """ % js(rows))
    assert got["promo"] == [True, True, False, False, False]
    assert got["all"] == [1, 3] and got["tests"] == [1, 2] and got["autos"] == [3]
    assert got["labels"] == ["Never", "Shadow", "Approve each", "Automatic"]
    assert got["spend"][0] == "$1.20 of $5.00 today" and got["spend"][1] == "$0.00 today"
    assert got["spend"][2].startswith("Spend unknown")


def test_the_screens_use_the_rules_and_never_hard_code_a_level_change():
    ov = _read("components", "ai", "OverviewTab.tsx")
    assert "isPromotion(a.level, to)" in ov, "a promotion must open the reason + PROMOTE dialog"
    assert "a.choices.length > 0" in ov, "an action with no choices must not offer a select"
    assert "isAiAdmin(user)" in ov, "only an admin is offered any change"
    page = _read("pages", "AiAgentsPage.tsx")
    assert "<OverviewTab" in page and "<ActivityTab" in page
