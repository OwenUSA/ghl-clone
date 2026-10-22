"""Reviews & QC's Zuper requests. Reads through the auto-reply's readers (the Connect inbox and
one conversation's history); its two writes - the SMS send and the private job note, in the
auto-reply's exact shape - only inside `client.reviews_write()`, under REVIEWS_TEXTS_ENABLED."""
from __future__ import annotations

from typing import Any

from ..autoreply import zconnect as az
from ..autoreply.rules import ZMsg
from ..zuper import client


def conversations_by_phone() -> dict[str, str]:
    """Last ten digits -> conversation uid, from the Connect inbox (newest activity first)."""
    out: dict[str, str] = {}
    for conv in az.recent_conversations():
        cust = az.customer_of(conv)
        if cust["phone"] and conv.get("conversation_uid") and cust["phone"] not in out:
            out[cust["phone"]] = conv["conversation_uid"]
    return out


def messages(conversation_uid: str) -> list[ZMsg]:
    return az.messages(conversation_uid)


def sender_check() -> str | None:
    return az.sender_check()


def send(customer_uid: str, phone10: str, body: str) -> Any:
    with client.reviews_write():
        return client.request("POST", az.SEND_PATH % customer_uid, body={
            "type": "SMS", "sms": {"body": body, "to": "+1" + phone10}})


def add_note(job_uid: str, text: str) -> Any:
    with client.reviews_write():
        return client.request("POST", client.path("job_notes", uid=job_uid), body={"note": {
            "note": text, "is_private": True, "notify_users": False}})
