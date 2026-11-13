"""Reading Frontdoor, AHS's contractor portal (2026-11-12). No test reaches the portal.

  * the guard is live: frontdoorhome.com lookups refused, no Chromium can start, no FRONTDOOR_*
    setting survives into the suite;
  * the route guard lets reads and the login through and ABORTS every other write to a
    frontdoorhome.com host, failing the read; telemetry writes are dropped;
  * the click guard refuses anything that reads like an action, and "Submitted Invoices" is not
    "Submit";
  * off (or no login) = a sentence and no browser; the login's secrets never ride on an error;
  * the parsers turn the page's cells (as measured 2026-11-12, re-typed with invented values)
    into ids, statuses, dates and money — never a note's text or a customer's name;
  * Owen AI's tools and the inspection report use it only when allowed, and a failure is a
    sentence or a warning, never a crash.
"""
import contextlib
import logging
import os
import socket

import pytest
from app.frontdoor import client, config, guard, parse, service
from app.owen_ai import report, tools
from app.registry import catalog
from tests import frontdoor_guard

SECRET = "pw-Never-Show-9$"


@pytest.fixture(autouse=True)
def _fresh_cache():
    service.clear_cache()
    yield
    service.clear_cache()


@pytest.fixture()
def armed(monkeypatch, tmp_path):
    monkeypatch.setenv("FRONTDOOR_READ_ENABLED", "true")
    monkeypatch.setenv("FRONTDOOR_USER", "office@example.com")
    monkeypatch.setenv("FRONTDOOR_PASSWORD", SECRET)
    monkeypatch.setenv("FRONTDOOR_STATE_DIR", str(tmp_path / "state"))


# ------------------------------------------------------------------------------ the fake portal

AUTHO_TEXT = ("Authorization\nRoof\nCustomer Owes\n$475.00\nAuthorized To Contractor\n$1125.00\n"
              "Total\n$1600.00\nAuthorized:\n11/12/2026 at 12:50 PM heo\nby AGENTX\n"
              "Authorized To Contractor\n$1125.00\nCustomer Owes\n$475.00\nTotal\n$1600.00\n"
              "Edit Estimate")
NOTE = ("Note Added in Frontdoor System NCC $1,125 Net Total $1,600 AUTHO # 123456RNCL Thanks "
        "for being the best! Call Jane Customer at 941-555-0100, 1 Main St")


def detail_raw(did="99000001"):
    return {"dispatch_id": did,
            "cells": {"DispatchStatusText": "In Progress Authorization Approved with Limitations",
                      "DispatchInvoiceStatusText": "Job Not Completed. Cannot be invoiced.",
                      "DispatchTypeText": "Transfer Vendor", "DispatchTradeText": "ROOF LEAKS",
                      "DispatchUrgencyText": "Normal", "DispatchWarrantyText": "AHS",
                      "ContractEffectiveDateText": "05/27/2026 12:06 PM -04",
                      "ContractExpiryDateText": "05/27/2027 12:06 PM -04"},
            "items": ["Roof Leaks"], "autho_text": AUTHO_TEXT,
            "history": [
                ["11/12/2026 01:50 PM GMT-4", "Authorization Approved with Limitations",
                 "[CSC] Agent X", "Roof Leaks", NOTE],
                ["11/12/2026 01:49 PM GMT-4", "Authorization Reported", "[CNTRTRPRTL] Office",
                 "Roof Leaks", ""],
                ["11/04/2026 10:04 AM GMT-4", "Dispatch Assigned", "[DISPQMGR] SYSTEM",
                 "Roof Leaks", ""]]}


