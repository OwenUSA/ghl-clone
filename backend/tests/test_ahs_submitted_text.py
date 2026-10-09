"""The "inspection report submitted to AHS" text (2026-10-09), by behaviour.

  * Only a MOVE into "Submit To AHS…" / "Awaiting AHS Decision" on AHS - Inspection texts — a
    job already there when it is switched on (or after a gap in the watch) never is.
  * ONE text per job: skipping the first column, going Submit -> Awaiting, or back and forth,
    never sends a second.
  * At night the text is held and goes at 8 AM only if the job is still waiting on AHS or further
    along; otherwise it is cancelled. A held text never goes days late.
  * Its own switch: the reminders' mode does not turn it on, nor it them; test mode texts only the
    test numbers.
"""
from datetime import UTC, datetime, timedelta

import pytest
from app import automations
from app.models import (
    AppointmentReminder,
    DeliveryStatus,
    DispatchJob,
    DispatchState,
    ReminderSettings,
)
from app.reminders import config as c
from app.reminders import service
from app.transport import MessageRef
from sqlalchemy import select


def ny(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=c.TZ).astimezone(UTC)


T0 = ny(2026, 10, 9, 10, 0)
MOBILE = "9415550100"
SUBMIT = "Submit To AHS For Approval"
AWAIT = "Awaiting AHS Decision"


class Recorder:
    def __init__(self):
        self.sent = []

    def send_sms(self, to, body, from_number, **kw):
        self.sent.append({"to": to, "body": body})
        return MessageRef(provider_ref="r", status=DeliveryStatus.QUEUED)


@pytest.fixture()
def rec(monkeypatch):
    monkeypatch.setenv("ZUPER_REMINDERS_ENABLED", "true")
    r = Recorder()
    monkeypatch.setattr(automations, "get_transport", lambda: r)
    return r


def job(db, uid="j1", status="Inspection Completed", board="AHS - Inspection", mobile=MOBILE,
        number="801"):
    j = DispatchJob(job_uid=uid, job_number=number, board=board, status=status, mobile=mobile,
                    phones=[mobile], customer_name="Maria Lopez", is_open=True)
    db.add(j)
    db.commit()
    return j


def settings(db, ahs="on", reminders="off", tests=None):
    s = db.get(ReminderSettings, 1) or ReminderSettings(id=1)
    s.mode, s.ahs_submitted_mode, s.test_numbers = reminders, ahs, tests
    db.add(s)
    db.commit()


def tick(db, at):
    st = db.get(DispatchState, 1) or DispatchState(id=1)
    st.last_success_at = at
    db.add(st)
    db.commit()
    return service.run(db, at)


def move(db, j, status, board=None):
    j.status = status
    if board:
        j.board = board
    db.commit()


def rows(db):
    db.expire_all()
    return list(db.scalars(select(AppointmentReminder).where(
        AppointmentReminder.kind == c.AHS_SUBMITTED).order_by(AppointmentReminder.id)))


def test_a_move_into_submit_texts_once_and_awaiting_after_it_does_not(db, rec):
    j = job(db)
    settings(db)
    tick(db, T0)                                  # the first pass only records columns
    assert rec.sent == []
    move(db, j, SUBMIT)
    tick(db, T0 + timedelta(minutes=2))
    assert len(rec.sent) == 1
    body = rec.sent[0]["body"]
    assert body.startswith("Hi Maria, this is Dream Team Roofing. We submitted your inspection "
                           "report to American Home Shield and are waiting for their approval.")
    assert "(954) 914-7244" in body and "STOP" in body
    move(db, j, AWAIT)
    tick(db, T0 + timedelta(minutes=4))
    move(db, j, SUBMIT)
    tick(db, T0 + timedelta(minutes=6))
    assert len(rec.sent) == 1
    [r] = rows(db)
    assert (r.state, r.key) == ("sent", "ahs_submitted:j1")


def test_skipping_straight_to_awaiting_is_the_same_news(db, rec):
    j = job(db)
    settings(db)
    tick(db, T0)
    move(db, j, AWAIT)
    tick(db, T0 + timedelta(minutes=2))
    assert len(rec.sent) == 1


def test_jobs_already_in_the_column_when_switched_on_are_never_texted(db, rec):
    job(db, "a", SUBMIT, number="1")
    job(db, "b", AWAIT, mobile="9415550101", number="2")
    settings(db)
    tick(db, T0)
    tick(db, T0 + timedelta(minutes=2))
    tick(db, T0 + timedelta(minutes=4))
    assert rec.sent == [] and rows(db) == []


def test_after_a_gap_in_the_watch_the_first_pass_only_records(db, rec):
    j = job(db)
    settings(db)
    tick(db, T0)
    move(db, j, SUBMIT)                         # moved while the worker was down for an hour
    tick(db, T0 + timedelta(minutes=c.STAGE_WATCH_STALE_MINUTES + 30))
    assert rec.sent == []


