"""The customer review (2026-11-13): many customers gathered in the background, read in pages.

  * gathering saves one file per job as it goes, records a failed job and goes on, and a
    resumed review skips what is saved;
  * Frontdoor is read only for jobs with an AHS Job ID, in one batch, and a Frontdoor failure
    leaves the Zuper part and the Excel intact;
  * the attention rules say why a customer is on the list;
  * pages of ten, `only_attention`, the Excel (three sheets) and its owner-only download;
  * a finished review queues one message in the chat that started it;
  * Frontdoor's batch read: one session, cache reused, one bad page does not stop the rest, a
    refused write stops everything.
No test reaches Zuper, Quo or Frontdoor: every source is a parameter or a fake.
"""
import contextlib
from datetime import UTC, datetime, timedelta

import pytest
from app.frontdoor import client as fd_client
from app.frontdoor import service as fd_service
from app.models import OwenAiMessage, OwenAiThread
from app.owen_ai import review, tools
from openpyxl import load_workbook
from tests.test_owen_ai import call, people  # noqa: F401  (fixture)

NOW = datetime.now(UTC)


def ts(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).astimezone(review.dossier.TZ).strftime(
        "%Y-%m-%d %H:%M")


JOBS = [{"uid": "u1", "number": "701", "stage": "Inspection Scheduled", "days_in_stage": 2,
         "first_name": "Ann", "assigned": ["Luis"]},
        {"uid": "u2", "number": "702", "stage": "Waiting on AHS", "days_in_stage": 12,
         "first_name": "Bob", "assigned": []},
        {"uid": "u3", "number": "703", "stage": "New", "days_in_stage": 0,
         "first_name": "Cy", "assigned": []}]


def dossier_of(uid):
    if uid == "u3":
        raise RuntimeError("Zuper said 500")
    if uid == "u1":
        return {"job": {"board": "AHS Simple Flow", "customer": {"first_name": "Ann",
                                                                 "last_name": "Ames"},
                        "custom_fields": {"AHS Job ID": "99000001"},
                        "stage_history": [{"stage": "New", "at": ts(5), "by": "Luis"}]},
                "notes": [{"at": ts(4), "by": "Luis", "text": "Called, will book"}],
                "proposals": [],
                "conversations": [{"ts": ts(0.2), "ch": "quo_text", "dir": "in",
                                   "text": "When are you coming?"},
                                  {"ts": ts(1), "ch": "zuper_call", "dir": "out", "who": "Luis",
                                   "secs": 60, "answered": True, "summary": "Booked Friday"}]}
    return {"job": {"board": "AHS Simple Flow", "customer": {"first_name": "Bob"},
                    "custom_fields": {}},
            "notes": [], "proposals": [{"number": "DTR-9", "status": "SENT"}],
            "conversations": [{"ts": ts(6), "ch": "zuper_text", "dir": "out", "who": "Office",
                               "text": "Waiting on AHS"}]}


AUTH = {"dispatch_id": "99000001", "status": "In Progress",
        "authorization": {"requested": True, "authorized_to_contractor": 1125.0,
                          "customer_owes": 475.0, "total": 1600.0}, "history": []}


@pytest.fixture()
def reviews(tmp_path, monkeypatch):
    monkeypatch.setenv("OWEN_AI_REPORT_DIR", str(tmp_path / "reports"))
    return tmp_path


def new_review(with_frontdoor=True):
    review.root().mkdir(parents=True, exist_ok=True)
    rid = "R20261113-0900-abcd"
    d = review.root() / rid
    (d / "customers").mkdir(parents=True)
    s = {"id": rid, "board": "AHS Simple Flow", "with_frontdoor": with_frontdoor,
         "created_at": NOW.isoformat(), "state": "running"}
    return d, s


def run(d, s, frontdoor_fn=None, monkeypatch=None):
    seen = []

    def fd(ids, each):
        seen.append(list(ids))
        for i in ids:
            each(i, AUTH)
    review.gather(d, s, dossier_fn=dossier_of, open_jobs_fn=lambda board: JOBS,
                  frontdoor_fn=frontdoor_fn or fd)
    return seen


