"""The automatic texts go out from their own line (2026-10-09, the user: 786-920-0331).

  * With ZUPER_REMINDERS_FROM_NUMBER set, a reminder is handed to the transport with that
    sender, owen-main is asked to send FROM it, and the CRM records it as the source number.
  * A person's text is untouched: it still goes from the CRM line, and no API route can choose a
    sender.
  * Unset: the reminders use the CRM line like everything else.
"""
from datetime import UTC, datetime, timedelta

import pytest
from app import automations, crmlink, transport
from app.models import Contact, DeliveryStatus, DispatchJob, DispatchState, ReminderSettings
from app.reminders import config as c
from app.reminders import service
from app.transport import MessageRef

NOW = datetime(2026, 10, 8, 10, 30, tzinfo=c.TZ).astimezone(UTC)
VISIT = datetime(2026, 10, 9, 14, 0, tzinfo=c.TZ).astimezone(UTC)


class Recorder:
    def __init__(self):
        self.sent = []

    def send_sms(self, to, body, from_number, **kw):
        self.sent.append({"to": to, "from": from_number})
        return MessageRef(provider_ref="r", status=DeliveryStatus.QUEUED)


@pytest.fixture()
def rec(monkeypatch):
    monkeypatch.setenv("ZUPER_REMINDERS_ENABLED", "true")
    r = Recorder()
    monkeypatch.setattr(automations, "get_transport", lambda: r)
    return r


def setup(db):
    db.add(DispatchJob(job_uid="j1", job_number="701", board="Retail", status="Scheduled",
                       scheduled_start=VISIT, scheduled_end=VISIT + timedelta(hours=2),
                       mobile="9415550100", phones=["9415550100"], customer_name="Maria L",
                       is_open=True))
    db.add(DispatchState(id=1, last_success_at=NOW))
    db.add(ReminderSettings(id=1, mode="on"))
    db.commit()


def test_a_reminder_goes_from_its_own_line_and_is_recorded_so(db, rec, monkeypatch):
    monkeypatch.setenv("ZUPER_REMINDERS_FROM_NUMBER", "(786) 920-0331")
    setup(db)
    service.run(db, NOW)
    assert rec.sent == [{"to": "+19415550100", "from": "+17869200331"}]
    from app.models import NumberThreadEvent
    ev = db.query(NumberThreadEvent).one()
    assert ev.source_number == "+17869200331"


def test_unset_it_uses_the_crm_line(db, rec):
    setup(db)
    service.run(db, NOW)
    assert rec.sent[0]["from"] == ""            # "" = the CRM line, as for every other text


def test_a_persons_text_still_goes_from_the_crm_line(db, rec, monkeypatch):
    monkeypatch.setenv("ZUPER_REMINDERS_FROM_NUMBER", "+17869200331")
    contact = Contact(first_name="Ana", phone="+19415550101")
    db.add(contact)
    db.flush()
    automations.send_outbound(db, contact, "We can be there Tuesday")
    assert rec.sent == [{"to": "+19415550101", "from": ""}]


def test_owen_main_is_asked_to_send_from_the_given_line_only_when_one_is_given(monkeypatch):
    posted = []
    monkeypatch.setattr(crmlink, "_post", lambda path, body: posted.append(body) or
                        crmlink.LinkResult(ok=True, data={"message_id": "m1"}))
    t = transport.CrmLinkTransport()
    t.send_sms(to="+19415550100", body="hi", from_number="+17869200331")
    t.send_sms(to="+19415550100", body="hi", from_number="")
    assert posted[0]["from_number"] == "+17869200331"
    assert posted[1]["from_number"] == crmlink.current().from_number
