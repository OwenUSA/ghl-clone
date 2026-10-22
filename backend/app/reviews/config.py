"""Reviews & QC settings: fixed rules here, the editable ones in the registry entry's `config`
(AI Agents -> Overview -> Reviews & QC). Decided with the user on 2026-10-22."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..autoreply.config import TEST_CUSTOMERS
from ..followup.config import call_hours, ten

KEY = "reviews"
POLL_SECONDS = 300
WINDOW_DAYS = 14                  # a job finished longer ago than this is never asked
DONE_STATUS = "Repair Complete"
MAX_SCORE = 5
OPEN_STATES = ("asked", "improve")
# How long after the first text a customer's reply still counts, and the whole check's life.
GIVE_UP_DAYS = 5
LIMITS = {"wait_days": (0, 14), "no_reply_days": (1, 7), "resurvey_days": (14, 365)}


@dataclass
class Settings:
    review_link: str = ""                     # empty = no link is ever sent
    test_only: bool = True
    test_phones: list[str] = field(default_factory=list)
    wait_days: int = 2
    no_reply_days: int = 2
    resurvey_days: int = 90
    call_agent: str = "DTR Retell - Brian"    # owen-main's name for Anisa

    @classmethod
    def from_config(cls, raw: dict | None) -> Settings:
        s = cls()
        raw = raw if isinstance(raw, dict) else {}
        if isinstance(raw.get("test_only"), bool):
            s.test_only = raw["test_only"]
        for k, (lo, hi) in LIMITS.items():
            v = raw.get(k)
            if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi:
                setattr(s, k, v)
        link = raw.get("review_link")
        if isinstance(link, str) and link.strip().lower().startswith("https://"):
            s.review_link = link.strip()
        if isinstance(raw.get("call_agent"), str) and raw["call_agent"].strip():
            s.call_agent = raw["call_agent"].strip()
        if isinstance(raw.get("test_phones"), list):
            s.test_phones = [p for p in (ten(x) for x in raw["test_phones"]) if p]
        return s

    def as_dict(self) -> dict:
        return {"review_link": self.review_link, "test_only": self.test_only,
                "test_phones": list(self.test_phones), "wait_days": self.wait_days,
                "no_reply_days": self.no_reply_days, "resurvey_days": self.resurvey_days,
                "call_agent": self.call_agent}


def validate(raw: dict) -> list[str]:
    out = []
    unknown = set(raw) - set(Settings().as_dict())
    if unknown:
        out.append("unknown settings: %s" % ", ".join(sorted(unknown)))
    for k, (lo, hi) in LIMITS.items():
        if k in raw and not (isinstance(raw[k], int) and not isinstance(raw[k], bool)
                             and lo <= raw[k] <= hi):
            out.append("%s must be between %d and %d" % (k.replace("_", " "), lo, hi))
    link = raw.get("review_link")
    if link not in (None, "") and not (isinstance(link, str)
                                       and link.strip().lower().startswith("https://")
                                       and len(link) <= 300 and " " not in link.strip()):
        out.append("the review link is one https:// address")
    if "test_only" in raw and not isinstance(raw["test_only"], bool):
        out.append("test only is on or off")
    if "test_phones" in raw and not isinstance(raw["test_phones"], list):
        out.append("test phones is a list of numbers")
    return out


__all__ = ["KEY", "TEST_CUSTOMERS", "Settings", "call_hours", "ten", "validate"]
