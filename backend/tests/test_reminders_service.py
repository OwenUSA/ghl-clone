"""Appointment reminders from Zuper (2026-10-08): the pass and its routes, by behaviour.

  * **Off means nothing leaves:** without ZUPER_REMINDERS_ENABLED, or with the mode off (the
    default), a pass sends nothing and writes no reminder row.
  * **Test mode** texts only the test numbers; every other reminder is a `would_send` row, and
    switching On later still sends one whose window is open.
  * **Exactly once:** the row is committed BEFORE the text is handed over; a second pass, a
    crash, a refusal — none of them sends twice.
  * **Stale data sends nothing**, and neither does a burst of more than MAX_PER_PASS.
  * The text goes to the Zuper MOBILE, lands on the contact's thread (DND honoured) or on a
    number-only thread, in the customer's language.
  * Settings are ADMIN with "TURN ON"; a technician or a restricted user is refused 403; a
    hidden board is left out of the log.
"""
from datetime import UTC, datetime, timedelta

import pytest
from app import automations
from app.auth import mint_api_token
from app.main import app
from app.models import (
    AppointmentReminder,
    Contact,
    Conversation,
    ConversationEvent,
    CustomerLanguage,
    DeliveryStatus,
    Direction,
    DispatchJob,
    DispatchState,
    EventType,
    NumberThread,
    NumberThreadEvent,
    Pipeline,
    PipelinePermission,
    ReminderSettings,
    ReminderSwitchLog,
    Role,
    User,
)
from app.reminders import config as c
from app.reminders import service
from app.transport import MessageRef
from fastapi.testclient import TestClient
from sqlalchemy import func, select


def ny(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=c.TZ).astimezone(UTC)


NOW = ny(2026, 10, 8, 10, 30)               # Thursday 10:30 AM
VISIT = ny(2026, 10, 9, 14, 0)              # Friday 2 PM -> day-before due now
MOBILE = "9415550100"
OTHER = "9415550199"


class Recorder:
    def __init__(self, status=DeliveryStatus.QUEUED, detail=""):
        self.sent, self.status, self.detail = [], status, detail

    def send_sms(self, to, body, from_number, **kw):
        self.sent.append({"to": to, "body": body})
        return MessageRef(provider_ref="r%d" % len(self.sent), status=self.status,
                          detail=self.detail)


@pytest.fixture()
def rec(monkeypatch):
    r = Recorder()
    monkeypatch.setattr(automations, "get_transport", lambda: r)
    return r


@pytest.fixture()
def gate(monkeypatch):
    monkeypatch.setenv("ZUPER_REMINDERS_ENABLED", "true")


def job(db, uid="j1", *, number="701", board="Retail", status="Scheduled", start=VISIT,
        end="2h", mobile=MOBILE, phones=None, name="Maria Lopez", is_open=True):
    if end == "2h":
        end = start + timedelta(hours=2) if start else None
    j = DispatchJob(job_uid=uid, job_number=number, board=board, status=status,
                    scheduled_start=start, scheduled_end=end, mobile=mobile,
                    phones=phones if phones is not None else ([mobile] if mobile else None),
                    customer_name=name, is_open=is_open)
    db.add(j)
    db.flush()
    return j


def fresh(db, at=NOW):
    s = db.get(DispatchState, 1) or DispatchState(id=1)
    s.last_success_at = at
    db.add(s)
    db.flush()


def mode(db, m, tests=None):
    s = db.get(ReminderSettings, 1) or ReminderSettings(id=1)
    s.mode, s.test_numbers = m, tests
    db.add(s)
    db.commit()


def rows(db):
    db.expire_all()
    return list(db.scalars(select(AppointmentReminder).order_by(AppointmentReminder.id)))


def outbound(db):
    return (db.query(ConversationEvent).filter(
        ConversationEvent.direction == Direction.OUTBOUND).count()
        + db.query(NumberThreadEvent).filter(
            NumberThreadEvent.direction == Direction.OUTBOUND).count())


# --- off ---------------------------------------------------------------------------------

