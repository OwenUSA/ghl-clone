"""Zuper in the browser (2026-09-16): executed where it can be.

**Executed under node.** `lib/zuper.ts` is import-free, so the shipped code runs here: the
plain words for every rule the sync writes to the conflict log, a field's name, money from
cents, a date written in the ACCOUNT's zone whatever the laptop's zone is, which panels are
drawn at all, whether the switch is drawn or replaced by reasons, and the "CRM Link" deep link
the sync writes into Zuper parsed back into the record it names.

**Asserted against source.** Settings → Zuper, the modal's "Quotes & invoices", the contact
panel section and the Photos tab are React, which this project has no runner for. Each
assertion names the failure it would catch. The headless drive (`tests/browser_zuper.py`)
covers them end to end.
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
BACKEND = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

node = pytest.mark.skipif(
    NODE is None,
    reason="node is not on PATH, so lib/zuper.ts cannot be executed. CI's backend job "
           "installs it so this file is never skipped there.")


def _read(*parts: str) -> str:
    return FRONTEND.joinpath(*parts).read_text(encoding="utf-8")


def run_js(body: str, tz: str = "Europe/Berlin"):
    script = textwrap.dedent("""
        import * as z from %s
        const out = (v) => console.log('@@' + JSON.stringify(v))
    """) % json.dumps((FRONTEND / "lib" / "zuper.ts").as_posix()) + textwrap.dedent(body)
    proc = subprocess.run(
        [NODE, "--experimental-strip-types", "--input-type=module", "-"],
        input=script, capture_output=True, text=True, env={**os.environ, "TZ": tz})
    assert proc.returncode == 0, proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@")]
    assert line, "the script printed no result:\n" + proc.stdout + proc.stderr
    return json.loads(line[-1][2:])


# ---------------- executed: words ----------------

@node
def test_every_rule_reads_in_plain_words():
    got = run_js("""
        out(['zuper_money', 'workiz_before_cutover', 'zuper_schedule_after_cutover',
             'latest_edit', 'crm_owned', 'crm_cannot_hold', 'mirrored_cancel',
             'zuper_owned', 'something_new'].map(z.ruleLabel))
    """)
    assert got == ["Zuper owns money", "Workiz wins before cutover",
                   "Zuper owns the schedule after cutover", "Latest edit wins",
                   "CRM-owned field", "The CRM question cannot hold Zuper\u2019s answer",
                   "Deleted in Zuper → visit cancelled", "Zuper owns this field",
                   "Something new"]


@node
def test_every_rule_the_engine_can_log_has_a_label():
    """A rule added to the engine without a label would show its snake_case name."""
    engine = (BACKEND / "app" / "zuper" / "engine.py").read_text(encoding="utf-8")
    rules = set(re.findall(r'"rule": "([a-z_]+)"', engine))
    rules |= set(re.findall(r'return [A-Z_]+, "([a-z_]+)", "', engine))
    assert {"zuper_money", "crm_owned", "latest_edit"} <= rules, rules
    got = run_js("out(Object.keys(z.RULE_LABELS))")
    # v2 (2026-09-16) dropped every automatic outcome on a Zuper job: no label may promise one.
    assert "invoice_paid" not in got
    assert rules - DROPPED_RULES <= set(got), rules - DROPPED_RULES - set(got)


DROPPED_RULES = {"invoice_paid"}


def test_nothing_in_the_ui_says_zuper_marks_a_card_won_or_makes_a_task():
    """Owner v2: no automation runs on a Zuper job in the CRM."""
    for parts in (("lib", "zuper.ts"), ("components", "ZuperSettings.tsx"),
                  ("components", "ZuperMoneyPanel.tsx"), ("components", "ZuperSend.tsx"),
                  ("components", "ContactDetailsPanel.tsx")):
        src = _read(*parts)
        assert "→ Won" not in src and "marked Won" not in src, parts
        assert not re.search(r"declined[^\n]{0,40}task", src, re.IGNORECASE), parts


@node
def test_field_names_read_as_the_form_names_them():
    got = run_js("""
        out(['cl:How many leaks?', 'crm:Technicians', 'street', 'city', 'state', 'zip',
             'first_name', 'value', 'owner_email', 'starts_at', 'brand_new_field',
            ].map(z.fieldLabel))
    """)
    assert got == ["Checklist: How many leaks?", "Technicians", "Address", "Address", "Address",
                   "Address", "First name", "Value", "Owner", "Start", "Brand new field"]


@node
def test_sides_directions_and_record_types():
    got = run_js("""
        out([z.sideLabel('crm'), z.sideLabel('zuper'), z.sideLabel('workiz'),
             z.directionLabel('crm_to_zuper'), z.directionLabel('zuper_to_crm'),
             z.crmTypeLabel('appointment'), z.crmTypeLabel('opportunity'),
             z.deleteStateLabel('restore_failed'),
             z.filterCheckLabel(true), z.filterCheckLabel(false)])
    """)
    assert got == ["CRM", "Zuper", "Workiz", "Deleted in the CRM → deleted in Zuper",
                   "Deleted in Zuper → deleted in the CRM", "Visit", "Opportunity",
                   "Restore failed", "narrows", "ignored — paged whole"]


@node
def test_chips_and_document_statuses():
    got = run_js("""
        out([z.syncChip('on'), z.syncChip('paused'), z.syncChip('off'),
             z.setupChip('pass'), z.setupChip('fail'), z.setupChip('confirm_by_hand'),
             z.documentStatus('PAID'), z.documentStatus('DECLINED'),
             z.documentStatus('PARTIALLY_PAID'), z.documentStatus('SENT'),
             z.documentStatus(null)])
    """)
    assert [c["text"] for c in got] == ["On", "Paused", "Off", "PASS", "FAIL", "Confirm by hand",
                                        "Paid", "Declined", "Partially paid", "Sent", "Unknown"]
    assert [c["tone"] for c in got] == ["green", "amber", "grey", "green", "red", "amber",
                                        "green", "red", "amber", "blue", "grey"]


@node
def test_cutover_and_value_source_sentences():
    got = run_js("""
        out([z.cutoverSentence(null, true), z.cutoverSentence('2026-10-01', true),
             z.cutoverSentence('2026-09-01', false),
             z.valueSourceSentence('invoices'), z.valueSourceSentence('approved_quotes'),
             z.valueSourceSentence(null)])
    """)
    assert got == ["Workiz is still in use", "Workiz is still in use until Oct 1, 2026",
                   "Workiz retired on Sep 1, 2026 — the importer refuses to run",
                   "The card\u2019s value follows the invoices\u2019 total in Zuper.",
                   "The card\u2019s value follows the approved quotes\u2019 total in Zuper.",
                   None]


# ---------------- executed: numbers and times ----------------

@node
def test_money_is_dollars_from_cents():
    got = run_js("""
        out([z.money(0), z.money(123456), z.money(-500), z.money(null), z.money(undefined),
             z.valueText(950000, 'value'), z.valueText(950000, 'cl:Squares'),
             z.valueText({ a: 1 }), z.valueText(null), z.valueText(true),
             z.valueText('x'.repeat(200)).length])
    """)
    assert got == ["$0.00", "$1,234.56", "-$5.00", "—", "—", "$9,500.00", "950000",
                   '{"a":1}', "—", "Yes", 120]


@node
@pytest.mark.parametrize("tz", ["Europe/Berlin", "Asia/Tokyo", "America/Los_Angeles"])
def test_times_are_written_in_the_accounts_zone_and_dates_never_shift(tz):
    got = run_js("""
        out([z.stamp('2026-09-16T13:17:00+00:00'), z.stamp('2026-01-13T13:17:00Z'),
             z.stamp(null), z.stamp('garbage'),
             z.dateText('2026-09-10'), z.dateText('2026-09-12T23:30:00Z'), z.dateText(null)])
    """, tz=tz)
    assert got == ["Sep 16 2026, 9:17 AM (EDT)", "Jan 13 2026, 8:17 AM (EST)", "—", "—",
                   "Sep 10, 2026", "Sep 12, 2026", "—"]


@node
def test_the_counts_table_fills_every_cell():
    got = run_js("""
        out([z.countTable({ contact: { linked: 3 }, stage: { linked: 5, deleted: 1 } }),
             z.pageCount(0, 25), z.pageCount(26, 25), z.pageCount(50, 25)])
    """)
    table, *pages = got
    assert [r["label"] for r in table] == ["Contacts", "Opportunities", "Visits", "Notes",
                                           "Tasks", "Pipelines", "Stages"]
    assert table[0]["cells"] == [3, 0, 0] and table[6]["cells"] == [5, 0, 1]
    assert table[1]["cells"] == [0, 0, 0]
    assert pages == [1, 2, 2]


@node
def test_the_load_report_summary_carries_counts_and_ids_only():
    got = run_js("""
        out(z.loadSummary({ mode: 'commit', started_at: '2026-09-16T12:00:00Z',
          phases: { contact: { crm: 3, created: 2, failed: 1, failed_ids: [9] } },
          verification: { mismatches: 2,
            contact: { crm: 3, mapped: 2, crm_not_mapped: [9], mapped_not_in_zuper: [],
                       zuper_not_mapped: [] },
            note: { crm: 1, zuper_not_mapped: ['note-4'] } } }))
    """)
    assert got["mode"] == "Commit" and got["mismatchTotal"] == 2
    assert got["phases"] == [{"kind": "Contact", "counts": "crm 3 · created 2 · failed 1",
                              "failedIds": [9]}]
    assert got["mismatches"] == [
        {"kind": "Contact", "what": "In the CRM, not linked", "ids": [9]},
        {"kind": "Note", "what": "In Zuper, not linked", "ids": ["note-4"]}]


# ---------------- executed: what is drawn ----------------

@node
def test_the_switch_is_drawn_only_when_it_can_work():
    got = run_js("""
        out([z.switchView({ enabled: true, blockers: ['x'] }),
             z.switchView({ enabled: false, blockers: [] }),
             z.switchView({ enabled: false, blockers: ['The setup check has not been run.'] })])
    """)
    assert got == ["pause", "switch_on", "blocked"]


@node
def test_panels_are_drawn_only_when_there_is_something_to_show():
    got = run_js("""
        const doc = { id: 'inv-1', kind: 'invoice' }
        const money = (linked, quotes, invoices) => ({ sync: 'on', linked, last_synced_at: null,
          quotes, invoices, value_source: null })
        out([z.showMoneyNav(undefined), z.showMoneyNav(money(false, [], [])),
             z.showMoneyNav(money(true, [], [])), z.showMoneyNav(money(false, [], [doc])),
             z.showContactMoney(undefined), z.showContactMoney({ documents: [] }),
             z.showContactMoney({ documents: [doc] }),
             z.attachmentsView(undefined),
             z.attachmentsView({ state: 'off', message: null, attachments: [] }),
             z.attachmentsView({ state: 'not_linked', message: null, attachments: [] }),
             z.attachmentsView({ state: 'ok', message: null, attachments: [] }),
             z.attachmentsView({ state: 'ok', message: null, attachments: [doc] }),
             z.attachmentsView({ state: 'unavailable', message: 'x', attachments: [] })])
    """)
    assert got == [False, False, True, True, False, False, True,
                   "none", "none", "none", "none", "group", "message"]


# ---------------- executed: deep links ----------------

@node
def test_the_crm_link_the_sync_writes_opens_that_record():
    from app.zuper import mapping

    links = [mapping.crm_link("opportunity", 42), mapping.crm_link("contact", 7)]
    got = run_js("""
        const parse = (href) => {
          const u = new URL(href)
          return z.recordFromLocation(u.pathname, u.search)
        }
        out([parse(%s), parse(%s),
             z.recordFromLocation('/opportunities/', '?opportunity=5&pipeline=2'),
             z.recordFromLocation('/opportunities', '?pipeline=2'),
             z.recordFromLocation('/opportunities', '?opportunity=0'),
             z.recordFromLocation('/opportunities', '?opportunity=12abc'),
             z.recordFromLocation('/opportunities', '?opportunity=-3'),
             z.recordFromLocation('/contacts', '?opportunity=5'),
             z.recordFromLocation('/', '?contact=5'),
             z.withoutRecordParam('/opportunities', '?opportunity=5&pipeline=2'),
             z.withoutRecordParam('/contacts', '?contact=7')])
    """ % (json.dumps(links[0]), json.dumps(links[1])))
    assert got == [{"view": "opportunities", "id": 42}, {"view": "contacts", "id": 7},
                   {"view": "opportunities", "id": 5}, None, None, None, None, None, None,
                   "/opportunities?pipeline=2", "/contacts"]


def test_the_app_opens_a_deep_linked_record_once_through_the_palette_path():
    app = _read("App.tsx")
    effect = app.split("recordFromLocation(window.location.pathname", 1)
    assert len(effect) == 2, "App.tsx no longer reads the CRM Link on load"
    body = effect[1].split("}, [openRecord])", 1)[0]
    assert "withoutRecordParam(" in body and "openRecord(link.view, link.id)" in body
    # Before the early returns: a hook after `if (session.isLoading) return` would break React.
    assert app.index("recordFromLocation(window.location") < app.index("if (session.isLoading)")


# ---------------- source: Settings ----------------

def test_settings_shows_zuper_to_an_admin_only():
    settings = _read("pages", "SettingsPage.tsx")
    assert "section === 'zuper' && user.role === 'ADMIN' ? <ZuperSettings />" in settings
    assert "(!ADMIN_SECTIONS.includes(s.key) || user.role === 'ADMIN') && (" in settings
    sections = _read("lib", "settingsSections.ts")
    assert "{ key: 'zuper', label: 'Zuper', path: '/settings/zuper' }" in sections
    assert "'zuper'" in sections.split("export const ADMIN_SECTIONS", 1)[1].split("\n", 1)[0]


def test_the_switch_is_not_rendered_while_blockers_exist():
    src = _read("components", "ZuperSettings.tsx")
    card = src.split("function SwitchCard(", 1)[1].split("\nfunction ", 1)[0]
    blocked, drawn = card.split("view === 'blocked' ? (", 1)[1].split(") : (", 1)
    assert 'role="switch"' not in blocked, "a dead switch is drawn next to the reasons"
    assert "The sync can be switched on when:" in blocked and "s.blockers.map(" in blocked
    assert 'role="switch"' in drawn
    assert "disabled" not in drawn.split('role="switch"', 1)[1].split(">", 1)[0], \
        "the switch is drawn disabled instead of not at all"
    # Both directions ask first; the 409's sentence is shown.
    assert "Switch the Zuper sync on?" in card and "Pause the Zuper sync?" in card
    assert "setError(e.message)" in card


def test_settings_covers_every_part_of_the_status():
    src = _read("components", "ZuperSettings.tsx")
    for needle in ("s.config.env_enabled", "s.config.key_set", "s.config.base_url",
                   "s.config.webhook_token_set", "syncChip(s.sync)",
                   "putZuperSettings({ workiz_cutover_date: value })", "save.mutate(null)",
                   "cutoverSentence(s.workiz_cutover_date, s.before_cutover)",
                   "runZuperSetupCheck", "confirmZuperSetupItem(item.key, confirmed)",
                   "Confirmed by hand", "item.confirmed.by", "last_sweep_success_at",
                   "sweep_cursor", "filterCheckLabel(", "last_error_at", "last_counts",
                   "countTable(s.counts)", "recent_failures", "s.inbox.recent",
                   "loadSummary(s.load_report)", "listZuperConflicts(page, PAGE_SIZE)",
                   "ruleLabel(c.rule)", "fieldLabel(c.field)", "sideLabel(c.written_to)",
                   "listZuperDeletes(page, PAGE_SIZE)", "directionLabel(d.direction)",
                   "restoreZuperDelete(id)"):
        assert needle in src, needle


def test_the_setup_button_is_drawn_only_when_the_check_can_run():
    src = _read("components", "ZuperSettings.tsx")
    card = src.split("function SetupCard(", 1)[1].split("\nfunction ", 1)[0]
    assert "const canRun = s.config.env_enabled && s.config.key_set" in card
    assert "{canRun && (" in card.split("Run setup check", 1)[0]
    assert "{!canRun && <div" in card


def test_restore_is_offered_only_when_the_server_says_it_can_work():
    src = _read("components", "ZuperSettings.tsx")
    log = src.split("function DeleteLog(", 1)[1]
    assert "{d.restorable && (" in log.split(">\n                      Restore", 1)[0]
    assert "setAsking(d)" in log and "restore.mutate(asking.id)" in log, "Restore must confirm"
    assert "result.results.map((r) =>" in log and "r.detail" in log


# ---------------- source: the modal, the contact panel, the Photos tab ----------------

def test_the_quotes_nav_item_is_conditional_and_sits_after_photos():
    modal = _read("components", "OpportunityDetail.tsx")
    nav = modal.split('aria-label="Opportunity sections"', 1)[1].split("</nav>", 1)[0]
    assert nav.index('label="Photos"') < nav.index('label="Quotes & invoices"') \
        < nav.index('label="AI agent"')
    before = nav.split('label="Quotes & invoices"', 1)[0].rsplit("{", 1)[1]
    assert before.startswith("showMoney && ("), "the nav item is drawn unconditionally"
    assert "const showMoney = showMoneyNav(zuperMoney.data)" in modal
    assert "getOpportunityZuper(opportunityId)" in modal and "retry: false" in modal
    assert "{tab === 'quotes' && showMoney && (" in modal


def test_the_money_panel_is_read_only():
    panel = _read("components", "ZuperMoneyPanel.tsx")
    assert "From Zuper — read only" in panel and "valueSourceSentence(data.value_source)" in panel
    for word in ("<input", "<select", "useMutation", "send<", "onChange"):
        assert word not in panel, word
    for col in ("'Number', 'Status', 'Total', 'Date', 'Expires'",
                "'Number', 'Status', 'Total', 'Balance', 'Date', 'Due'"):
        assert col in panel


def test_the_contact_panel_section_only_draws_when_there_is_a_document():
    panel = _read("components", "ContactDetailsPanel.tsx")
    assert "<ZuperSection contactId={c.id} />" in panel
    section = panel.split("function ZuperSection(", 1)[1].split("\nfunction ", 1)[0]
    assert "if (!data || !showContactMoney(data)) return null" in section
    assert "Zuper quotes & invoices (${data.documents.length})" in section
    assert "const links = canOpenRecords()" in section
    assert "openRecord('opportunities', d.opportunity_id!)" in section


def test_the_photos_tab_keeps_companycam_and_adds_zuper():
    tab = _read("components", "CompanyCamPhotos.tsx")
    assert "<ZuperPhotos opportunityId={opportunityId} />" in tab
    zuper = _read("components", "ZuperPhotos.tsx")
    assert "if (view === 'none' || !data) return null" in zuper
    assert "src={a.url}" in zuper and 'rel="noopener noreferrer"' in zuper
    assert "zuperpro.com" not in zuper, "the browser must only load CRM relay paths"


def test_api_helpers_were_appended_at_the_end():
    api = _read("lib", "api.ts")
    head, tail = api.split("Zuper two-way sync (2026-09-16)", 1)
    for name in ("getZuperStatus", "putZuperSettings", "runZuperSetupCheck",
                 "confirmZuperSetupItem", "listZuperConflicts", "listZuperDeletes",
                 "restoreZuperDelete", "getOpportunityZuper", "getContactZuper",
                 "getOpportunityZuperAttachments"):
        assert "export const %s" % name in tail and name not in head, name


# ---------------- v2 (2026-09-16): Send to Zuper, the mirror, lead outcomes ----------------

@node
def test_the_lead_outcomes_are_exactly_the_owners_list():
    from app import lead_outcomes

    got = run_js("out(z.LEAD_OUTCOMES)")
    assert got == ["Spam", "Wrong number", "Not interested", "Price shopping",
                   "Out of service area", "Duplicate", "No response", "Other"]
    assert got == lead_outcomes.OUTCOMES, "the UI and the server disagree on the list"


@node
def test_closing_an_unbooked_card_needs_an_outcome_and_other_needs_a_note():
    got = run_js("""
        const p = (status, booked, outcome, note) =>
          z.leadOutcomeProblem({ status, booked, outcome, note })
        out([p('lost', false, '', ''), p('abandoned', false, null, null), p('lost', true, '', ''),
             p('open', false, '', ''), p('lost', false, 'Spam', ''), p('lost', false, 'Other', ' '),
             p('lost', true, 'Other', ''), p('open', false, 'Other', 'moved away'),
             z.showLeadOutcome('open', ''), z.showLeadOutcome('lost', ''),
             z.showLeadOutcome('won', 'Spam')])
    """)
    from app import lead_outcomes

    need, note = lead_outcomes.NEEDED, lead_outcomes.NOTE_NEEDED
    assert got == [need, need, None, None, None, note, note, None, False, True, True]


@node
def test_the_lock_reason_and_what_it_locks():
    got = run_js("""
        const z1 = (state, fields) => ({ state, job_uid: 'job-1', job_url: null, sent_at: null,
          error: null, locked_fields: fields, may_send: true, can_send: true, send_problems: [] })
        out([z.LOCK_REASON,
             z.lockedField(z1('sent', ['title', 'stage_id']), 'title'),
             z.lockedField(z1('sent', ['title']), 'notes'),
             z.lockedField(z1('queued', ['value_cents']), 'value_cents'),
             z.lockedField(z1('not_sent', ['title']), 'title'),
             z.lockedField(undefined, 'title'),
             z.lockTitle(z1('sent', ['owner_id']), 'owner_id', 'role'),
             z.lockTitle(z1('sent', []), 'owner_id', 'role'),
             z.cardDraggable({ managed_in_zuper: true }), z.cardDraggable({}),
             z.visitLocked({ zuper_locked: true }), z.visitLocked({}),
             z.contactFieldLocked({ zuper_locked: true, zuper_locked_fields: ['phone'] }, 'phone'),
             z.contactFieldLocked({ zuper_locked: true, zuper_locked_fields: ['phone'] }, 'source'),
             z.contactFieldLocked({ zuper_locked: false, zuper_locked_fields: [] }, 'phone')])
    """)
    assert got == ["Change this in Zuper", True, False, True, False, False,
                   "Change this in Zuper", "role", False, True, True, False, True, False, False]


@node
def test_the_send_control_is_hidden_unless_this_user_may_send():
    got = run_js("""
        const z1 = (state, may, can, problems = [], error = null) => ({ state, job_uid: null,
          job_url: null, sent_at: null, error, locked_fields: [], may_send: may, can_send: can,
          send_problems: problems })
        out([z.sendView(undefined), z.sendView(z1('not_sent', false, true)),
             z.sendView(z1('not_sent', true, true)), z.sendView(z1('queued', false, false)),
             z.sendView(z1('sent', false, false)), z.sendView(z1('failed', true, true, [], 'x')),
             z.sendButton(z1('not_sent', false, true)),
             z.sendButton(z1('not_sent', true, true)),
             z.sendButton(z1('not_sent', true, false, ['The job has no address.', 'No phone.'])),
             z.sendButton(z1('failed', false, true)), z.sendButton(z1('failed', true, true)),
             z.sendButton(z1('sent', true, true))])
    """)
    hidden = {"shown": False, "enabled": False, "reason": None}
    assert got == ["none", "none", "send", "queued", "managed", "failed",
                   hidden, {"shown": True, "enabled": True, "reason": None},
                   {"shown": True, "enabled": False,
                    "reason": "The job has no address. No phone."},
                   hidden, {"shown": True, "enabled": True, "reason": None}, hidden]


@node
def test_the_lead_outcome_report_tables_name_blanks():
    got = run_js("""
        out([z.outcomeTable([{ source: null, counts: { Spam: 2 }, total: 2 },
                             { source: 'Google', counts: { Other: 1, Spam: 1 }, total: 2 }],
                            ['Spam', 'Other'], 'source'),
             z.outcomeTable([{ campaign: '  ', counts: {}, total: 0 }], ['Spam'], 'campaign'),
             z.dayAfter('2026-09-30'), z.dayAfter('2026-12-31'), z.dayAfter('garbage')])
    """)
    assert got[2:] == ["2026-10-01", "2027-01-01", "garbage"]
    assert got[:2] == [[{"label": "No source", "cells": [2, 0], "total": 2},
                    {"label": "Google", "cells": [1, 1], "total": 2}],
                   [{"label": "No campaign", "cells": [0], "total": 0}]]


def test_the_modal_disables_every_zuper_only_control_with_the_reason():
    src = _read("components", "OpportunityDetail.tsx")
    assert "const lock = (field: string) => lockedField(zuper, field)" in src
    assert "title: lock(field) ? LOCK_REASON" in src
    for label, key in (('aria-label="Opportunity name"', "title"),
                       ('ariaLabel="Pipeline"', "pipeline_id"),
                       ('ariaLabel="Stage"', "stage_id"), ('ariaLabel="Status"', "status"),
                       ('aria-label="Value"', "value_cents"), ('ariaLabel="Owner"', "owner_id")):
        at = src.index(label)
        assert "'%s').disabled" % key in src[at - 200:at + 200], label
    # The job address: every input, "Use contact address", and the Checklist's linked address.
    assert "disabled={gate(canEdit, key).disabled}" in src
    assert "{fallback && canEdit && !addressLocked && (" in src
    assert "disabled: !canEdit || addressLocked," in src
    assert "reason: addressLocked ? LOCK_REASON" in src
    # Visits: no booking, and the reason.
    assert "canBook={canBook && !lock('appointments')}" in src
    assert "bookReason={lock('appointments') ? LOCK_REASON : undefined}" in src
    # Notes, Checklist answers and tasks are NOT gated by the lock.
    assert "<fieldset disabled={!canAnswer}" in src
    assert "<NotesTab opportunityId={o.id} user={user} />" in src
    assert "<ZuperSendLine opportunityId={o.id} zuper={o.zuper} />" in src


def test_the_send_line_draws_no_button_for_a_user_who_may_not_send():
    src = _read("components", "ZuperSend.tsx")
    assert "if (view === 'none' && !error) return null" in src
    assert "{button.shown && (" in src
    assert "title={button.reason ?? 'Send this job to Zuper'}" in src
    assert "Send this job to Zuper?" in src and "send.mutate()" in src, "sending must confirm first"
    assert "Managed in Zuper" in src and "zuper?.job_url && (" in src
    assert "Sending to Zuper…" in src


def test_a_managed_card_does_not_lift_on_the_board():
    card = _read("components", "OpportunityCard.tsx")
    assert "const draggable = cardDraggable(o)" in card
    assert "disabled: { draggable: !draggable, droppable: false }" in card
    assert "title={draggable ? undefined : LOCK_REASON}" in card
    assert "Managed in Zuper" in card
    page = _read("pages", "OpportunitiesPage.tsx")
    assert "?.managed_in_zuper) return" in page.split("function onDragEnd(", 1)[1][:400]


def test_a_locked_visit_offers_no_edit_or_cancel():
    dialog = _read("components", "AppointmentDetailDialog.tsx")
    assert "const zuperLocked = visitLocked(detail.data)" in dialog
    assert "const canWrite = user.role !== 'TECH' && !zuperLocked" in dialog
    assert "{!zuperLocked && <>" in dialog
    assert "data-zuper-locked={visitLocked(a) || undefined}" in _read("pages", "CalendarsPage.tsx")


def test_a_locked_contact_cannot_be_edited_in_the_panel():
    panel = _read("components", "ContactDetailsPanel.tsx")
    assert "locked={contactFieldLocked(c, f.key) ? LOCK_REASON : null}" in panel
    field = panel.split("function Field(", 1)[1].split("\nfunction ", 1)[0]
    locked = field.split("{locked ? (", 1)[1].split(") : editing ? (", 1)[0]
    assert "onClick" not in locked, "a locked field still opens its editor"


def test_lead_outcome_in_the_modal_add_opportunity_bulk_and_reporting():
    modal = _read("components", "OpportunityDetail.tsx")
    assert "leadOutcomeProblem({ status: form.status, booked: !!o.booked," in modal
    assert "body.lead_outcome = f.leadOutcome || null" in modal
    add = _read("components", "AddOpportunityModal.tsx")
    assert ("leadOutcomeProblem({ status, booked: false, outcome: leadOutcome, "
            "note: leadOutcomeNote })") in add
    bulk = _read("components", "BulkActionsBar.tsx")
    assert "bulkLeadOutcome(ids, outcome, outcomeNote.trim() || null)" in bulk
    assert "{canAssign && (" in bulk.split("Lead outcome…", 1)[0][-1200:]
    report = _read("pages", "ReportingPage.tsx")
    assert "{ key: 'lead-outcomes', label: 'Lead outcomes' }" in report
    assert "getLeadOutcomeReport({ since: startDate, until: dayAfter(endDate)," in report
    assert "kind === 'source' ? 'Source' : 'Campaign'" in report


def test_ai_actions_render_from_the_catalogue_generically():
    """suggest_send_to_zuper and set_lead_outcome need no special case: the builder lists
    every catalogue action, and a suggestion card draws the server's summary."""
    builder = _read("components", "ai", "AgentBuilder.tsx")
    assert "(cat.data?.actions ?? []).filter((a) => a.channels.includes('text'))" in builder
    assert "offered.map((a) => (" in builder
    assert "{s.summary}" in _read("components", "AiSuggestions.tsx")