def test_gathering_saves_each_job_records_a_failure_and_reads_frontdoor_once(reviews,
                                                                           monkeypatch):
    monkeypatch.setenv("FRONTDOOR_READ_ENABLED", "true")
    monkeypatch.setenv("FRONTDOOR_USER", "x")
    monkeypatch.setenv("FRONTDOOR_PASSWORD", "y")
    d, s = new_review()
    seen = run(d, s)
    assert sorted(p.stem for p in (d / "customers").glob("*.json")) == ["701", "702", "703"]
    st = review.status(s["id"])
    assert (st["total"], st["done"], st["failed"]) == (3, 3, 1)
    assert seen == [["99000001"]]                       # only the job with an AHS Job ID
    recs = {r["row"]["number"]: r for r in review.customers(s["id"])}
    assert recs["701"]["frontdoor"]["authorization"]["authorized_to_contractor"] == 1125.0
    assert "Zuper said 500" in recs["703"]["error"]

    calls = []
    review.gather(d, s, dossier_fn=lambda uid: calls.append(uid) or dossier_of(uid),
                  open_jobs_fn=lambda board: JOBS, frontdoor_fn=lambda ids, each: calls.append(
                      tuple(ids)))
    assert calls == []                                  # resumed: everything was saved


def test_frontdoor_off_or_failing_never_loses_the_zuper_part(reviews, monkeypatch):
    d, s = new_review()
    assert run(d, s) == []                              # reading is off: no batch at all
    assert "FRONTDOOR_READ_ENABLED" in review.status(s["id"])["frontdoor"]

    monkeypatch.setenv("FRONTDOOR_READ_ENABLED", "true")
    monkeypatch.setenv("FRONTDOOR_USER", "x")
    monkeypatch.setenv("FRONTDOOR_PASSWORD", "y")
    d2 = review.root() / "R20261113-0901-abcd"
    (d2 / "customers").mkdir(parents=True)
    s2 = {**s, "id": d2.name}

    def boom(ids, each):
        raise fd_client.FrontdoorError("Frontdoor did not accept the login")
    run(d2, s2, frontdoor_fn=boom)
    assert "did not accept the login" in review.status(d2.name)["frontdoor"]
    assert review.write_excel(d2).is_file()


def test_the_attention_rules_say_why(reviews, monkeypatch):
    monkeypatch.setenv("FRONTDOOR_READ_ENABLED", "true")
    monkeypatch.setenv("FRONTDOOR_USER", "x")
    monkeypatch.setenv("FRONTDOOR_PASSWORD", "y")
    d, s = new_review()
    run(d, s)
    out = review.read(s["id"])
    by = {c["job"]: c for c in out["customers"]}
    a1 = " | ".join(by["701"]["attention"])
    assert "customer's text is the last word" in a1
    assert "AHS authorized $1,125 on Frontdoor but the job has no proposal" in a1
    a2 = " | ".join(by["702"]["attention"])
    assert "No contact for 6 days" in a2 and "“Waiting on AHS” for 12 days" in a2
    assert "No AHS Job ID" in a2
    assert by["703"]["attention"][0].startswith("Could not read this job")
    assert by["701"]["customer"] == "Ann Ames"
    assert by["701"]["frontdoor"]["customer_owes"] == 475.0
    assert any("Booked Friday" in line for line in by["701"]["conversation"])


def test_pages_only_attention_and_the_excel(reviews):
    d, s = new_review(with_frontdoor=False)
    run(d, s)
    p1 = review.read(s["id"], 0, 2)
    assert [c["job"] for c in p1["customers"]] == ["701", "702"] and p1["next_start"] == 2
    p2 = review.read(s["id"], p1["next_start"], 2)
    assert [c["job"] for c in p2["customers"]] == ["703"] and p2["next_start"] is None
    assert p1["excel"] is None                          # not done yet
    review._save_status(d, review.status(s["id"]), state="done")
    path = review.write_excel(d)
    wb = load_workbook(path)
    assert wb.sheetnames == ["Customers", "History", "Frontdoor"]
    rows = list(wb["Customers"].iter_rows(min_row=2, values_only=True))
    assert [r[0] for r in rows] == ["701", "702", "703"]
    hist = [r[6] for r in wb["History"].iter_rows(min_row=2, values_only=True)]
    assert any("When are you coming?" in str(h) for h in hist)
    assert review.read(s["id"])["excel"] == "/api/owen-ai/reviews/%s/review.xlsx" % s["id"]
    assert review.file_path(s["id"], "review.xlsx") == path.resolve()
    assert review.file_path(s["id"], "status.json") is None
    assert review.file_path("../" + s["id"], "review.xlsx") is None