def test_without_the_server_gate_nothing_is_sent_whatever_the_mode(db, rec):
    job(db)
    fresh(db)
    mode(db, "on")
    assert "off" in service.run(db, NOW)
    assert rec.sent == [] and rows(db) == [] and outbound(db) == 0


def test_the_mode_is_off_by_default(db, rec, gate):
    job(db)
    fresh(db)
    db.commit()
    assert service.run(db, NOW) == {"off": "mode is off"}
    assert rec.sent == [] and rows(db) == []
    assert db.get(ReminderSettings, 1) is None            # a pass while off writes nothing


# --- on ----------------------------------------------------------------------------------

def test_on_sends_the_day_before_text_once_to_the_number_thread(db, rec, gate):
    job(db)
    fresh(db)
    mode(db, "on")
    counts = service.run(db, NOW)
    assert counts["sent"] == 1, counts
    assert len(rec.sent) == 1
    assert rec.sent[0]["to"].endswith(MOBILE)
    body = rec.sent[0]["body"]
    assert body.startswith("Hi Maria, this is Dream Team Roofing")
    assert body == ("Hi Maria, this is Dream Team Roofing. Reminder: our visit is tomorrow, "
                    "Friday, October 9, from 2 PM to 4 PM. Text or call (954) 914-7244 if you "
                    "need to reschedule. Reply STOP to opt out.")
    [r] = rows(db)
    assert (r.state, r.kind, r.job_number, r.language) == ("sent", "day_before", "701", "en")
    t = db.scalar(select(NumberThread).where(NumberThread.phone_key == MOBILE))
    assert r.number_thread_id == t.id and outbound(db) == 1
    # A second pass (and one a minute later) sends nothing more.
    service.run(db, NOW + timedelta(minutes=2))
    service.run(db, NOW + timedelta(hours=3))
    assert len(rec.sent) == 1 and len(rows(db)) == 1


def test_the_four_hour_text_then_goes_on_the_visit_day(db, rec, gate):
    job(db)
    fresh(db, ny(2026, 10, 9, 10, 0))
    mode(db, "on")
    service.run(db, ny(2026, 10, 9, 10, 0))
    assert [r.kind for r in rows(db)] == ["four_hour"]
    body = rec.sent[0]["body"]
    assert "our visit is today from 2 PM to 4 PM. Text or call (954) 914-7244" in body


def test_a_visit_with_no_end_or_one_on_another_day_says_at_the_start_time(db, rec, gate):
    job(db, end=None, start=ny(2026, 10, 9, 14, 30))
    job(db, "j2", number="702", mobile=OTHER, end=ny(2026, 10, 10, 9, 0))
    fresh(db)
    mode(db, "on")
    service.run(db, NOW)
    bodies = sorted(x["body"] for x in rec.sent)
    assert "tomorrow, Friday, October 9, at 2:30 PM. Text or call" in bodies[1]
    assert "tomorrow, Friday, October 9, at 2 PM. Text or call" in bodies[0]


def test_a_contact_holding_the_number_gets_it_on_their_thread_and_dnd_is_honoured(
        db, rec, gate):
    c1 = Contact(first_name="Ana", last_name="Diaz", phone="+1 (941) 555-0100")
    db.add(c1)
    job(db, name="Zuper Name")
    fresh(db)
    mode(db, "on")
    service.run(db, NOW)
    [r] = rows(db)
    assert r.state == "sent" and r.contact_id == c1.id
    assert rec.sent[0]["body"].startswith("Hi Ana,")
    conv = db.scalar(select(Conversation).where(Conversation.contact_id == c1.id))
    assert db.query(ConversationEvent).filter_by(conversation_id=conv.id).count() == 1

    job(db, "j2", number="702", mobile=OTHER)
    c2 = Contact(first_name="Do", last_name="Not", phone=OTHER, dnd=True)
    db.add(c2)
    db.commit()
    service.run(db, NOW + timedelta(minutes=2))
    r2 = rows(db)[-1]
    assert r2.state == "suppressed" and "DND" in r2.reason
    assert len(rec.sent) == 1


