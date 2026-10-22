"""The registry's database half: levels, caps, spend, health, failure alerts, the activity feed.

    level(db, key, action)           the level an action is at (a person's row, else the default)
    refusal(db, key, action, now)    why that action may not run right now, or None — the level
                                     is Never, the entry's own cap, or the overall cap
    alert_failure(db, key, sentence) the bell, once per entry per New York day (Q13)
    overview(db, now)                every entry with its scope, levels, status, spend, health
    activity(db, key=None)           what the entries did, newest first, from their own logs

Nothing here sends, calls or writes to Zuper. A module asks `refusal` before it acts; the
registry never acts itself.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..dispatch.config import local
from ..models import (
    AgentPermission,
    AgentRegistryEntry,
    AgentRegistryLog,
    AiAgent,
    AiAlert,
    AiConnection,
    AiRun,
    AiSettings,
    AutoReply,
    CallFollowup,
    DispatchAiRun,
    DispatchSettings,
    DispatchWriteLog,
    InspectionCheck,
    Role,
    User,
)
from . import catalog as cat

log = logging.getLogger("registry")

DEFAULT_OVERALL_CAP_MICROS = 25_000_000          # $25 a day, every agent together (Q27)
PROMOTE_CONFIRM = "PROMOTE"
MICRO = 1_000_000


# Dispatch AI runs (`dispatch_ai_runs.kind`) that belong to an entry other than the assistant.
DISPATCH_AI_KINDS = {"availability": "availability_reader", "inspection": "inspection_docs",
                     "briefing": "tech_briefing", "payments": "payments",
                     "insights": "insights"}


def _dispatch_kind_filter(key: str):
    if key == "dispatch_assistant":
        return DispatchAiRun.kind.notin_(tuple(DISPATCH_AI_KINDS))
    return DispatchAiRun.kind.in_([k for k, v in DISPATCH_AI_KINDS.items() if v == key])


class Invalid(ValueError):
    """A change the registry refuses, with the sentence to show."""


@dataclass
class View:
    """One entry as the page and the gates see it."""
    key: str
    name: str
    kind: str
    role: int | None
    purpose: str
    covers: str
    channels: tuple[str, ...]
    runs_on: str
    provider: str | None
    hours: str
    never: tuple[str, ...]
    actions: tuple[cat.Action, ...]
    owner_hint: str | None = None
    outside: bool = False
    archived: bool = False
    planned: str | None = None


# ------------------------------------------------------------------------------ time

def day_start(now: datetime) -> datetime:
    return local(now).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


# ------------------------------------------------------------------------------ entries

def _ai_view(a: AiAgent) -> View:
    voice = a.channel == AiAgent.VOICE
    return View(
        key=cat.ai_agent_key(a.id), name=a.name, kind="agent", role=1 if voice else None,
        purpose=(a.description or "").strip() or (
            "Answers calls on its phone numbers." if voice else "A text agent built in AI Agents."),
        covers="The numbers routed to it on the phone system." if voice
        else "The triggers in its builder.",
        channels=("Phone (Retell / owen-main)",) if voice else ("CRM text",),
        runs_on="owen-main" if voice else "CRM worker",
        provider="Retell" if voice else None,
        hours="The phone system's call flow" if voice else "Its schedule in the builder",
        never=(), actions=cat.ai_agent_actions(a.channel, a.mode, bool(a.answering_calls)),
        archived=a.archived_at is not None)


def _cat_view(e: cat.Entry) -> View:
    return View(key=e.key, name=e.name, kind=e.kind, role=e.role, purpose=e.purpose,
                covers=e.covers, channels=e.channels, runs_on=e.runs_on, provider=e.provider,
                hours=e.hours, never=e.never, actions=e.actions, owner_hint=e.owner_hint,
                outside="outside" in e.tags, planned=e.planned)


def views(db: Session, *, include_archived: bool = False) -> list[View]:
    out = [_cat_view(e) for e in cat.ENTRIES]
    q = select(AiAgent).order_by(AiAgent.id)
    if not include_archived:
        q = q.where(AiAgent.archived_at.is_(None))
    out += [_ai_view(a) for a in db.scalars(q)]
    return out


def view(db: Session, key: str) -> View | None:
    aid = cat.ai_agent_id(key)
    if aid is not None:
        a = db.get(AiAgent, aid)
        return _ai_view(a) if a is not None else None
    e = cat.BY_KEY.get(key)
    return _cat_view(e) if e else None


def settings_row(db: Session, key: str) -> AgentRegistryEntry | None:
    return db.get(AgentRegistryEntry, key)


def _row(db: Session, key: str) -> AgentRegistryEntry:
    r = db.get(AgentRegistryEntry, key)
    if r is None:
        r = AgentRegistryEntry(key=key)
        db.add(r)
        db.flush()
    return r


# ------------------------------------------------------------------------------ levels

def level(db: Session, key: str, action: str) -> str:
    v = view(db, key)
    if v is None:
        return cat.NEVER
    act = next((x for x in v.actions if x.action == action), None)
    if act is None:
        return cat.NEVER                       # an action outside the scope is never allowed
    if not act.choices:
        return act.default                     # set by its own switch or outside the CRM
    row = db.scalar(select(AgentPermission).where(AgentPermission.entry_key == key,
                                                  AgentPermission.action == action))
    return row.level if row is not None and row.level in act.choices else act.default


def set_level(db: Session, key: str, action: str, new: str, *, user_id: int | None,
              reason: str | None, confirm: str | None) -> AgentRegistryLog | None:
    v = view(db, key)
    if v is None:
        raise Invalid("There is no registry entry %s." % key)
    act = next((x for x in v.actions if x.action == action), None)
    if act is None:
        raise Invalid("%s cannot do “%s”." % (v.name, cat.ACTIONS.get(action, {}).get(
            "label", action)))
    if not act.choices:
        raise Invalid("“%s” for %s is not set here. %s" % (
            cat.ACTIONS[action]["label"], v.name, act.note or "It has its own switch."))
    if new not in act.choices:
        raise Invalid("%s's “%s” can be %s." % (v.name, cat.ACTIONS[action]["label"], ", ".join(
            cat.LEVEL_LABEL[c] for c in act.choices)))
    old = level(db, key, action)
    if old == new:
        return None
    reason = (reason or "").strip()
    if cat.LEVEL_RANK[new] > cat.LEVEL_RANK[old]:
        if not reason:
            raise Invalid("Say why it is being promoted. The reason is kept with the change.")
        if (confirm or "").strip() != PROMOTE_CONFIRM:
            raise Invalid("To give it more freedom, type %s exactly." % PROMOTE_CONFIRM)
    now = datetime.now(UTC)
    row = db.scalar(select(AgentPermission).where(AgentPermission.entry_key == key,
                                                  AgentPermission.action == action))
    if row is None:
        row = AgentPermission(entry_key=key, action=action, level=new)
        db.add(row)
    row.level, row.changed_at, row.changed_by_id, row.reason = new, now, user_id, reason or None
    entry = AgentRegistryLog(at=now, entry_key=key, user_id=user_id, what="level",
                             target=action, old_value=old, new_value=new, reason=reason or None)
    db.add(entry)
    db.flush()
    return entry


# ------------------------------------------------------------------------------ spend

def _price(db: Session, conn_id: int | None) -> tuple[str, int | None, int | None] | None:
    conn = db.get(AiConnection, conn_id) if conn_id else None
    if conn is None:
        return None
    return conn.provider, conn.price_input_micros, conn.price_output_micros


def _token_cost(price, tin: int, tout: int) -> int | None:
    if not tin and not tout:
        return 0
    if price is None or price[1] is None or price[2] is None:
        return None
    from ..ai import pricing
    return pricing.cost_micros(price[0], price[1], price[2], input_tokens=tin,
                               output_tokens=tout)


def _dispatch_settings(db: Session) -> DispatchSettings | None:
    return db.get(DispatchSettings, 1)


def spend_today(db: Session, key: str, now: datetime) -> int | None:
    """Micro-dollars this entry spent on its model today (New York day). None = unknown (the
    connection has no price). Voice minutes are owen-main's to count (`/agent-spend`)."""
    start = day_start(now)
    aid = cat.ai_agent_id(key)
    if aid is not None:
        rows = db.execute(select(func.coalesce(func.sum(AiRun.cost_micros), 0),
                                 func.count().filter(and_(AiRun.cost_micros.is_(None),
                                                          AiRun.input_tokens > 0)))
                          .where(AiRun.agent_id == aid, AiRun.created_at >= start,
                                 AiRun.is_test.is_(False))).one()
        return None if rows[1] else int(rows[0])
    s = _dispatch_settings(db)
    if key == "inspection_report":
        from ..models import InspectionReport
        v = db.scalar(select(func.coalesce(func.sum(InspectionReport.cost_micros), 0))
                      .where(InspectionReport.updated_at >= start))
        return int(v or 0)
    if key == "autoreply":
        tin, tout = db.execute(select(func.coalesce(func.sum(AutoReply.input_tokens), 0),
                                      func.coalesce(func.sum(AutoReply.output_tokens), 0))
                               .where(AutoReply.created_at >= start)).one()
        return _token_cost(_price(db, s.autoreply_connection_id if s else None), tin, tout)
    if key in ("dispatch_assistant", *DISPATCH_AI_KINDS.values()):
        cond = _dispatch_kind_filter(key)
        tin, tout = db.execute(select(func.coalesce(func.sum(DispatchAiRun.input_tokens), 0),
                                      func.coalesce(func.sum(DispatchAiRun.output_tokens), 0))
                               .where(DispatchAiRun.created_at >= start, cond)).one()
        return _token_cost(_price(db, s.connection_id if s else None), tin, tout)
    return 0


