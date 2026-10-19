"""Every Zuper request the auto-reply makes, and the translation of Zuper's rows into plain
objects. Reads through `client.connect_read` (the Connect host's short read list) and
`client.request`; its two writes only inside `client.autoreply_write()`.

Shapes verified read-only against the live account on 2026-10-19:
  POST /telephony/message/conversations {"limit","page","filters":{"status": ""}}
      -> {data: [{conversation_uid, is_unresponded, recent_conversation_at, recent_message,
                  customer_number: {customer_uid, customer_full_name, number, is_blocked},
                  number: {number}}], total_pages}
  POST /telephony/messages {"filters": {"conversation_uid", "message_type": [...]}, "limit",
                            "page"}
      -> {data: [{message_uid, message_type SMS|MMS|CALL|SYSTEM, direction INCOMING|OUTGOING,
                  message, status, created_at, module, module_uid, user: {first_name},
                  call: {call_uid, status, duration, call_recordings: [{recording_url,
                         call_summary: {summary, sentiment, next_action}}]}}]}   newest first
  GET  /company/config -> {data: {zuper_connect: {system_notification_number, _enabled}}}
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..dispatch.comms import digits
from ..zuper import client
from . import config as c
from .rules import ZMsg

MESSAGE_TYPES = ["SMS", "MMS", "CALL", "SYSTEM"]


def _time(value) -> datetime | None:
    if not value:
        return None
    try:
        return c.aware(datetime.fromisoformat(str(value)))
    except ValueError:
        return None


def recent_conversations(pages: int = c.CONVERSATION_PAGES) -> list[dict]:
    """The inbox, newest activity first."""
    out: list[dict] = []
    for page in range(1, pages + 1):
        payload = client.connect_read("POST", "/message/conversations", body={
            "limit": 25, "page": page, "filters": {"status": ""}})
        rows = client.data_of(payload)
        if not isinstance(rows, list) or not rows:
            break
        out += [r for r in rows if isinstance(r, dict)]
        total = payload.get("total_pages") if isinstance(payload, dict) else None
        if isinstance(total, int) and page >= total:
            break
    return out


def history(conversation_uid: str, limit: int = c.HISTORY_LIMIT) -> list[dict]:
    payload = client.connect_read("POST", "/messages", body={
        "filters": {"conversation_uid": conversation_uid, "message_type": MESSAGE_TYPES},
        "limit": limit, "page": 1})
    rows = client.data_of(payload)
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def to_msg(row: dict) -> ZMsg | None:
    at = _time(row.get("created_at"))
    uid = row.get("message_uid")
    if at is None or not uid:
        return None
    mtype = str(row.get("message_type") or "").upper()
    if mtype not in ("SMS", "MMS", "CALL"):
        return None                                 # SYSTEM rows are not the customer or us
    user = row.get("user") if isinstance(row.get("user"), dict) else {}
    m = ZMsg(uid=str(uid), at=at, kind="call" if mtype == "CALL" else "text",
             out=str(row.get("direction") or "").upper() == "OUTGOING",
             body=str(row.get("message") or ""), user=user.get("first_name"),
             status=row.get("status"))
    if mtype == "MMS" and not m.body:
        m.body = "[a picture]"
    call = row.get("call") if isinstance(row.get("call"), dict) else None
    if call:
        m.call_uid = call.get("call_uid")
        m.call_status = str(call.get("status") or "").upper() or None
        m.seconds = int(call.get("duration") or 0)
        if call.get("direction"):
            m.out = str(call["direction"]).upper() == "OUTGOING"
        for r in call.get("call_recordings") or []:
            if not isinstance(r, dict):
                continue
            m.recording_url = m.recording_url or r.get("recording_url")
            cs = r.get("call_summary") if isinstance(r.get("call_summary"), dict) else {}
            if cs.get("summary") and not m.summary:
                m.summary = str(cs["summary"])
                if cs.get("next_action"):
                    m.summary += "\nNext step: %s" % cs["next_action"]
            m.sentiment = m.sentiment or cs.get("sentiment")
    return m


def messages(conversation_uid: str) -> list[ZMsg]:
    """One conversation, oldest first."""
    out = [m for m in (to_msg(r) for r in history(conversation_uid)) if m]
    return sorted(out, key=lambda m: m.at)


def customer_of(conv: dict) -> dict:
    cn = conv.get("customer_number") if isinstance(conv.get("customer_number"), dict) else {}
    return {"customer_uid": cn.get("customer_uid") or None,
            "name": cn.get("customer_full_name") or None,
            "phone": digits(cn.get("number")),
            "blocked": bool(cn.get("is_blocked"))}


def our_line(conv: dict) -> str:
    n = conv.get("number") if isinstance(conv.get("number"), dict) else {}
    return digits(n.get("number"))


def sender_check() -> str | None:
    """Why a send must not happen now, or None. Zuper sends from the company's System
    Notification Number — the 954 line the customer texted, unless someone changed it."""
    cfg = client.data_of(client.request("GET", client.path("company_config")))
    zc = cfg.get("zuper_connect") if isinstance(cfg, dict) else None
    if not isinstance(zc, dict):
        return "Zuper did not say which number its texts go out from"
    number = digits(zc.get("system_notification_number"))
    if not zc.get("system_notification_enabled"):
        return "Zuper's system texts are switched off (Settings → Communication)"
    if number != digits(c.SENDER):
        return "Zuper's texts would go out from %s, not %s" % (
            number or "an unknown number", c.SENDER_DISPLAY)
    return None


def send(customer_uid: str, phone10: str, body: str) -> Any:
    with client.autoreply_write():
        return client.request("POST", client.path("customer_send", uid=customer_uid), body={
            "type": "SMS", "sms": {"body": body, "to": "+1" + phone10}})


def add_note(job_uid: str, text: str) -> Any:
    with client.autoreply_write():
        return client.request("POST", client.path("job_notes", uid=job_uid), body={"note": {
            "note": text, "is_private": True, "notify_users": False}})