def test_a_refusal_is_recorded_and_never_retried(db, monkeypatch, gate):
    r = Recorder(DeliveryStatus.REFUSED, "This number has opted out of texts")
    monkeypatch.setattr(automations, "get_transport", lambda: r)
    job(db)
    fresh(db)
    mode(db, "on")
    service.run(db, NOW)
    service.run(db, NOW + timedelta(minutes=5))
    [row] = rows(db)
    assert row.state == "refused" and "opted out" in row.reason
    assert len(r.sent) == 1


def test_a_crash_while_sending_is_never_sent_twice(db, monkeypatch, gate):
    class Boom:
        calls = 0

        def send_sms(self, *a, **kw):
            Boom.calls += 1
            raise RuntimeError("socket closed")
    monkeypatch.setattr(automations, "get_transport", lambda: Boom())
    job(db)
    fresh(db)
    mode(db, "on")
    service.run(db, NOW)
    service.run(db, NOW + timedelta(minutes=5))
    [row] = rows(db)
    assert row.state == "failed" and "Not retried" in row.reason
    assert Boom.calls == 1


def test_a_row_left_sending_by_a_dead_worker_blocks_a_second_text(db, rec, gate):
    job(db)
    fresh(db)
    mode(db, "on")
    from app.reminders import rules
    db.add(AppointmentReminder(key=rules.key_for("j1", VISIT, "day_before"), job_uid="j1",
                               kind="day_before", visit_start=VISIT, state="sending"))
    db.commit()
    service.run(db, NOW)
    assert rec.sent == []


def test_stale_zuper_data_sends_nothing_and_says_why(db, rec, gate):
    job(db)
    fresh(db, NOW - timedelta(minutes=c.STALE_MINUTES + 1))
    mode(db, "on")
    counts = service.run(db, NOW)
    assert "have not been read" in counts["error"]
    assert rec.sent == [] and rows(db) == []
    assert "have not been read" in db.get(ReminderSettings, 1).last_error


def test_a_burst_sends_nothing(db, rec, gate):
    for i in range(c.MAX_PER_PASS + 1):
        job(db, "j%d" % i, number=str(800 + i), mobile="94155%05d" % i)
    fresh(db)
    mode(db, "on")
    counts = service.run(db, NOW)
    assert "none was sent" in counts["error"]
    assert rec.sent == [] and rows(db) == []


def test_columns_that_do_not_count_and_closed_jobs_get_nothing(db, rec, gate):
    job(db, "a", board="AHS - Inspection", status="Welcome Call!")
    job(db, "b", board="AHS - Repair & Review", status="Day-Before Call", mobile=OTHER)
    job(db, "c", is_open=False, mobile="9415550111")
    job(db, "d", board="AHS - Inspection", status="Inspection: Day-Before Cal",
        mobile="9415550122")
    fresh(db)
    mode(db, "on")
    service.run(db, NOW)
    assert [r.job_uid for r in rows(db)] == ["d"]


def test_no_mobile_and_two_numbers_is_skipped_one_number_is_used(db, rec, gate):
    job(db, "a", mobile=None, phones=["9415550111", "9415550122"])
    job(db, "b", mobile=None, phones=["9415550133"])
    fresh(db)
    mode(db, "on")
    service.run(db, NOW)
    by = {r.job_uid: r for r in rows(db)}
    assert by["a"].state == "skipped" and "no mobile" in by["a"].reason
    assert by["b"].state == "sent" and rec.sent[0]["to"].endswith("9415550133")


def test_a_rescheduled_visit_gets_its_own_reminder(db, rec, gate):
    j = job(db)
    fresh(db)
    mode(db, "on")
    service.run(db, NOW)
    j.scheduled_start, j.scheduled_end = ny(2026, 10, 9, 16, 0), ny(2026, 10, 9, 18, 0)
    db.commit()
    service.run(db, NOW + timedelta(minutes=2))
    assert len(rec.sent) == 2 and "from 4 PM to 6 PM" in rec.sent[1]["body"]


