"""Zuper's history kept in the CRM for the KPI reports (2026-09-30), against the fake Zuper.

The owner's rules this pins: the CRM only READS Zuper for it, nothing it stores is ever changed
or deleted, and a move is kept exactly as first seen — even after Zuper rewrites it.
"""
from datetime import UTC, datetime

from app.db import SessionLocal
from app.models import ZuperRecordVersion, ZuperStatusHistory
from app.zuper import engine, history, sweep
from sqlalchemy import func, select
from tests.zuper_support import uid_of


def pull(uid: str) -> dict:
    with SessionLocal() as s:
        ctx = engine.Ctx(s)
        engine.pull(ctx, "job", uid)
        s.commit()
        return ctx.counts


def moves(job_uid: str) -> list[ZuperStatusHistory]:
    with SessionLocal() as s:
        return s.scalars(select(ZuperStatusHistory).where(ZuperStatusHistory.job_uid == job_uid)
                         .order_by(ZuperStatusHistory.changed_at)).all()


def versions(module: str | None = None) -> list[ZuperRecordVersion]:
    with SessionLocal() as s:
        stmt = select(ZuperRecordVersion).order_by(ZuperRecordVersion.id)
        if module:
            stmt = stmt.where(ZuperRecordVersion.module == module)
        return s.scalars(stmt).all()


def run(commit: bool = True, now: datetime | None = None) -> dict:
    with SessionLocal() as s:
        return history.run(s, commit=commit, now=now)


def test_a_move_in_zuper_is_kept_once_with_zuper_s_own_time_and_person(loaded, fake):
    uid = uid_of("opportunity", loaded.ids["jane_card"])
    target = uid_of("stage", loaded.ids["stages"]["ahs:Submit Invoices"])
    fake.move_job(uid, target)
    entry = fake.jobs[uid]["job_status"][-1]

    pull(uid)
    pull(uid)                                   # seen twice: stored once

    rows = moves(uid)
    assert len(rows) == len(fake.jobs[uid]["job_status"])
    last = rows[-1]
    assert last.history_uid == entry["status_history_uid"] and last.status_uid == target
    assert last.changed_at.replace(tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%SZ") == entry["created_at"]
    assert last.done_by_name == "Owen Owner"
    assert len({r.history_uid for r in rows}) == len(rows)


def test_the_sweep_keeps_moves_too(loaded, fake):
    uid = uid_of("opportunity", loaded.ids["jane_card"])
    fake.move_job(uid, uid_of("stage", loaded.ids["stages"]["ahs:Submit Invoices"]))
    with SessionLocal() as s:
        sweep.run(s)
    assert [m.history_uid for m in moves(uid)] == [
        e["status_history_uid"] for e in fake.jobs[uid]["job_status"]]


def test_a_move_zuper_later_rewrites_stays_as_first_seen(loaded, fake):
    uid = uid_of("opportunity", loaded.ids["jane_card"])
    fake.move_job(uid, uid_of("stage", loaded.ids["stages"]["ahs:Submit Invoices"]))
    pull(uid)
    first = moves(uid)[-1]
    # A column renamed in Zuper rewrites how its old entries read (seen live 2026-09-25).
    fake.jobs[uid]["job_status"][-1]["status_name"] = "Renamed later"
    fake.edit_job(uid)
    pull(uid)
    again = moves(uid)
    assert again[-1].status_name == first.status_name != "Renamed later"
    assert len(again) == len(fake.jobs[uid]["job_status"])


def test_the_daily_pass_copies_every_record_and_adds_a_version_only_on_change(loaded, fake):
    fake.commissions.append({"commission_uid": "c1", "commission_amount": 500,
                             "updated_at": "2026-09-30T10:00:00Z"})
    first = run()
    jobs = len(fake.jobs)
    assert first["job_new_version"] == jobs and len(versions("job")) == jobs
    assert len(versions("commission")) == 1

    second = run()
    assert "job_new_version" not in second and second["job_unchanged"] == jobs
    assert len(versions("job")) == jobs

    uid = uid_of("opportunity", loaded.ids["jane_card"])
    old_title = fake.jobs[uid]["job_title"]
    fake.edit_job(uid, job_title="Jane Roof - changed in Zuper")
    run()
    jane = [v for v in versions("job") if v.zuper_uid == uid]
    assert [v.record["job_title"] for v in jane] == [old_title, "Jane Roof - changed in Zuper"]


def test_the_pass_only_reads_zuper(loaded, fake):
    before = len(fake.writes())
    run()
    assert fake.writes()[before:] == []


def test_a_dry_run_reports_and_writes_nothing(loaded, fake):
    counts = run(commit=False)
    assert counts["job_new_version"] == len(fake.jobs) and counts["status_moves_added"] > 0
    with SessionLocal() as s:
        assert s.scalar(select(func.count(ZuperRecordVersion.id))) == 0


def test_the_worker_runs_the_pass_once_a_day_after_two_am(loaded, fake):
    morning = datetime(2026, 10, 1, 5, 30, tzinfo=UTC)       # 01:30 in New York
    later = datetime(2026, 10, 1, 7, 0, tzinfo=UTC)          # 03:00
    with SessionLocal() as s:
        assert history.run_daily(s, morning) is None
        assert history.run_daily(s, later) is not None
        assert history.run_daily(s, later) is None
    assert len(versions("job")) == len(fake.jobs)


def test_a_capture_failure_never_breaks_the_pull(loaded, fake, monkeypatch):
    uid = uid_of("opportunity", loaded.ids["jane_card"])
    target = loaded.ids["stages"]["ahs:Submit Invoices"]

    def boom(job):
        raise RuntimeError("history broke")
    monkeypatch.setattr(history, "status_rows", boom)
    fake.move_job(uid, uid_of("stage", target))
    pull(uid)
    from app.models import Opportunity
    with SessionLocal() as s:
        assert s.get(Opportunity, loaded.ids["jane_card"]).stage_id == target