def overall_cap(db: Session) -> int:
    s = db.get(AiSettings, 1)
    v = s.overall_daily_cap_micros if s is not None else None
    return DEFAULT_OVERALL_CAP_MICROS if v is None else v


def overall_today(db: Session, now: datetime) -> tuple[int, bool]:
    """(micro-dollars every entry spent today, whether any of it is unknown)."""
    total, unknown = 0, False
    for v in views(db, include_archived=True):
        x = spend_today(db, v.key, now)
        if x is None:
            unknown = True
        else:
            total += x
    return total, unknown


def money(micros: int | None) -> str | None:
    return None if micros is None else "$%.2f" % (micros / MICRO)


def cap_refusal(db: Session, key: str, now: datetime) -> str | None:
    r = settings_row(db, key)
    v = view(db, key)
    name = v.name if v else key
    if r is not None and r.daily_cap_micros is not None:
        spent = spend_today(db, key, now) or 0
        if spent >= r.daily_cap_micros:
            return ("%s reached its daily limit of %s; it resets at midnight (AI Agents → "
                    "Overview)." % (name, money(r.daily_cap_micros)))
    cap = overall_cap(db)
    total, _ = overall_today(db, now)
    if total >= cap:
        return ("All agents together reached today's limit of %s; it resets at midnight "
                "(AI Agents → Overview)." % money(cap))
    return None


