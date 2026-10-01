"""Phase 3 (2026-10-01): "the agent does it itself" — BUILT, WIRED, AND OFF.

After a person confirms a change the page suggested (or picks a booking slot, or a stage), they
may tell the agent to make it in Zuper. Five actions, each behind its own switch:

  write_fields   fill / correct one of the job's own custom fields
  write_address  the JOB's service address (never the customer record) — body UNVERIFIED
  write_note     add a note to the job (private, nobody notified)
  write_booking  a visit's scheduled start / end + the job's Technician field
  write_stage    move the job to another stage ON ITS OWN BOARD — never a closing stage

Nothing is written unless ALL of these hold, checked here before a request exists:

  1. the server gate `DISPATCH_ZUPER_WRITES` (zuper.config.dispatch_writes_enabled) — off by
     default, so a switch flipped on a server nobody armed writes nothing;
  2. the Zuper sync armed (ZUPER_SYNC_ENABLED + a key) — the reads need it too;
  3. the page's master switch `writes_enabled` AND that action's own switch (dispatch_settings,
     ADMIN only, a typed confirmation to turn one on, every flip a dispatch_write_log row).

And then the client refuses anything else: every request goes through
`client.dispatch_write()`, whose `DISPATCH_WRITES` lists exactly four requests (PUT /jobs, PUT
/jobs/{uid}/update, PUT /jobs/{uid}/status, POST /jobs/{uid}/note) after the denylist — no
DELETE, no customers, no attachments / photos, no money documents, no sends.

After every write the job is READ BACK and the change is believed only when Zuper shows it.
Either way the outcome is a log row with a sentence for the office. Nothing is retried by
itself: a write whose result is unclear might have landed, and a second one could duplicate a
note or move a job twice. A person looks at Zuper and decides.

The operator runs the Zuper safety check (no customer-facing workflows or notifications that a
field / stage change could set off — see docs/ZUPER-OPERATIONS.md) BEFORE turning any of this
on; the switches do not check it for you.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DispatchJob, DispatchSuggestion, DispatchWriteLog
from ..zuper import client, mapping, zapi
from ..zuper import config as zconfig
from . import ai
from . import config as c

CONFIRM = "TURN ON"
MASTER = "writes_enabled"
SWITCHES: dict[str, str] = {
    MASTER: "Let the agent make changes in Zuper",
    "write_fields": "Fill or correct a job field",
    "write_address": "Change a job's service address",
    "write_note": "Add a note to a job",
    "write_booking": "Book a visit (date, time and the Technician field)",
    "write_stage": "Move a job to another stage",
}
# A suggestion's kind -> the switch that lets the agent apply it.
KIND_SWITCH = {"field": "write_fields", "address": "write_address", "note": "write_note",
               "booking": "write_booking", "stage": "write_stage"}

TECHNICIAN_FIELD = "Technician"
# A stage move never lands on one of these, by name or by Zuper's own status type: closing a
# job (paid, cancelled, declined, reviewed) is a person's decision made in Zuper. COMPLETED is
# NOT a closing type here: Zuper types "Repair Complete" and "Inspection Completed" that way,
# and both are ordinary next steps (review 2026-10-01).
NEVER_STAGES = set(c.CLOSED) | {"Estimate Declined"}
CLOSING_TYPES = {"CANCELED", "CANCELLED", "CLOSED", "PAID"}
# Where Zuper lists a status's checklist questions on GET /jobs/status/{category}.
CHECKLIST_KEYS = ("checklist", "checklists", "status_checklist")
# Copied from the field's current definition onto every custom-field write. Verified live
# 2026-10-01: a PUT that names only label + value makes Zuper drop the field out of its group.
FIELD_META = ("label", "group_name", "group_uid", "type")
FIELD_FLAGS = ("hide_to_fe", "hide_field", "hide", "read_only", "is_read_only")
ZUPER_TIME = "%Y-%m-%d %H:%M:%S"
TIME_SLACK = timedelta(seconds=60)


class Refused(Exception):
    """Not allowed now (a switch, the server gate) — nothing was sent. 409 for the page."""


class Invalid(Exception):
    """The request itself cannot be made (a closing stage, another board) — nothing sent."""


# ------------------------------------------------------------------------------- switches

def _on(s, name: str) -> bool:
    # An unsaved defaults row (ai.settings with no row yet) holds None, not False.
    return bool(getattr(s, name, False))


def why_not(db: Session, kind: str) -> str | None:
    """Why the agent may not make a change of this kind right now, or None."""
    if not zconfig.dispatch_writes_enabled():
        return zconfig.DISPATCH_WRITES_OFF_SENTENCE
    if not zconfig.env_enabled():
        return zconfig.OFF_SENTENCE
    if not zconfig.key_set():
        return zconfig.NO_KEY_SENTENCE
    s = ai.settings(db)
    if not _on(s, MASTER):
        return "“%s” is off (Dispatch → Settings)." % SWITCHES[MASTER]
    switch = KIND_SWITCH[kind]
    if not _on(s, switch):
        return "“%s” is off (Dispatch → Settings)." % SWITCHES[switch]
    return None


def status(db: Session) -> dict:
    """The switches as the page shows them, the server gate, and whether each action would
    run right now (with the reason when not)."""
    s = ai.settings(db)
    recent = db.scalars(select(DispatchWriteLog).order_by(DispatchWriteLog.id.desc()).limit(10))
    return {
        "server_gate": zconfig.dispatch_writes_enabled(),
        "server_gate_sentence": None if zconfig.dispatch_writes_enabled()
        else zconfig.DISPATCH_WRITES_OFF_SENTENCE,
        "confirm_phrase": CONFIRM,
        "switches": [{"key": k, "label": label, "on": _on(s, k)} for k, label in SWITCHES.items()],
        "can": {kind: why_not(db, kind) is None for kind in KIND_SWITCH},
        "why_not": {kind: why_not(db, kind) for kind in KIND_SWITCH},
        "recent": [log_out(r) for r in recent],
    }


def log_out(r: DispatchWriteLog) -> dict:
    at = c.aware(r.at)
    return {"id": r.id, "at": at.isoformat() if at else None, "user_id": r.user_id,
            "action": r.action, "target": r.target, "old": r.old_value, "new": r.new_value,
            "job_uid": r.job_uid, "job_number": r.job_number, "suggestion_id": r.suggestion_id,
            "result": r.result, "sentence": r.sentence}


def set_switches(db: Session, user_id: int | None, changes: dict[str, bool],
                 confirm: str | None) -> list[DispatchWriteLog]:
    """Flip switches. Turning ANY on needs the typed phrase; turning off never does (stopping
    must always be one click). Every real change is a log row with old -> new."""
    unknown = set(changes) - set(SWITCHES)
    if unknown:
        raise Invalid("Unknown switch: %s." % ", ".join(sorted(unknown)))
    s = ai.settings(db, for_update=True)
    flips = {k: v for k, v in changes.items() if _on(s, k) != bool(v)}
    if any(flips.values()) and (confirm or "").strip() != CONFIRM:
        raise Invalid("To turn this on, type %s exactly. It lets the agent change jobs in "
                      "Zuper." % CONFIRM)
    rows = []
    now = datetime.now(UTC)
    for k, v in flips.items():
        old = _on(s, k)
        setattr(s, k, bool(v))
        r = DispatchWriteLog(at=now, user_id=user_id, action="switch", target=k,
                             old_value="on" if old else "off", new_value="on" if v else "off")
        db.add(r)
        rows.append(r)
    if flips:
        s.updated_at, s.updated_by_id = now, user_id
    db.flush()
    return rows


def require(db: Session, kind: str) -> None:
    why = why_not(db, kind)
    if why:
        raise Refused(why)


# ------------------------------------------------------------------------------- helpers

def _norm(value: Any) -> str:
    return " ".join(str("" if value is None else value).lower().split())


def _log(db: Session, user_id: int | None, action: str, j: DispatchJob, *, target: str | None,
         old: str | None, new: str | None, ok: bool | None, sentence: str,
         suggestion_id: int | None = None) -> DispatchWriteLog:
    r = DispatchWriteLog(at=datetime.now(UTC), user_id=user_id, action=action, target=target,
                         old_value=old, new_value=new, job_uid=j.job_uid,
                         job_number=j.job_number, suggestion_id=suggestion_id,
                         result="refused" if ok is None else ("ok" if ok else "failed"),
                         sentence=sentence)
    db.add(r)
    db.flush()
    return r


def _fields(record: dict) -> list[dict]:
    return [f for f in record.get("custom_fields") or [] if isinstance(f, dict)]


def _field(record: dict, label: str) -> dict | None:
    return next((f for f in _fields(record) if f.get("label") == label), None)


def field_body(definition: dict, value: Any) -> dict:
    """One custom field for `PUT /jobs`, its group and type copied from the job's own
    definition (or Zuper strips the group)."""
    out = {"label": definition.get("label"), "value": value}
    for k in FIELD_META[1:]:
        out[k] = definition.get(k)
    for k in FIELD_FLAGS:
        if k in definition:
            out[k] = definition[k]
    return out


def _read_only(definition: dict) -> bool:
    return any(definition.get(k) is True for k in ("read_only", "is_read_only"))


class _Unsent(Exception):
    """Found out before anything was sent: the change cannot be made as asked."""


def _put_field(job_uid: str, definition: dict, value: Any) -> None:
    client.request("PUT", client.path("jobs"), body={"job": {
        "job_uid": job_uid, "custom_fields": [field_body(definition, value)]}})


def _run(write, read_back, *, what: str) -> tuple[bool, str]:
    """Send, then read back. (ok, sentence). A failure after the send says so plainly: it may
    have landed, so a person checks Zuper — nothing here tries again."""
    try:
        with client.dispatch_write():
            write()
    except _Unsent as e:
        return False, "Nothing was sent to Zuper: %s" % e
    except client.ZuperError as e:
        return False, ("Zuper did not take the change (%s). Check the job in Zuper before "
                       "trying again." % client.sentence(e))
    try:
        shown = read_back()
    except client.ZuperError as e:
        return False, ("The change was sent but the job could not be read back (%s). Check "
                       "it in Zuper." % client.sentence(e))
    if shown is True:
        return True, "Done in Zuper: %s." % what
    return False, ("Zuper accepted the request but the job does not show it (%s). Check it in "
                   "Zuper; nothing was retried." % (shown or "it reads differently"))


# ------------------------------------------------------------------------------- actions

def write_field(j: DispatchJob, label: str, value: str) -> tuple[bool, str]:
    def write():
        record = zapi.job(j.job_uid)
        f = _field(record, label)
        if f is None:
            raise _Unsent("the job has no field called “%s” any more." % label)
        if _read_only(f):
            raise _Unsent("Zuper marks “%s” read-only." % label)
        _put_field(j.job_uid, f, value)

    def read_back():
        f = _field(zapi.job(j.job_uid), label)
        if f is not None and _norm(f.get("value")) == _norm(value):
            return True
        return "“%s” reads “%s”" % (label, "" if f is None else f.get("value"))

    return _run(write, read_back, what="“%s” is now “%s”" % (label, value))


ADDRESS_RX = re.compile(r"^\s*(?P<street>[^,]+?)\s*,\s*(?P<city>[^,]+?)\s*,\s*"
                        r"(?P<state>[A-Za-z]{2})\.?\s+(?P<zip>\d{5})(?:-\d{4})?\s*$")


def parse_address(text: str) -> dict | None:
    """"12 Palm Ave, Weston, FL 33326" -> street / city / state / zip_code. Anything else is
    refused rather than guessed."""
    m = ADDRESS_RX.match(text or "")
    if not m:
        return None
    return {"street": m["street"], "city": m["city"], "state": m["state"].upper(),
            "zip_code": m["zip"]}


def write_address(j: DispatchJob, text: str) -> tuple[bool, str]:
    """The JOB's service address. UNVERIFIED body: `PUT /jobs {"job": {job_uid,
    customer_address}}` with the job's current address copied and the parts replaced (no map
    position — Zuper should place the new one). Its switch stays off until a live test on a
    test job confirms the shape."""
    parts = parse_address(text)

    def write():
        if parts is None:
            raise _Unsent("the address must read “street, city, ST 12345”.")
        record = zapi.job(j.job_uid)
        current = record.get("customer_address") if isinstance(
            record.get("customer_address"), dict) else {}
        addr = {k: v for k, v in current.items() if k != "geo_cordinates"}
        addr.update(parts)
        client.request("PUT", client.path("jobs"), body={"job": {
            "job_uid": j.job_uid, "customer_address": addr}})

    def read_back():
        a = zapi.job(j.job_uid).get("customer_address") or {}
        if isinstance(a, dict) and _norm(a.get("street")) == _norm(parts["street"]) and \
                _norm(a.get("city")) == _norm(parts["city"]):
            return True
        return "the address reads “%s”" % (a.get("street") if isinstance(a, dict) else "")

    return _run(write, read_back, what="the job's address is now %s" % text)


def write_note(j: DispatchJob, text: str) -> tuple[bool, str]:
    def write():
        client.request("POST", client.path("job_notes", uid=j.job_uid), body={"note": {
            "note": text, "is_private": True, "notify_users": False}})

    def read_back():
        for n in zapi.job_notes(j.job_uid):
            if not n.get("is_deleted") and _norm(text) in _norm(
                    re.sub(r"<[^>]+>", " ", str(n.get("note") or ""))):
                return True
        return "the note is not on the job"

    return _run(write, read_back, what="the note is on the job")


def write_booking(j: DispatchJob, start: datetime, end: datetime,
                  technician: str) -> tuple[bool, str]:
    start, end = start.astimezone(UTC), end.astimezone(UTC)

    def write():
        record = zapi.job(j.job_uid)
        tech = _field(record, TECHNICIAN_FIELD)
        if tech is None:
            raise _Unsent("the job has no Technician field.")
        if _read_only(tech):
            raise _Unsent("Zuper marks the Technician field read-only.")
        client.request("PUT", client.path("job_update", uid=j.job_uid), body={"job": [{
            "scheduled_start_time": start.strftime(ZUPER_TIME),
            "scheduled_end_time": end.strftime(ZUPER_TIME), "type": "SCHEDULE"}]})
        _put_field(j.job_uid, tech, technician)

    def read_back():
        record = zapi.job(j.job_uid)
        got_start = mapping.parse_time(record.get("scheduled_start_time"))
        got_end = mapping.parse_time(record.get("scheduled_end_time"))
        tech = _field(record, TECHNICIAN_FIELD)
        wrong = []
        if not got_start or abs(got_start - start) > TIME_SLACK or \
                not got_end or abs(got_end - end) > TIME_SLACK:
            wrong.append("the visit reads %s" % (record.get("scheduled_start_time") or "empty"))
        if tech is None or _norm(tech.get("value")) != _norm(technician):
            wrong.append("Technician reads “%s”" % ("" if tech is None else tech.get("value")))
        return True if not wrong else "; ".join(wrong)

    when = c.local(start).strftime("%a %b %d, %I:%M %p")
    return _run(write, read_back, what="booked %s with %s" % (when, technician))


def closing(name: str | None, status_type: str | None = None) -> bool:
    return (name or "").strip() in NEVER_STAGES or \
        (status_type or "").strip().upper() in CLOSING_TYPES


def checklist_count(status: dict) -> int:
    for key in CHECKLIST_KEYS:
        value = status.get(key)
        if isinstance(value, list):
            return len(value)
    return 0


def stage_target(j: DispatchJob, status_name: str) -> None:
    """The checks that need no Zuper read: never a closing stage, never a closed job."""
    if closing(status_name):
        raise Invalid("The agent never moves a job to “%s”: closing a job (paid, cancelled, "
                      "declined, reviewed) is done by a person in Zuper." % status_name)
    if closing(j.status):
        raise Invalid("Job #%s is closed (%s); the agent does not reopen it." % (
            j.job_number, j.status))


def write_stage(j: DispatchJob, status_name: str, remarks: str) -> tuple[bool, str]:
    def write():
        record = zapi.job(j.job_uid)
        cat = record.get("job_category") if isinstance(record.get("job_category"), dict) else {}
        cur = record.get("current_job_status") if isinstance(
            record.get("current_job_status"), dict) else {}
        # By NAME only: a job whose stage Zuper types COMPLETED ("Repair Complete") still moves
        # on to its invoice; a job that is paid / cancelled / declined does not.
        if closing(cur.get("status_name")):
            raise _Unsent("the job is closed in Zuper (%s)." % cur.get("status_name"))
        if _norm(cur.get("status_name")) == _norm(status_name):
            raise _Unsent("the job is already in “%s”." % status_name)
        if not cat.get("category_uid"):
            raise _Unsent("Zuper did not say which board the job is on.")
        if j.board and cat.get("category_name") and cat["category_name"] != j.board:
            raise _Unsent("the job is on “%s” in Zuper now, not “%s”; refresh the page." % (
                cat["category_name"], j.board))
        # Only the job's OWN board's statuses are candidates: a stage of the same name on
        # another board is never reachable from here, so a job cannot change boards.
        target = next((s for s in zapi.statuses(cat["category_uid"])
                       if s.get("status_name") == status_name), None)
        if target is None or not target.get("status_uid"):
            raise _Unsent("“%s” is not a stage on %s; the agent never moves a job to "
                          "another board." % (status_name, cat.get("category_name") or "its "
                                              "board"))
        if closing(target.get("status_name"), target.get("status_type")):
            raise _Unsent("“%s” closes the job; that is done by a person in Zuper." %
                          status_name)
        # A stage with a checklist asks its questions only when a PERSON moves the job in
        # Zuper; a move through the API would skip them, losing the intake / inspection answers
        # and the KPI questions (AHS authorized $, customer chose, customer pays $).
        asks = checklist_count(target)
        if asks:
            raise _Unsent("“%s” asks %d question%s when a job moves into it; move #%s in "
                          "Zuper so they get answered." % (
                              status_name, asks, "" if asks == 1 else "s", j.job_number))
        client.request("PUT", client.path("job_status", uid=j.job_uid), body={
            "status_uid": target["status_uid"], "remarks": remarks})

    def read_back():
        cur = zapi.job(j.job_uid).get("current_job_status") or {}
        name = cur.get("status_name") if isinstance(cur, dict) else None
        return True if name == status_name else "the stage reads “%s”" % (name or "")

    return _run(write, read_back, what="#%s moved to “%s”" % (j.job_number, status_name))


# ------------------------------------------------------------------------------- entry points

def apply_suggestion(db: Session, s: DispatchSuggestion, j: DispatchJob,
                     user_id: int | None) -> tuple[bool, str]:
    """Make one APPROVED suggestion in Zuper. The caller has checked `require` and the state."""
    if s.kind == "field":
        ok, sentence = write_field(j, s.field, s.proposed)
    elif s.kind == "address":
        ok, sentence = write_address(j, s.proposed)
    elif s.kind == "note":
        ok, sentence = write_note(j, s.proposed)
    else:
        raise Invalid("The agent cannot apply a “%s” suggestion." % s.kind)
    now = datetime.now(UTC)
    s.state = "applied" if ok else "apply_failed"
    s.applied_at, s.applied_by_id, s.apply_result = now, user_id, sentence
    _log(db, user_id, s.kind, j, target=s.field, old=s.current, new=s.proposed, ok=ok,
         sentence=sentence, suggestion_id=s.id)
    return ok, sentence


def book(db: Session, j: DispatchJob, start: datetime, end: datetime, technician: str,
         user_id: int | None) -> tuple[bool, str]:
    old = "%s to %s, %s" % (j.scheduled_start, j.scheduled_end, j.technician or "no technician")
    ok, sentence = write_booking(j, start, end, technician)
    _log(db, user_id, "booking", j, target="visit", old=old,
         new="%s to %s, %s" % (start.isoformat(), end.isoformat(), technician), ok=ok,
         sentence=sentence)
    return ok, sentence


def move_stage(db: Session, j: DispatchJob, status_name: str, user_id: int | None,
               who: str | None) -> tuple[bool, str]:
    try:
        stage_target(j, status_name)
    except Invalid as e:
        _log(db, user_id, "stage", j, target=status_name, old=j.status, new=status_name,
             ok=None, sentence=str(e))
        raise
    remarks = "Moved from the CRM's Dispatch page%s." % (" for " + who if who else "")
    ok, sentence = write_stage(j, status_name, remarks)
    _log(db, user_id, "stage", j, target=status_name, old=j.status, new=status_name, ok=ok,
         sentence=sentence)
    return ok, sentence