# --- test mode ---------------------------------------------------------------------------

def test_test_mode_texts_only_the_test_numbers_and_on_later_still_sends(db, rec, gate):
    job(db, "mine", mobile=MOBILE)
    job(db, "real", number="702", mobile=OTHER)
    fresh(db)
    mode(db, "test", [MOBILE])
    service.run(db, NOW)
    by = {r.job_uid: r for r in rows(db)}
    assert by["mine"].state == "sent"
    assert by["real"].state == "would_send" and by["real"].body.startswith("Hi Maria")
    assert by["real"].key.startswith(service.PREVIEW)
    assert [s["to"][-10:] for s in rec.sent] == [MOBILE]
    # Switched On while the window is still open: the real customer gets it, once.
    mode(db, "on")
    fresh(db, NOW + timedelta(minutes=5))
    service.run(db, NOW + timedelta(minutes=5))
    service.run(db, NOW + timedelta(minutes=7))
    assert [s["to"][-10:] for s in rec.sent] == [MOBILE, OTHER]


# --- language ----------------------------------------------------------------------------

def spanish_texts(db, phone):
    c1 = Contact(first_name="José", phone=phone)
    db.add(c1)
    db.flush()
    conv = Conversation(contact_id=c1.id)
    db.add(conv)
    db.flush()
    for body in ("Hola, sí, mañana está bien", "Gracias por la cita, la casa está lista"):
        db.add(ConversationEvent(conversation_id=conv.id, type=EventType.SMS,
                                 direction=Direction.INBOUND, body=body,
                                 occurred_at=NOW - timedelta(days=2)))
    db.flush()
    return c1


def test_a_customer_who_writes_in_spanish_is_reminded_in_spanish_and_it_is_saved(
        db, rec, gate):
    spanish_texts(db, MOBILE)
    job(db)
    fresh(db)
    mode(db, "on")
    service.run(db, NOW)
    body = rec.sent[0]["body"]
    assert body.startswith("Hola José, le saluda Dream Team Roofing")
    assert ("mañana, viernes 9 de octubre, de 2 PM a 4 PM. Si necesita cambiarla, escriba o "
            "llame al (954) 914-7244") in body
    row = db.scalar(select(CustomerLanguage).where(CustomerLanguage.phone_key == MOBILE))
    assert (row.language, row.source) == ("es", "auto")


def test_a_manual_language_wins_and_is_never_overwritten(db, rec, gate):
    from app.reminders import language
    spanish_texts(db, MOBILE)
    language.set_manual(db, MOBILE, "en", None)
    job(db)
    fresh(db)
    mode(db, "on")
    service.run(db, NOW)
    assert rec.sent[0]["body"].startswith("Hi José")
    assert db.scalar(select(CustomerLanguage)).source == "manual"


# --- routes ------------------------------------------------------------------------------

@pytest.fixture()
def world(db):
    people = {}
    for key, role, restricted in (("admin", Role.ADMIN, False),
                                  ("dispatcher", Role.DISPATCHER, False),
                                  ("restricted", Role.DISPATCHER, True),
                                  ("tech", Role.TECH, False)):
        u = User(email="%s@x.test" % key, name=key.title(), role=role,
                 only_assigned_data=restricted)
        db.add(u)
        db.flush()
        plain, tok = mint_api_token(u, name=key)
        db.add(tok)
        people[key] = (u.id, plain)
    db.commit()

    def as_(who):
        return {"Authorization": "Bearer " + people[who][1]}

    return TestClient(app), as_, people