def level_refusal(db: Session, key: str, action: str) -> str | None:
    """The level alone: a sentence when it is Never."""
    if level(db, key, action) == cat.NEVER:
        v = view(db, key)
        return "The registry holds %s's “%s” at Never (AI Agents → Overview)." % (
            v.name if v else key, cat.ACTIONS.get(action, {}).get("label", action))
    return None


def refusal(db: Session, key: str, action: str, now: datetime | None = None) -> str | None:
    """Why this entry may not take this action now, or None. Modules call this before acting."""
    return level_refusal(db, key, action) or cap_refusal(db, key, now or datetime.now(UTC))


# ------------------------------------------------------------------------------ alerts

def _recipients(db: Session, key: str) -> list[int]:
    ids = set(db.scalars(select(User.id).where(User.is_active.is_(True),
                                               User.role == Role.ADMIN)))
    r = settings_row(db, key)
    if r is not None and r.owner_user_id:
        u = db.get(User, r.owner_user_id)
        if u is not None and u.is_active:
            ids.add(u.id)
    return sorted(ids)


def alert_failure(db: Session, key: str, sentence: str, now: datetime | None = None, *,
                  what: str = "failure") -> bool:
    """Ring the bell for admins and the entry's owner — once per entry per New York day per
    kind. Returns whether it rang. Never raises: an alert must not break the thing failing."""
    now = now or datetime.now(UTC)
    try:
        already = db.scalar(select(func.count()).select_from(AgentRegistryLog).where(
            AgentRegistryLog.entry_key == key, AgentRegistryLog.what == what,
            AgentRegistryLog.at >= day_start(now)))
        if already:
            return False
        v = view(db, key)
        name = v.name if v else key
        db.add(AgentRegistryLog(at=now, entry_key=key, what=what, new_value=sentence[:1000]))
        title = ("%s stopped: %s" % (name, sentence))[:300]
        for uid in _recipients(db, key):
            db.add(AiAlert(user_id=uid, kind="agent_" + what, title=title, body=sentence,
                           urgent=True))
        db.flush()
        return True
    except Exception:
        log.exception("could not raise the registry alert for %s", key)
        return False


# ------------------------------------------------------------------------------ status