def test_other_boards_and_columns_do_not_text(db, rec):
    a = job(db, "a", number="1")
    b = job(db, "b", "Repair Scheduling Call", board="AHS - Repair & Review",
            mobile="9415550101", number="2")
    settings(db)
    tick(db, T0)
    move(db, a, "AHS Approved")
    move(db, b, "Invoice Submitted to AHS")
    tick(db, T0 + timedelta(minutes=2))
    assert rec.sent == []


def test_a_new_job_created_straight_into_the_column_is_a_move(db, rec):
    job(db, "old", number="1")
    settings(db)
    tick(db, T0)
    job(db, "new", SUBMIT, mobile="9415550101", number="2")
    tick(db, T0 + timedelta(minutes=2))
    assert [s["to"][-10:] for s in rec.sent] == ["9415550101"]


def test_a_night_move_is_held_and_sent_at_8_am_if_still_true(db, rec):
    a = job(db, "a", number="1")
    b = job(db, "b", mobile="9415550101", number="2")
    settings(db)
    night = ny(2026, 10, 9, 21, 0)
    tick(db, night)
    move(db, a, SUBMIT)
    move(db, b, SUBMIT)
    tick(db, night + timedelta(minutes=2))
    assert rec.sent == [] and {r.state for r in rows(db)} == {"waiting"}
    move(db, b, "Reschedule Required")          # b went backwards overnight
    for m in range(0, 11 * 60, 20):             # the watch keeps running through the night
        tick(db, night + timedelta(minutes=4 + m))
    tick(db, ny(2026, 10, 10, 8, 0))
    by = {r.job_uid: r for r in rows(db)}
    assert by["a"].state == "sent" and by["b"].state == "cancelled"
    assert "no longer waiting on AHS" in by["b"].reason
    assert [s["to"][-10:] for s in rec.sent] == [MOBILE]


def test_a_held_text_never_goes_days_late(db, rec):
    a = job(db)
    settings(db)
    night = ny(2026, 10, 9, 21, 0)
    tick(db, night)
    move(db, a, SUBMIT)
    tick(db, night + timedelta(minutes=2))
    settings(db, ahs="off")
    settings(db, ahs="on")
    tick(db, ny(2026, 10, 11, 9, 0))
    assert rec.sent == []
    assert rows(db)[0].state == "cancelled"


def test_its_own_switch_and_test_mode(db, rec):
    a = job(db, "mine", number="1")
    b = job(db, "real", mobile="9415550101", number="2")
    settings(db, ahs="off", reminders="on")
    tick(db, T0)
    move(db, a, SUBMIT)
    tick(db, T0 + timedelta(minutes=2))
    assert rec.sent == []                       # the reminders being on does not turn it on
    settings(db, ahs="test", tests=[MOBILE])
    tick(db, T0 + timedelta(minutes=4))
    move(db, a, "Inspection Completed")
    tick(db, T0 + timedelta(minutes=6))
    move(db, a, SUBMIT)
    move(db, b, SUBMIT)
    tick(db, T0 + timedelta(minutes=8))
    by = {r.job_uid: r for r in rows(db)}
    assert by["mine"].state == "sent" and by["real"].state == "would_send"
    assert [s["to"][-10:] for s in rec.sent] == [MOBILE]


def test_switching_it_on_through_the_api_needs_the_phrase_and_restarts_the_watch(db):
    from app.auth import mint_api_token
    from app.main import app
    from app.models import Role, User
    from fastapi.testclient import TestClient
    u = User(email="a@x.test", name="A", role=Role.ADMIN)
    db.add(u)
    db.flush()
    plain, tok = mint_api_token(u, name="a")
    db.add(tok)
    s = ReminderSettings(id=1, mode="off", stage_watch_at=T0)
    db.add(s)
    db.commit()
    h = {"Authorization": "Bearer " + plain}
    cl = TestClient(app)
    r = cl.put("/api/reminders/settings", json={"ahs_submitted_mode": "on"}, headers=h)
    assert r.status_code == 400 and "TURN ON" in r.text
    r = cl.put("/api/reminders/settings", json={"ahs_submitted_mode": "test", "confirm": "TURN ON"},
               headers=h)
    assert r.status_code == 200 and r.json()["ahs_submitted"]["mode"] == "test"
    assert r.json()["mode"] == "off"                     # the reminders are untouched
    db.expire_all()
    assert db.get(ReminderSettings, 1).stage_watch_at is None
    ok = cl.put("/api/reminders/settings",
                json={"templates": {"ahs_submitted": {"en": "Hi {first_name}, sent to AHS."}}},
                headers=h)
    assert ok.status_code == 200                         # no {time} needed for this one