class FakePortal:
    def __init__(self):
        self.reads = []

    def dispatch_detail(self, did):
        self.reads.append(("dispatch", did))
        return detail_raw(did)

    def dispatch_rows(self, since):
        self.reads.append(("dispatches", since))
        row = {"cells": {"Date": "11/12/2026", "WarrantyBrand": "AHS", "DispatchID": "99000001",
                         "StatusGroup": "In Progress", "Status": "Technician Arrived"},
               "items": ["Roof Leaks"],
               "href": "/dispatches/99000001?contractID=1&vendorID=2&workorderID=3"}
        return [row, row, {"cells": {"DispatchID": "Dispatch ID"}}]

    def invoice_rows(self):
        self.reads.append(("invoices",))
        return {"ready": [{"createOn": "11/03/2026", "DispatchLink": "99000002",
                           "DispatchItems": "Roof Leaks"}],
                "draft": [],
                "submitted": [{"DispatchLink": "99000003", "submittedOn": "09/06/2026",
                               "legacyItemName": "574", "Status": "Paid",
                               "TotalAmount": "$1,125.00", "TotalAmountPaid": "$1,125.00"}]}


@pytest.fixture()
def portal(monkeypatch):
    fake = FakePortal()
    opened = []

    @contextlib.contextmanager
    def session():
        opened.append(1)
        yield fake
    monkeypatch.setattr(client, "session", session)
    fake.opened = opened
    return fake


# ---------------------------------------------------------------------------- the guards are live

def test_the_guard_refuses_frontdoor_and_no_browser_can_start():
    with pytest.raises(OSError, match="network guard"):
        socket.getaddrinfo("contractor.frontdoorhome.com", 443)
    with pytest.raises(OSError, match="network guard"):
        client.LAUNCH(object())
    assert frontdoor_guard.ATTEMPTS[0] == "contractor.frontdoorhome.com"
    assert len(frontdoor_guard.ATTEMPTS) == 2
    frontdoor_guard.ATTEMPTS.clear()      # expected here; any other test would now fail
    assert frontdoor_guard.frontdoor_host("PROD.APIS.FrontdoorHome.com.")
    assert not frontdoor_guard.frontdoor_host("frontdoorhome.com.example.org")
    assert not [k for k in os.environ if k.startswith("FRONTDOOR_")]


# ------------------------------------------------------------------------------ the route guard

@pytest.mark.parametrize("method,url,logging_in,verdict", [
    ("GET", "https://prod.apis.frontdoorhome.com/service-request-facade/v1/dispatch", False,
     "read"),
    ("HEAD", "https://contractor.frontdoorhome.com/", False, "read"),
    ("OPTIONS", "https://prod.apis.frontdoorhome.com/x", False, "read"),
    ("GET", "https://www.google.com/anything", False, "read"),
    ("POST", "https://login.frontdoorhome.com/oauth2/authorize", True, "login"),
    ("POST", "https://login.frontdoorhome.com/oauth2/authorize", False, "refuse"),
    ("POST", "https://login.frontdoorhome.com/oauth2/token", False, "token"),
    ("POST", "https://login.frontdoorhome.com/oauth2/logout", True, "refuse"),
    ("POST", "https://prod.apis.frontdoorhome.com/service-request-facade/v1/dispatch", True,
     "refuse"),
    ("PUT", "https://prod.apis.frontdoorhome.com/estimate/v1/estimate", False, "refuse"),
    ("PATCH", "https://contractor.frontdoorhome.com/api/x", False, "refuse"),
    ("DELETE", "https://prod.apis.frontdoorhome.com/x/1", False, "refuse"),
    ("PUT", "https://login.frontdoorhome.com/oauth2/token", False, "refuse"),
    ("POST", "https://analytics.google.com/g/collect", False, "drop"),
    ("POST", "https://frontdoorhome.com.evil.example/oauth2/authorize", True, "drop"),
])
def test_every_request_is_decided_before_it_leaves(method, url, logging_in, verdict):
    assert guard.decide(method, url, logging_in=logging_in) == verdict


class Req:
    def __init__(self, method, url):
        self.method, self.url = method, url