def test_turning_reminders_on_needs_an_admin_and_the_typed_phrase(db, world):
    c_, as_, people = world
    r = c_.put("/api/reminders/settings", json={"mode": "on"}, headers=as_("admin"))
    assert r.status_code == 400 and "TURN ON" in r.text
    assert c_.put("/api/reminders/settings", json={"mode": "on", "confirm": "TURN ON"},
                  headers=as_("dispatcher")).status_code == 403
    assert db.get(ReminderSettings, 1) is None or db.get(ReminderSettings, 1).mode == "off"
    r = c_.put("/api/reminders/settings",
               json={"mode": "test", "confirm": "TURN ON", "test_numbers": ["(941) 555-0100"]},
               headers=as_("admin"))
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["mode"] == "test" and got["test_numbers"] == [MOBILE]
    assert got["sending"] is False and "ZUPER_REMINDERS_ENABLED" in got["sentence"]
    # Off is one press, no phrase.
    assert c_.put("/api/reminders/settings", json={"mode": "off"},
                  headers=as_("admin")).json()["mode"] == "off"
    db.expire_all()
    log = [(x.field, x.old_value, x.new_value) for x in db.scalars(
        select(ReminderSwitchLog).order_by(ReminderSwitchLog.id))]
    assert ("mode", "off", "test") in log and ("mode", "test", "off") in log
    assert log[0][0] in ("mode", "test_numbers")


def test_wording_can_be_edited_must_keep_the_time_and_resets_to_default(db, world):
    c_, as_, _ = world
    bad = c_.put("/api/reminders/settings",
                 json={"templates": {"day_before": {"en": "See you tomorrow!"}}},
                 headers=as_("admin"))
    assert bad.status_code == 400 and "{window}" in bad.text
    ok = c_.put("/api/reminders/settings",
                json={"templates": {"day_before": {"en": "Hi {first_name}, tomorrow {time}."}}},
                headers=as_("admin")).json()
    assert ok["templates"]["day_before"]["en"] == "Hi {first_name}, tomorrow {time}."
    back = c_.put("/api/reminders/settings", json={"templates": {"day_before": {"en": ""}}},
                  headers=as_("admin")).json()
    assert back["templates"]["day_before"]["en"] == c.DEFAULT_TEMPLATES["day_before"]["en"]


def test_a_technician_and_a_restricted_user_are_refused(db, world):
    c_, as_, _ = world
    for who in ("tech", "restricted"):
        for path in ("/api/reminders", "/api/reminders/log", "/api/reminders/upcoming",
                     "/api/reminders/language?phone=9415550100"):
            assert c_.get(path, headers=as_(who)).status_code == 403, (who, path)
        assert c_.put("/api/reminders/language", json={"phone": MOBILE, "language": "es"},
                      headers=as_(who)).status_code == 403
    assert db.query(CustomerLanguage).count() == 0
    assert c_.get("/api/reminders").status_code == 401


def test_a_dispatcher_sets_a_customer_language_and_reads_it_back(db, world):
    c_, as_, _ = world
    r = c_.put("/api/reminders/language", json={"phone": "+1 941 555 0100", "language": "es"},
               headers=as_("dispatcher"))
    assert r.status_code == 200 and r.json()["source"] == "manual"
    got = c_.get("/api/reminders/language?phone=9415550100", headers=as_("dispatcher")).json()
    assert (got["language"], got["source"]) == ("es", "manual")
    back = c_.put("/api/reminders/language", json={"phone": MOBILE, "language": None},
                  headers=as_("dispatcher")).json()
    assert back["source"] == "auto" and back["language"] == "en"


def test_the_log_and_the_plan_leave_out_a_hidden_board(db, world):
    c_, as_, people = world
    db.add(AppointmentReminder(key="k1", job_uid="r1", board="Retail", kind="day_before",
                               visit_start=VISIT, state="sent"))
    db.add(AppointmentReminder(key="k2", job_uid="a1", board="AHS - Inspection",
                               kind="day_before", visit_start=VISIT, state="sent"))
    p = Pipeline(name="AHS - Inspection")
    db.add(p)
    db.flush()
    db.add(PipelinePermission(pipeline_id=p.id, user_id=people["admin"][0]))
    db.commit()
    got = c_.get("/api/reminders/log", headers=as_("dispatcher")).json()["reminders"]
    assert [x["job_uid"] for x in got] == ["r1"]
    assert db.query(func.count(AppointmentReminder.id)).scalar() == 2