def _status(db: Session, v: View) -> dict:
    """{state: on | off | test | outside | unknown, sentence}."""
    if v.planned:
        return {"state": "off", "sentence": v.planned}
    try:
        aid = cat.ai_agent_id(v.key)
        if aid is not None:
            a = db.get(AiAgent, aid)
            if a.channel == AiAgent.VOICE:
                return ({"state": "on", "sentence": "Answering calls."} if a.answering_calls
                        else {"state": "off", "sentence": "Not answering calls."})
            return ({"state": "off", "sentence": "Off."} if a.mode == AiAgent.OFF
                    else {"state": "on", "sentence": "Mode: %s." % a.mode})
        from ..dispatch import config as dconf
        from ..zuper import config as zconf
        s = _dispatch_settings(db)
        if v.key == "autoreply":
            if not zconf.autoreply_enabled():
                return {"state": "off", "sentence": "Off on this server."}
            if not (s and s.autoreply_enabled):
                return {"state": "off", "sentence": "Switched off (Dispatch → Settings)."}
            if s.autoreply_test_only is not False:
                return {"state": "test", "sentence": "On for test customers only."}
            return {"state": "on", "sentence": "On for every customer."}
        if v.key in ("dispatch_assistant", "availability_reader"):
            if not (s and s.ai_enabled):
                return {"state": "off", "sentence": "Switched off (Dispatch → Settings)."}
            return {"state": "on", "sentence": "On."}
        if v.key == "dispatch_flags":
            return ({"state": "on", "sentence": "On."} if dconf.enabled()
                    else {"state": "off", "sentence": "Off on this server (DISPATCH_ENABLED)."})
        if v.key == "ahs_email_jobs":
            return ({"state": "on", "sentence": "On."} if zconf.ahs_email_creates_jobs()
                    else {"state": "off", "sentence": "Off on this server."})
        if v.key == "proposal_lines":
            return ({"state": "on", "sentence": "On."} if zconf.proposal_lines_enabled()
                    else {"state": "off", "sentence": "Off on this server."})
        if v.key == "crm_reminders":
            from ..reminders import config as rconf
            return ({"state": "on", "sentence": "The server gate is on; see Settings → "
                     "Automations."} if rconf.enabled()
                    else {"state": "off", "sentence": "Off (switched off 2026-10-12)."})
        if v.key == "kpi_report":
            return ({"state": "on", "sentence": "Runs nightly with the Zuper sync."}
                    if zconf.env_enabled() else {"state": "off", "sentence": "The Zuper sync "
                                                 "is off on this server."})
        if v.key == "call_followup":
            from ..followup import service as fsvc
            if not zconf.followup_enabled():
                return {"state": "off", "sentence": "Off on this server (CALL_FOLLOWUP_ENABLED)."}
            st = fsvc.settings(db)
            lv = {a: level(db, v.key, a) for a in ("send_text", "place_calls")}
            words = "texts %s, calls %s" % (cat.LEVEL_LABEL[lv["send_text"]].lower(),
                                            cat.LEVEL_LABEL[lv["place_calls"]].lower())
            if st.test_only:
                return {"state": "test", "sentence": "On for test customers only: %s." % words}
            return {"state": "on", "sentence": "On for every customer: %s." % words}
        if v.key == "inspection_docs":
            from ..inspection_docs import service as isvc
            if not zconf.inspection_docs_enabled():
                return {"state": "off", "sentence": "Off on this server (INSPECTION_DOCS_ENABLED)."}
            st = isvc.settings(db)
            words = "texts %s, calls %s" % (
                cat.LEVEL_LABEL[level(db, v.key, "text_staff")].lower(),
                cat.LEVEL_LABEL[level(db, v.key, "call_staff")].lower())
            if st.test_only:
                return {"state": "test", "sentence": "On for test jobs only (%s): %s." % (
                    ", ".join("#" + x for x in st.test_jobs), words)}
            return {"state": "on", "sentence": "On for every finished visit: %s." % words}
        if v.key == "inspection_report":
            from ..inspection_report import service as rsvc
            if not zconf.inspection_report_enabled():
                return {"state": "off",
                        "sentence": "Off on this server (INSPECTION_REPORT_ENABLED)."}
            st = rsvc.settings(db)
            if not st.vision_connection_id:
                return {"state": "off", "sentence": "No vision connection chosen in its settings."}
            if st.test_only:
                return {"state": "test", "sentence": "On for test jobs only (%s)." % ", ".join(
                    "#" + x for x in st.test_jobs)}
            return {"state": "on", "sentence": "On for every inspection."}
        if v.key == "tech_briefing":
            from ..tech_briefing import service as tsvc
            if not zconf.tech_briefing_enabled():
                return {"state": "off",
                        "sentence": "Off on this server (TECH_BRIEFING_ENABLED)."}
            st = tsvc.settings(db)
            words = "texts %s, at %s" % (cat.LEVEL_LABEL[level(db, v.key, "text_staff")].lower(),
                                         st.send_time)
            if st.test_only:
                return {"state": "test", "sentence": "On for test technicians only (%s): %s." % (
                    ", ".join(st.test_technicians), words)}
            return {"state": "on", "sentence": "On for every technician: %s." % words}
        if v.key == "payments":
            if not zconf.payments_enabled():
                return {"state": "off", "sentence": "The page and chat work; the background "
                        "pass is off on this server (PAYMENTS_ENABLED)."}
            return {"state": "on", "sentence": "On; customer reminders %s." % cat.LEVEL_LABEL[
                level(db, v.key, "send_customer")].lower()}
        if v.key == "insights":
            if not zconf.insights_enabled():
                return {"state": "off", "sentence": "The Insights tab works; reading calls and "
                        "texts is off on this server (INSIGHTS_ENABLED)."}
            if level(db, v.key, "ai_read") == cat.NEVER:
                return {"state": "off", "sentence": "Reading calls and texts is at Never."}
            return {"state": "on", "sentence": "On: reads every new call and text each hour."}
        if v.key == "ahs_authorizations":
            return {"state": "outside", "sentence": "Switched at owen-main."}
        if v.outside:
            return {"state": "outside", "sentence": cat.OUTSIDE}
    except Exception:
        log.exception("status of %s", v.key)
    return {"state": "unknown", "sentence": "Its state could not be read."}