class Route:
    def __init__(self):
        self.did = None

    def continue_(self):
        self.did = "continue"

    def abort(self):
        self.did = "abort"


def test_a_write_to_frontdoor_is_aborted_and_fails_the_read():
    g = guard.Guard()
    seen = []
    for method, url in [("GET", "https://contractor.frontdoorhome.com/dispatches"),
                        ("POST", "https://login.frontdoorhome.com/oauth2/token"),
                        ("POST", "https://u.clarity.ms/collect"),
                        ("POST", "https://prod.apis.frontdoorhome.com/estimate/v1/submit?x=1")]:
        r = Route()
        g.handle(r, Req(method, url))
        seen.append(r.did)
    assert seen == ["continue", "continue", "abort", "abort"]
    assert g.dropped == 1
    with pytest.raises(guard.WriteRefused, match=r"POST prod.apis.frontdoorhome.com"
                                                 r"/estimate/v1/submit"):
        g.check()


def test_the_login_post_goes_only_while_logging_in():
    g = guard.Guard()
    r = Route()
    g.handle(r, Req("POST", "https://login.frontdoorhome.com/oauth2/authorize"))
    assert r.did == "abort" and g.refused
    g = guard.Guard()
    g.logging_in = True
    r = Route()
    g.handle(r, Req("POST", "https://login.frontdoorhome.com/oauth2/authorize"))
    assert r.did == "continue" and g.login_posts == 1 and not g.refused
    g.check()


class Control:
    def __init__(self, text):
        self.text, self.clicked = text, False

    def inner_text(self, timeout=None):
        return self.text

    def click(self):
        self.clicked = True


@pytest.mark.parametrize("label", ["Submit", "Accept", "Decline", "Update Dispatch",
                                   "View / Edit Authorization", "Request Authorization",
                                   "Create invoice", "Dismiss", "Add Note", "Edit Estimate",
                                   "Upload", "Logout"])
def test_an_action_is_never_clicked(label):
    c = Control(label)
    with pytest.raises(guard.ClickRefused):
        guard.safe_click(c, "Next page")      # the page's own words decide too
    assert not c.clicked


@pytest.mark.parametrize("label", ["Next page", "Submitted Invoices", "Draft Invoices"])
def test_reading_controls_are_clicked(label):
    c = Control(label)
    guard.safe_click(c, label)
    assert c.clicked


# ------------------------------------------------------------------------------ off and login

def test_off_is_a_sentence_and_no_browser(portal):
    for call in (lambda: service.dispatch("99000001"), lambda: service.dispatches(),
                 lambda: service.invoices()):
        with pytest.raises(service.FrontdoorOff, match="FRONTDOOR_READ_ENABLED"):
            call()
    assert portal.opened == [] and portal.reads == []


def test_on_without_a_login_is_a_sentence_and_no_browser(portal, monkeypatch):
    monkeypatch.setenv("FRONTDOOR_READ_ENABLED", "1")
    with pytest.raises(service.FrontdoorOff, match="FRONTDOOR_USER"):
        service.dispatch("99000001")
    assert portal.opened == []


def test_the_login_can_come_from_a_root_only_file(monkeypatch, tmp_path):
    f = tmp_path / "frontdoor.env"
    f.write_text("# comment\nFRONTDOOR_USER=office@example.com\nFRONTDOOR_PASSWORD='%s'\n"
                 % SECRET, encoding="utf-8")
    monkeypatch.setenv("FRONTDOOR_CREDENTIALS_FILE", str(f))
    assert config.credentials() == ("office@example.com", SECRET)
    monkeypatch.setenv("FRONTDOOR_READ_ENABLED", "yes")
    assert config.refusal() is None