def test_the_excel_is_the_owners_only(reviews, people):  # noqa: F811
    d, s = new_review(with_frontdoor=False)
    run(d, s)
    review.write_excel(d)
    r = call("GET", "/reviews/%s/review.xlsx" % s["id"], "owner", people)
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert call("GET", "/reviews/%s/review.xlsx" % s["id"], "admin", people).status_code == 404
    assert call("GET", "/reviews/%s/status.json" % s["id"], "owner", people).status_code == 404


def test_start_runs_in_the_background_and_queues_one_message_in_its_chat(reviews, db,
                                                                       monkeypatch):
    th = OwenAiThread(user_id=1, kind="daily", title="Today")
    db.add(th)
    db.commit()
    ran = []
    real = review.gather

    def fake_gather(d, s):
        ran.append(s["id"])
        real(d, s, dossier_fn=dossier_of, open_jobs_fn=lambda board: JOBS,
             frontdoor_fn=lambda ids, each: None)
    monkeypatch.setattr(review, "gather", fake_gather)
    monkeypatch.setattr(tools.proposals, "CURRENT_THREAD", th.id)
    out = tools.start_customer_review()
    assert '"started"' in out
    for t in list(review._THREADS.values()):
        t.join(10)
    s = review.status()
    assert s["state"] == "done" and ran == [s["id"]] and s["thread_id"] == th.id
    db.expire_all()
    msgs = db.query(OwenAiMessage).filter_by(thread_id=th.id).all()
    assert len(msgs) == 1 and msgs[0].kind == "review" and msgs[0].status == "queued"
    assert s["id"] in msgs[0].content and "customer_review_read" in msgs[0].content
    assert '"customers_total": 3' in tools.customer_review_read()


def test_a_second_start_while_one_runs_does_not_start_another(reviews, monkeypatch):
    import threading
    gate = threading.Event()
    monkeypatch.setattr(review, "gather", lambda d, s: gate.wait(10))
    monkeypatch.setattr(review, "notify", lambda s: None)
    first = review.start()
    again = review.start()
    assert again.get("already_running") and again["id"] == first["id"]
    gate.set()
    for t in list(review._THREADS.values()):
        t.join(10)
    assert "already running" in review.started_text({**again, "already_running": True})


# ------------------------------------------------------------------------------ Frontdoor batch

class Portal:
    def __init__(self, bad=(), write=False):
        self.read, self.bad, self.write = [], set(bad), write

        class G:
            def check(g):
                if self.write:
                    raise fd_client.WriteRefused("tried to write")
        self.guard = G()

    def dispatch_detail(self, did):
        self.read.append(did)
        if did in self.bad:
            raise fd_client.FrontdoorError("Frontdoor shows no dispatch %s" % did)
        return {"dispatch_id": did, "cells": {}, "items": [], "autho_text": None,
                "history": []}


@pytest.fixture()
def armed_fd(monkeypatch):
    monkeypatch.setenv("FRONTDOOR_READ_ENABLED", "true")
    monkeypatch.setenv("FRONTDOOR_USER", "x")
    monkeypatch.setenv("FRONTDOOR_PASSWORD", "y")
    fd_service.clear_cache()
    yield
    fd_service.clear_cache()


def install(monkeypatch, portal):
    opened = []

    @contextlib.contextmanager
    def session():
        opened.append(1)
        yield portal
    monkeypatch.setattr(fd_client, "session", session)
    return opened


def test_the_batch_uses_one_session_and_one_bad_page_does_not_stop_it(armed_fd, monkeypatch):
    p = Portal(bad={"99000002"})
    opened = install(monkeypatch, p)
    seen = []
    out = fd_service.dispatch_many(["99000001", "AHS 99000002", "99000003", "99000001", "x"],
                                   lambda i, v: seen.append(i))
    assert opened == [1] and p.read == ["99000001", "99000002", "99000003"]
    assert "no dispatch" in out["99000002"]["error"] and seen == list(out)
    again = fd_service.dispatch_many(["99000001", "99000003"])
    assert opened == [1] and all(v["cached"] for v in again.values())


def test_a_refused_write_stops_the_batch(armed_fd, monkeypatch):
    install(monkeypatch, Portal(write=True))
    with pytest.raises(fd_client.WriteRefused):
        fd_service.dispatch_many(["99000001", "99000002"])


def test_the_batch_refuses_when_off(monkeypatch):
    opened = install(monkeypatch, Portal())
    with pytest.raises(fd_service.FrontdoorOff):
        fd_service.dispatch_many(["99000001"])
    assert opened == []
