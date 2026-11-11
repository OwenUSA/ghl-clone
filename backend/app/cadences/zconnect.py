"""Every Zuper request Follow-ups makes. Reads: the Connect text conversations (the auto-reply's
verified reads, `client.connect_read`). Writes: the SAME SMS send and private job note as the
auto-reply, in the same exact shape, inside `client.followups_write()` and nothing else - its
own server gate FOLLOWUPS_ENABLED (owner's approval for this module only, 2026-11-11).

Connect CALL rows are not read here: the Dispatch reader already copies every Connect call into
`dispatch_calls`, and reading them twice would count one call twice."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..autoreply import zconnect as az
from ..dispatch.comms import digits
from ..zuper import client
from .rules import Ev


def inbox(pages: int = 2) -> dict[str, dict]:
    """Phone (ten digits) -> {"uid", "recent_at", "blocked", "customer_uid", "name"} for the
    newest text conversations on the 954 line."""
    out: dict[str, dict] = {}
    for conv in az.recent_conversations(pages):
        if az.our_line(conv) and az.our_line(conv) != az.c.SENDER[-10:]:
            continue
        cust = az.customer_of(conv)
        if cust["phone"] and cust["phone"] not in out and conv.get("conversation_uid"):
            out[cust["phone"]] = {"uid": conv["conversation_uid"],
                                  "recent_at": az._time(conv.get("recent_conversation_at")),
                                  "blocked": cust["blocked"],
                                  "customer_uid": cust["customer_uid"], "name": cust["name"]}
    return out


def find(phone: str) -> dict | None:
    """The conversation with this number, asked fresh (right before a send)."""
    payload = client.connect_read("POST", "/message/conversations", body={
        "limit": 10, "page": 1, "filters": {"status": "", "keyword": phone}})
    rows = client.data_of(payload)
    for conv in rows if isinstance(rows, list) else []:
        if not isinstance(conv, dict):
            continue
        cust = az.customer_of(conv)
        if cust["phone"] == phone and conv.get("conversation_uid") and not (
                az.our_line(conv) and az.our_line(conv) != az.c.SENDER[-10:]):
            return {"uid": conv["conversation_uid"],
                    "recent_at": az._time(conv.get("recent_conversation_at")),
                    "blocked": cust["blocked"], "customer_uid": cust["customer_uid"],
                    "name": cust["name"]}
    return None


def texts(conversation_uid: str, since: datetime) -> list[Ev]:
    """The conversation's texts since `since`, oldest first."""
    out = []
    for m in az.messages(conversation_uid):
        if m.kind != "text" or m.at < since:
            continue
        out.append(Ev(uid="zt:" + m.uid, at=m.at, kind="text", out=m.out, source="Zuper",
                      staff=m.user, body=m.body or ""))
    return out


def sender_check() -> str | None:
    return az.sender_check()


def send(customer_uid: str, phone10: str, body: str) -> Any:
    with client.followups_write():
        return client.request("POST", az.SEND_PATH % customer_uid, body={
            "type": "SMS", "sms": {"body": body, "to": "+1" + phone10}})


def add_note(job_uid: str, text: str) -> Any:
    with client.followups_write():
        return client.request("POST", client.path("job_notes", uid=job_uid), body={"note": {
            "note": text, "is_private": True, "notify_users": False}})


__all__ = ["digits"]