def _health(db: Session, v: View, now: datetime) -> dict:
    """The last error the entry recorded, and whether it is from today."""
    start = day_start(now)
    err, at = None, None
    # Only real failures: a heartbeat sentence such as "switched off" is the status, not an error.
    flagged = db.scalars(select(AgentRegistryLog).where(
        AgentRegistryLog.entry_key == v.key, AgentRegistryLog.what == "failure")
        .order_by(AgentRegistryLog.id.desc()).limit(1)).first()
    if flagged is not None:
        err, at = flagged.new_value, flagged.at
    if v.key == "autoreply":
        r = db.scalars(select(AutoReply).where(AutoReply.state == "failed")
                       .order_by(AutoReply.id.desc()).limit(1)).first()
        if r is not None and (at is None or (r.created_at and _aw(r.created_at) > _aw(at))):
            err, at = r.error, r.created_at
    elif v.key in ("dispatch_assistant", *DISPATCH_AI_KINDS.values()):
        cond = _dispatch_kind_filter(v.key)
        r = db.scalars(select(DispatchAiRun).where(DispatchAiRun.ok.is_(False), cond)
                       .order_by(DispatchAiRun.id.desc()).limit(1)).first()
        if r is not None and (at is None or _aw(r.created_at) > _aw(at)):
            err, at = r.error, r.created_at
    elif cat.ai_agent_id(v.key) is not None:
        r = db.scalars(select(AiRun).where(AiRun.agent_id == cat.ai_agent_id(v.key),
                                           AiRun.outcome == "error")
                       .order_by(AiRun.id.desc()).limit(1)).first()
        if r is not None and (at is None or _aw(r.created_at) > _aw(at)):
            err, at = r.reason, r.created_at
    from ..dispatch.config import aware
    at = aware(at)
    return {"last_error": err, "last_error_at": at.isoformat() if at else None,
            "error_today": bool(err and at and at >= start)}


def _aw(dt: datetime | None) -> datetime | None:
    from ..dispatch.config import aware
    return aware(dt)


def _owner_name(db: Session, uid: int | None) -> str | None:
    if not uid:
        return None
    u = db.get(User, uid)
    return (u.name or u.email) if u is not None else None


def entry_out(db: Session, v: View, now: datetime) -> dict:
    r = settings_row(db, v.key)
    spent = spend_today(db, v.key, now)
    return {
        "key": v.key, "name": v.name, "kind": v.kind,
        "role": r.role if r is not None and r.role is not None else v.role,
        "purpose": v.purpose, "covers": v.covers, "channels": list(v.channels),
        "runs_on": v.runs_on, "provider": v.provider, "hours": v.hours, "never": list(v.never),
        "is_test": bool(r and r.is_test), "notes": r.notes if r else None,
        "owner_user_id": r.owner_user_id if r else None,
        "owner": _owner_name(db, r.owner_user_id if r else None) or v.owner_hint,
        "archived": v.archived,
        "actions": [{
            "action": a.action, "label": cat.ACTIONS[a.action]["label"],
            "customer": cat.ACTIONS[a.action]["customer"],
            "level": level(db, v.key, a.action), "default": a.default,
            "choices": list(a.choices), "note": a.note} for a in v.actions],
        "status": _status(db, v), "health": _health(db, v, now),
        "settings": _settings_out(db, v.key),
        "spend_today": money(spent), "spend_unknown": spent is None,
        "daily_cap": money(r.daily_cap_micros) if r and r.daily_cap_micros is not None
        else None,
    }


# Entries with settings a person may edit, and how each is read and checked.
def _settings_kinds():
    from ..followup import config as fconf
    from ..insights import config as nconf
    from ..inspection_docs import config as iconf
    from ..inspection_report import config as rconf
    from ..payments import config as pconf
    from ..tech_briefing import config as tconf
    return {"payments": (lambda raw: pconf.Settings.from_config(raw).as_dict(), pconf.validate),
            "insights": (lambda raw: nconf.Settings.from_config(raw).as_dict(), nconf.validate),
            "tech_briefing": (lambda raw: tconf.Settings.from_config(raw).as_dict(),
                              tconf.validate),
            "inspection_report": (lambda raw: rconf.Settings.from_config(raw).as_dict(),
                                  rconf.validate),
            "call_followup": (lambda raw: fconf.Settings.from_config(raw).as_dict(),
                              fconf.validate),
            "inspection_docs": (lambda raw: iconf.Settings.from_config(raw).as_dict(),
                                iconf.validate)}