class LoginPage:
    """FusionAuth's form, refusing the click with a message that would carry the password."""
    url = "https://login.frontdoorhome.com/oauth2/authorize?client_id=x"

    def __init__(self):
        self.filled = {}

    def goto(self, url, **kw):
        pass

    def wait_for_function(self, *a, **kw):
        pass

    def wait_for_selector(self, *a, **kw):
        pass

    def fill(self, selector, value):
        self.filled[selector] = value

    def click(self, selector):
        raise RuntimeError("Timeout clicking; fill('#password', '%s')" % SECRET)


def test_a_failed_login_says_so_without_the_password(armed, monkeypatch, caplog):
    monkeypatch.setattr(config, "PAGE_GAP_SECONDS", 0)
    g = guard.Guard()
    p = client.Portal(LoginPage(), g)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(client.FrontdoorError) as err:
        p.ensure_login()
    assert "did not accept the login" in str(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__
    assert SECRET not in str(err.value) and SECRET not in caplog.text
    assert g.logging_in is False                       # the login POST window is closed again
    assert service.sentence(err.value) == str(err.value)
    assert SECRET not in service.sentence(RuntimeError("fill " + SECRET))


# ------------------------------------------------------------------------------ parsing

def test_the_authorization_panel_is_read_as_money():
    a = parse.authorization(AUTHO_TEXT)
    assert a == {"requested": True, "trade": "Roof", "customer_owes": 475.0,
                 "authorized_to_contractor": 1125.0, "total": 1600.0,
                 "authorized_at": "2026-11-12T12:50:00-05:00", "authorized_by": "AGENTX"}
    assert parse.authorization("Authorization\nNo Authorization Requested\nRequest "
                               "Authorization") == {"requested": False}
    assert parse.authorization(None) is None


def test_history_times_keep_the_portals_own_offset():
    # The portal stamped GMT-4 while New York was on EST: 13:50 GMT-4 is 12:50 in New York,
    # the same minute the Authorization panel shows.
    assert parse.moment("11/12/2026 01:50 PM GMT-4") == "2026-11-12T12:50:00-05:00"
    assert parse.moment("07/01/2026 09:05 AM GMT-4") == "2026-07-01T09:05:00-04:00"
    assert parse.moment("11/10/2026 at 05:03 PM heo") == "2026-11-10T17:03:00-05:00"
    assert parse.moment("not a date") is None


def test_a_detail_keeps_ahs_figures_but_never_a_notes_text():
    d = parse.dispatch_detail(detail_raw())
    assert d["status"].endswith("Approved with Limitations") and d["items"] == ["Roof Leaks"]
    assert d["plan_effective"] == "2026-05-27" and d["plan_expires"] == "2027-05-27"
    assert d["authorization"]["authorized_to_contractor"] == 1125.0
    assert d["autho_numbers"] == ["123456RNCL"]
    assert d["ncc_amounts"] == [1125.0] and d["net_totals"] == [1600.0]
    assert d["authorization_update"]["update"] == "Authorization Approved with Limitations"
    assert [h["at"][:10] for h in d["history"]] == ["2026-11-12", "2026-11-12", "2026-11-04"]
    text = repr(d)
    for personal in ("Jane", "941-555-0100", "Main St", "Thanks for being"):
        assert personal not in text


def test_list_and_invoice_rows():
    r = parse.dispatch_row({"cells": {"Date": "11/12/2026", "WarrantyBrand": "AHS",
                                      "DispatchID": "102249039", "StatusGroup": "New",
                                      "Status": "Dispatch Assigned"},
                            "items": ["Roof Leaks", " "],
                            "href": "/dispatches/102249039?contractID=17&workorderID=24"})
    assert r == {"dispatch_id": "102249039", "date": "2026-11-12", "brand": "AHS",
                 "items": ["Roof Leaks"], "status_group": "New", "status": "Dispatch Assigned",
                 "contract_id": "17", "work_order_id": "24"}
    assert parse.dispatch_row({"cells": {"DispatchID": "Dispatch ID"}}) is None
    s = parse.invoice_row("submitted", {"DispatchLink": "94399869", "submittedOn": "11/06/2026",
                                        "legacyItemName": "574 (Roof Leaks $1,125.00)",
                                        "Status": "Submitted", "TotalAmount": "$1,125.00",
                                        "TotalAmountPaid": "$0.00"})
    assert (s["invoice_id"], s["invoice_item"], s["total"], s["paid"], s["submitted"]) == \
        ("574", "Roof Leaks $1,125.00", 1125.0, 0.0, "2026-11-06")
    assert parse.invoice_row("submitted", {"DispatchLink": "1234567",
                                           "legacyItemName": "573"})["invoice_id"] == "573"
    assert parse.money("-$1,125.50") == -1125.5 and parse.money("") is None


# ------------------------------------------------------------------------------ the service

def test_a_read_is_cached_and_fresh_reads_again(armed, portal):
    a = service.dispatch("AHS #99000001")
    b = service.dispatch("99000001")
    assert a["cached"] is False and b["cached"] is True and len(portal.opened) == 1
    service.dispatch("99000001", fresh=True)
    assert len(portal.opened) == 2
    with pytest.raises(ValueError, match="6 to 10 digits"):
        service.dispatch("job 701")
    assert len(portal.opened) == 2


def test_the_list_drops_header_rows_and_repeats_and_invoices_filter_by_date(armed, portal):
    d = service.dispatches()
    assert d["count"] == 1 and d["dispatches"][0]["work_order_id"] == "3"
    inv = service.invoices(since=parse.datetime(2026, 10, 1).date())
    assert [r["dispatch_id"] for r in inv["invoices"]] == ["99000002"]
    assert inv["count_by_tab"] == {"ready": 1}


def test_authorization_for_is_none_when_off_or_not_requested(armed, portal, monkeypatch):
    a = service.authorization_for("99000001")
    assert a["authorized_to_contractor"] == 1125.0 and a["autho_numbers"] == ["123456RNCL"]
    monkeypatch.setattr(FakePortal, "dispatch_detail",
                        lambda self, did: {**detail_raw(did), "autho_text":
                                           "Authorization\nNo Authorization Requested"})
    assert service.authorization_for("99000002") is None
    monkeypatch.setenv("FRONTDOOR_READ_ENABLED", "false")
    assert service.authorization_for("99000003") is None


# ------------------------------------------------------------------------------ Owen AI

def test_owen_ais_tools_answer_with_a_sentence_when_off(db, portal):
    out = tools.frontdoor_read("dispatch", dispatch="99000001")
    assert out.startswith("ERROR") and "FRONTDOOR_READ_ENABLED" in out
    assert portal.opened == []
    names = {name for name, *_ in tools.SPECS}
    assert {"frontdoor_dispatch", "frontdoor_dispatches", "frontdoor_invoices"} <= names


def test_owen_ais_tools_read_when_on_and_obey_the_registry(db, armed, portal, monkeypatch):
    out = tools.frontdoor_read("dispatch", dispatch="99000001")
    assert '"authorized_to_contractor": 1125.0' in out and "Jane" not in out
    assert SECRET not in out
    from app.registry import service as registry
    monkeypatch.setattr(registry, "level", lambda db, key, action: catalog.NEVER
                        if action == "ahs_read" else catalog.AUTO)
    out = tools.frontdoor_read("dispatch", dispatch="99000001", fresh=True)
    assert out.startswith("ERROR") and "Never" in out
    assert len(portal.opened) == 1


def test_a_failed_read_is_a_sentence_without_the_pages_call_log(db, armed, monkeypatch):
    @contextlib.contextmanager
    def broken():
        raise TimeoutError("waiting for locator('#password') filled with %s" % SECRET)
        yield
    monkeypatch.setattr(client, "session", broken)
    out = tools.frontdoor_read("invoices")
    assert out == ("ERROR from Frontdoor: the Frontdoor page did not load as expected "
                   "(TimeoutError)")


def test_the_registry_lets_owen_ai_read_and_never_submit():
    e = next(x for x in catalog.ENTRIES if x.key == "owen_ai")
    a = e.action("ahs_read")
    assert a.default == catalog.AUTO and set(a.choices) == {catalog.NEVER, catalog.AUTO}
    assert e.action("ahs_portal") is None
    sub = next(x for x in catalog.ENTRIES if x.key == "ahs_submission")
    assert all(x.default == catalog.NEVER and not x.choices for x in sub.actions)


# ------------------------------------------------------------------------------ the report

def opt(name, credit_line):
    return {"option_name": name, "total": 2000, "deposit": 1000, "is_hidden": False,
            "line_items": [{"markdown_description":
                            "(Retail Value: $3,000 | Special AHS Member Price: $2,500%s | "
                            "Customer Pays Out of Pocket: $2,000)\n* Step one: x" % credit_line}]}


def test_the_report_takes_a_missing_ahs_credit_from_frontdoor_and_flags_a_different_one():
    ahs = {"dispatch_id": "99000001", "authorized_to_contractor": 1125.0, "customer_owes": 475.0,
           "total": 1600.0, "authorized_at": "2026-11-12T12:50:00-05:00"}
    warnings = []
    opts = report.options_of({"proposal_options": [
        opt("GOOD", ""), opt("BETTER", " | AHS Authorized Credit: -$1,125"),
        opt("BEST", " | AHS Authorized Credit: -$900")]}, "DTR-1", warnings, ahs)
    assert [o.credit for o in opts] == [-1125.0, -1125.0, -900.0]
    assert any("AHS credit for GOOD taken from Frontdoor" in w and "dispatch 99000001" in w
               for w in warnings)
    assert any("BEST" in w and "Frontdoor says AHS authorized $1,125" in w for w in warnings)
    assert not any("GOOD: could not read" in w and "AHS credit" in w for w in warnings)
    warnings = []
    plain = report.options_of({"proposal_options": [opt("GOOD", "")]}, "DTR-1", warnings)
    assert plain[0].credit is None and any("AHS credit" in w for w in warnings)


def job_rec(ahs_id):
    return {"custom_fields": [{"label": "AHS Job ID", "value": ahs_id}] if ahs_id else []}


def test_the_report_asks_frontdoor_only_for_an_ahs_job_and_survives_a_failure(
        db, armed, portal, monkeypatch):
    w = []
    assert report.ahs_authorization(job_rec(None), w) is None and portal.opened == [] and not w
    assert report.ahs_authorization(job_rec("99000001"), w)["authorized_to_contractor"] == 1125.0

    def boom(_):
        raise client.WriteRefused("The Frontdoor page tried to write and was stopped: POST x")
    monkeypatch.setattr(service, "authorization_for", boom)
    w = []
    assert report.ahs_authorization(job_rec("99000005"), w) is None
    assert w and "tried to write" in w[0]


def test_the_report_makes_no_request_when_reading_is_off(db, portal):
    w = []
    assert report.ahs_authorization(job_rec("99000001"), w) is None
    assert portal.opened == [] and not w


def test_the_owen_ai_pages_chip_is_green_only_when_reading_can_work(monkeypatch, tmp_path):
    monkeypatch.setenv("FRONTDOOR_STATE_DIR", str(tmp_path))
    assert config.ready() is False                          # off
    monkeypatch.setenv("FRONTDOOR_READ_ENABLED", "true")
    assert config.ready() is False                          # on, but no login anywhere
    (tmp_path / "state.json").write_text("{}", encoding="utf-8")
    assert config.ready() is True                           # the api container's case
    (tmp_path / "state.json").unlink()
    monkeypatch.setenv("FRONTDOOR_USER", "office@example.com")
    monkeypatch.setenv("FRONTDOOR_PASSWORD", SECRET)
    assert config.ready() is True                           # owen_ai's case
