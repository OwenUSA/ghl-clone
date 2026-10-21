"""The call follow-up's two Zuper writes, inside `client.followup_write()` and nothing else: the
same SMS send and private job note as the auto-reply, in the same exact shape (the client checks
it), under the follow-up's own server gate CALL_FOLLOWUP_ENABLED. Who the customer is comes from
the Dispatch reader's copy of Zuper's jobs - never a new kind of Zuper read."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..autoreply import zconnect as az
from ..models import DispatchJob
from ..zuper import client
from . import config as c


@dataclass
class Customer:
    customer_uid: str | None
    name: str | None
    job_uid: str | None
    job_number: str | None
    test: bool


def _best(jobs: list[DispatchJob]) -> DispatchJob | None:
    jobs = sorted(jobs, key=lambda j: (not j.is_open, -(j.id or 0)))
    return jobs[0] if jobs else None


def test_phones(db: Session, s: c.Settings) -> set[str]:
    """Every number that counts as a test customer: the Zuper TEST customers' numbers as the
    Dispatch reader copied them, and the numbers typed in the agent's settings."""
    out = set(s.test_phones)
    for phones in db.scalars(select(DispatchJob.phones).where(
            DispatchJob.customer_uid.in_(c.TEST_CUSTOMERS))):
        out |= {c.ten(p) for p in phones or [] if c.ten(p)}
    return out


def customer_for(db: Session, phone: str, s: c.Settings) -> Customer:
    """The Zuper customer and job a number belongs to. A typed test number with no job of its
    own belongs to the Zuper TEST customer (so a test can reach your own phone)."""
    jobs = [j for j in db.scalars(select(DispatchJob).where(DispatchJob.customer_uid.isnot(None)))
            if phone in {c.ten(p) for p in j.phones or []}]
    is_test = phone in test_phones(db, s)
    if not jobs and phone in s.test_phones:
        jobs = list(db.scalars(select(DispatchJob).where(
            DispatchJob.customer_uid.in_(c.TEST_CUSTOMERS))))
    # One person only: a number shared by two Zuper customers is nobody's.
    uids = {j.customer_uid for j in jobs}
    if len(uids) != 1:
        return Customer(None, None, None, None, is_test)
    j = _best(jobs)
    return Customer(j.customer_uid, j.customer_name, j.job_uid, j.job_number,
                    is_test or j.customer_uid in c.TEST_CUSTOMERS)


def sender_check() -> str | None:
    return az.sender_check()


def send(customer_uid: str, phone10: str, body: str) -> Any:
    with client.followup_write():
        return client.request("POST", az.SEND_PATH % customer_uid, body={
            "type": "SMS", "sms": {"body": body, "to": "+1" + phone10}})


def add_note(job_uid: str, text: str) -> Any:
    with client.followup_write():
        return client.request("POST", client.path("job_notes", uid=job_uid), body={"note": {
            "note": text, "is_private": True, "notify_users": False}})