def _settings_out(db: Session, key: str) -> dict | None:
    kinds = _settings_kinds()
    if key not in kinds:
        return None
    r = settings_row(db, key)
    return kinds[key][0](r.config if r is not None else None)


def overview(db: Session, now: datetime) -> dict:
    total, unknown = overall_today(db, now)
    paused = db.get(AiSettings, 1)
    return {
        "roles": [{"role": k, "name": n} for k, n in cat.ROLES.items()],
        "levels": [{"value": k, "label": cat.LEVEL_LABEL[k]} for k in cat.LEVELS],
        "entries": [entry_out(db, v, now) for v in views(db)],
        "overall": {"spend_today": money(total), "spend_unknown": unknown,
                    "daily_cap": money(overall_cap(db)),
                    "paused": bool(paused and paused.paused)},
        "promote_confirm": PROMOTE_CONFIRM,
    }


def _cap_micros(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise Invalid("A limit is a dollar amount, like 5 or 12.50.") from None
    if v < 0 or v > 10000:
        raise Invalid("A daily limit is between $0 and $10,000.")
    return round(v * MICRO)


def update_entry(db: Session, key: str, changes: dict, *, user_id: int | None) -> list:
    """role, owner_user_id, is_test, daily_cap_usd, notes — each change logged."""
    v = view(db, key)
    if v is None:
        raise Invalid("There is no registry entry %s." % key)
    r = _row(db, key)
    now = datetime.now(UTC)
    logs = []

    def note(what, old, new):
        logs.append(AgentRegistryLog(at=now, entry_key=key, user_id=user_id, what=what,
                                     old_value=None if old is None else str(old),
                                     new_value=None if new is None else str(new)))

    if "role" in changes:
        new = changes["role"]
        if new is not None and new not in cat.ROLES:
            raise Invalid("A role is one of the ten agents (1-10).")
        old = r.role if r.role is not None else v.role
        if new != old:
            r.role = new
            note("role", old, new)
    if "owner_user_id" in changes:
        new = changes["owner_user_id"]
        if new is not None:
            u = db.get(User, new)
            if u is None or not u.is_active:
                raise Invalid("That person is not an active user.")
        if new != r.owner_user_id:
            note("owner", r.owner_user_id, new)
            r.owner_user_id = new
    if "is_test" in changes:
        new = bool(changes["is_test"])
        if new != bool(r.is_test):
            note("test", bool(r.is_test), new)
            r.is_test = new
    if "daily_cap_usd" in changes:
        new = _cap_micros(changes["daily_cap_usd"])
        if new != r.daily_cap_micros:
            note("cap", money(r.daily_cap_micros), money(new))
            r.daily_cap_micros = new
    if "config" in changes:
        kinds = _settings_kinds()
        if key not in kinds:
            raise Invalid("%s has no settings." % v.name)
        new = changes["config"]
        if not isinstance(new, dict):
            raise Invalid("Settings are a set of named values.")
        problems = kinds[key][1](new)
        if problems:
            raise Invalid("Not saved: %s." % "; ".join(problems))
        old = kinds[key][0](r.config)
        merged = kinds[key][0]({**(r.config or {}), **new})
        if merged != old:
            r.config = merged
            note("settings", ", ".join("%s=%s" % (k, old[k]) for k in sorted(new) if k in old),
                 ", ".join("%s=%s" % (k, merged[k]) for k in sorted(new) if k in merged))
    if "notes" in changes:
        new = (changes["notes"] or "").strip() or None
        if new != r.notes:
            note("notes", r.notes, new)
            r.notes = new
    if logs:
        r.updated_at, r.updated_by_id = now, user_id
        db.add_all(logs)
    db.flush()
    return logs


def set_overall_cap(db: Session, usd, *, user_id: int | None) -> None:
    new = _cap_micros(usd)
    if new is None:
        raise Invalid("Give the overall daily limit in dollars.")
    s = db.get(AiSettings, 1)
    if s is None:
        s = AiSettings(id=1, paused=False)
        db.add(s)
    old = overall_cap(db)
    if new != old:
        s.overall_daily_cap_micros = new
        db.add(AgentRegistryLog(entry_key="*", user_id=user_id, what="overall_cap",
                                old_value=money(old), new_value=money(new)))
    db.flush()


# ------------------------------------------------------------------------------ activity

def activity(db: Session, *, key: str | None = None, limit: int = 100) -> list[dict]:
    """What the entries did, newest first, read from each one's own log. Nothing is copied."""
    limit = max(1, min(limit, 500))
    names = {v.key: v.name for v in views(db, include_archived=True)}
    out: list[dict] = []

    def add(k, at, what, text, *, tone="info", ref=None):
        from ..dispatch.config import aware
        out.append({"key": k, "name": names.get(k, k), "at": aware(at).isoformat() if at else None,
                    "what": what, "text": text, "tone": tone, "ref": ref})

    if key in (None, "autoreply"):
        for r in db.scalars(select(AutoReply).order_by(AutoReply.id.desc()).limit(limit)):
            who = r.customer_name or "a customer"
            if r.state in ("sent", "sending"):
                add("autoreply", r.sent_at or r.created_at, "text",
                    "Texted %s: “%s”" % (who, r.reply or ""), tone="good")
            elif r.state == "shadow":
                add("autoreply", r.created_at, "shadow",
                    "Would have texted %s (shadow): “%s”" % (who, r.reply or ""))
            elif r.state == "failed":
                add("autoreply", r.created_at, "failed",
                    "Could not text %s: %s" % (who, r.error or "unknown error"), tone="bad")
            else:
                add("autoreply", r.created_at, r.state,
                    "Did not text %s: %s" % (who, r.reason or r.state), tone="muted")
    if key is None or cat.ai_agent_id(key) is not None:
        q = select(AiRun).where(AiRun.is_test.is_(False))
        if key is not None:
            q = q.where(AiRun.agent_id == cat.ai_agent_id(key))
        for r in db.scalars(q.order_by(AiRun.id.desc()).limit(limit)):
            add(cat.ai_agent_key(r.agent_id), r.created_at, r.outcome,
                "%s%s%s" % (r.trigger.replace("_", " "),
                            " — %s" % r.subject_label if r.subject_label else "",
                            ": %s" % r.reason if r.reason else ""),
                tone="bad" if r.outcome == "error" else "info", ref=r.id)
    if key in (None, "call_followup"):
        for r in db.scalars(select(CallFollowup).order_by(CallFollowup.id.desc()).limit(limit)):
            who = r.customer_name or "%s-%s-%s" % (r.phone[:3], r.phone[3:6], r.phone[6:])
            if r.call_state in ("requested", "shadow", "failed"):
                verb = {"requested": "Anisa called %s",
                        "shadow": "Would have had Anisa call %s (shadow)",
                        "failed": "Could not have Anisa call %s"}[r.call_state]
                add("call_followup", r.call_at, "call", (verb % who) + (
                    ": %s" % r.error if r.call_state == "failed" and r.error else ""),
                    tone={"failed": "bad", "requested": "good"}.get(r.call_state, "info"))
            if r.text_state in ("sent", "shadow", "failed", "sending"):
                verb = {"sent": "Texted %s", "sending": "Texted %s",
                        "shadow": "Would have texted %s (shadow)",
                        "failed": "Could not text %s"}[r.text_state]
                add("call_followup", r.text_at, "text", (verb % who) + (
                    ": \u201c%s\u201d" % r.text_body if r.text_body else ""),
                    tone={"failed": "bad", "sent": "good"}.get(r.text_state, "info"))
            add("call_followup", r.created_at, "started", "%s did not answer our call (%s%s). "
                "Now: %s" % (who, r.trigger_source or "?", ", %s" % r.staff if r.staff else "",
                             r.reason or r.state), tone="muted")
    if key in (None, "inspection_report"):
        from ..models import InspectionReport
        for r in db.scalars(select(InspectionReport).order_by(InspectionReport.id.desc())
                            .limit(limit)):
            label = "#%s %s" % (r.job_number or "?", r.customer_name or "")
            text = {"draft": "Draft ready: %s, %d pictures" % (label, len(r.photos or [])),
                    "approved": "Approved: %s (nothing sent)" % label,
                    "discarded": "Discarded: %s" % label,
                    "failed": "Failed: %s - %s" % (label, r.error or "?"),
                    }.get(r.state, "%s: %s" % (r.state.title(), label))
            add("inspection_report", r.updated_at or r.created_at, r.state, text,
                tone={"failed": "bad", "draft": "good"}.get(r.state, "info"))
    if key in (None, "tech_briefing"):
        from ..models import TechBriefing
        for r in db.scalars(select(TechBriefing).order_by(TechBriefing.id.desc()).limit(limit)):
            n = len(r.job_numbers or [])
            verb = {"sent": "Texted", "shadow": "Would have texted", "failed": "Could not text",
                    "skipped": "Did not text"}.get(r.state, r.state.title())
            add("tech_briefing", r.sent_at or r.created_at, r.state,
                "%s %s %d job%s for %s%s:\n%s" % (
                    verb, r.technician, n, "" if n == 1 else "s", r.brief_date.isoformat(),
                    " (%s)" % r.note if r.note and r.state != "sent" else "", r.body or ""),
                tone={"failed": "bad", "sent": "good", "skipped": "muted"}.get(r.state, "info"))
    if key in (None, "payments"):
        from ..models import PaymentChange
        for r in db.scalars(select(PaymentChange).order_by(PaymentChange.id.desc())
                            .limit(limit)):
            add("payments", r.created_at, "change", "%s%s (via %s)" % (
                r.summary, " - undone" if r.undone_at else "", r.via), tone="info")
    if key in (None, "insights"):
        from ..insights import config as nconf
        from ..models import ConversationSignal
        for r in db.scalars(select(ConversationSignal).order_by(ConversationSignal.id.desc())
                            .limit(limit)):
            add("insights", r.created_at or r.at, r.tag, "%s%s: “%s”" % (
                nconf.TAGS.get(r.tag, r.tag), " (job #%s)" % r.job_number if r.job_number
                else "", r.quote), tone="bad" if r.tag in nconf.ALERT_TAGS else "info")
    if key in (None, "inspection_docs"):
        for r in db.scalars(select(InspectionCheck).order_by(InspectionCheck.id.desc())
                            .limit(limit)):
            label = "#%s %s" % (r.job_number or "?", r.customer_name or "")
            if r.state == "documented":
                add("inspection_docs", r.checked_at or r.created_at, "documented",
                    "%s: %s fully documented" % (label, r.kind), tone="good")
            elif r.state in ("flagged", "resolved"):
                from ..inspection_docs import rules as irules
                add("inspection_docs", r.checked_at or r.created_at, r.state,
                    "%s: %s missing %s%s" % (label, r.kind, irules.words(r.missing or []),
                                             " (now resolved)" if r.state == "resolved" else ""),
                    tone="bad" if r.state == "flagged" else "good")
            if r.text_state in ("sent", "shadow", "failed"):
                add("inspection_docs", r.text_at, "text", "%s %s: \u201c%s\u201d" % (
                    {"sent": "Texted", "shadow": "Would have texted",
                     "failed": "Could not text"}[r.text_state],
                    r.technician or "the technician", r.text_body or ""),
                    tone={"failed": "bad", "sent": "good"}.get(r.text_state, "info"))
            if r.call_state in ("requested", "shadow", "failed"):
                add("inspection_docs", r.call_at, "call", "%s %s about %s" % (
                    {"requested": "Called", "shadow": "Would have called",
                     "failed": "Could not call"}[r.call_state],
                    r.technician or "the technician", label),
                    tone={"failed": "bad", "requested": "good"}.get(r.call_state, "info"))
    if key in (None, "dispatch_assistant"):
        for r in db.scalars(select(DispatchWriteLog).order_by(DispatchWriteLog.id.desc())
                            .limit(limit)):
            add("dispatch_assistant", r.at, "zuper_" + (r.action or ""),
                r.sentence or "%s %s → %s" % (r.target or r.action, r.old_value, r.new_value),
                tone="bad" if r.result == "failed" else "info")
    if key in (None, "dispatch_assistant", *DISPATCH_AI_KINDS.values()):
        for r in db.scalars(select(DispatchAiRun).where(DispatchAiRun.ok.is_(False))
                            .order_by(DispatchAiRun.id.desc()).limit(limit)):
            k = DISPATCH_AI_KINDS.get(r.kind, "dispatch_assistant")
            if key in (None, k):
                add(k, r.created_at, "failed", "The AI request failed: %s" % (r.error or "?"),
                    tone="bad")
    q = select(AgentRegistryLog).order_by(AgentRegistryLog.id.desc()).limit(limit)
    if key is not None:
        q = q.where(or_(AgentRegistryLog.entry_key == key, AgentRegistryLog.entry_key == "*"))
    for r in db.scalars(q):
        add(r.entry_key, r.at, r.what, _log_text(r, db),
            tone="bad" if r.what in ("failure", "cap_reached") else "muted")
    out.sort(key=lambda x: x["at"] or "", reverse=True)
    return out[:limit]


def _log_text(r: AgentRegistryLog, db: Session) -> str:
    who = _owner_name(db, r.user_id) or "The system"
    if r.what == "level":
        label = cat.ACTIONS.get(r.target or "", {}).get("label", r.target)
        return "%s set “%s” from %s to %s%s" % (
            who, label, cat.LEVEL_LABEL.get(r.old_value or "", r.old_value),
            cat.LEVEL_LABEL.get(r.new_value or "", r.new_value),
            ". Reason: %s" % r.reason if r.reason else "")
    if r.what in ("failure", "cap_reached"):
        return "Alert sent: %s" % (r.new_value or "")
    if r.what == "overall_cap":
        return "%s set the overall daily limit from %s to %s" % (who, r.old_value, r.new_value)
    if r.what == "role":
        return "%s moved it from role %s to %s" % (who, r.old_value, r.new_value)
    if r.what == "owner":
        return "%s changed who answers for it" % who
    if r.what == "test":
        return "%s marked it %s" % (who, "a test agent" if r.new_value == "True"
                                    else "not a test agent")
    if r.what == "settings":
        return "%s changed its settings: %s" % (who, r.new_value)
    if r.what == "cap":
        return "%s set its daily limit from %s to %s" % (who, r.old_value or "none",
                                                          r.new_value or "none")
    return "%s changed its %s" % (who, r.what)
