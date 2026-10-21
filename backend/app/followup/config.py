"""The call follow-up's settings: fixed rules here, the editable ones in the registry entry's
`config` (AI Agents -> Overview -> Call follow-up). Decided with the user on 2026-10-21."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time

from ..autoreply.config import TEST_CUSTOMERS, holidays
from ..dispatch.config import aware, local

KEY = "call_followup"                     # its registry entry
POLL_SECONDS = 60
LOOKBACK_HOURS = 6                        # a call older than this never starts a follow-up
CALLS_PER_DAY = 2                         # AI calls to one number, New York day (Q15)
CALL_DAYS = (0, 1, 2, 3, 4, 5)            # Monday-Saturday
CALL_OPEN, CALL_CLOSE = time(8, 0), time(20, 0)

DEFAULT_TEXT = ("Hi {first_name}, this is Dream Team Roofing. We just tried to call you about "
                "your roof. Reply here or call us back at (954) 420-7373.")
DEFAULT_TEXT_NO_NAME = ("Hi, this is Dream Team Roofing. We just tried to call you about your "
                        "roof. Reply here or call us back at (954) 420-7373.")

LIMITS = {"text_delay_minutes": (0, 60), "wait_minutes": (1, 240)}


def ten(value) -> str:
    d = "".join(ch for ch in str(value or "") if ch.isdigit())
    return d[-10:] if len(d) >= 10 else ""


@dataclass
class Settings:
    test_only: bool = True
    # Numbers (last ten digits) counted as test customers beside the Zuper TEST customers'.
    test_phones: list[str] = field(default_factory=lambda: ["5618788090"])
    text_delay_minutes: int = 2
    wait_minutes: int = 10
    text_template: str = DEFAULT_TEXT
    caller_id: str = "+19547758492"           # the CRM line; must be a number owen-main owns
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
        for k in ("text_template", "caller_id", "call_agent"):
            v = raw.get(k)
            if isinstance(v, str) and v.strip():
                setattr(s, k, v.strip())
        if isinstance(raw.get("test_phones"), list):
            s.test_phones = [p for p in (ten(x) for x in raw["test_phones"]) if p]
        return s

    def as_dict(self) -> dict:
        return {"test_only": self.test_only, "test_phones": list(self.test_phones),
                "text_delay_minutes": self.text_delay_minutes,
                "wait_minutes": self.wait_minutes, "text_template": self.text_template,
                "caller_id": self.caller_id, "call_agent": self.call_agent}


def validate(raw: dict) -> list[str]:
    """Problems with settings a person typed; empty = fine."""
    out = []
    unknown = set(raw) - set(Settings().as_dict())
    if unknown:
        out.append("unknown settings: %s" % ", ".join(sorted(unknown)))
    for k, (lo, hi) in LIMITS.items():
        if k in raw and not (isinstance(raw[k], int) and not isinstance(raw[k], bool)
                             and lo <= raw[k] <= hi):
            out.append("%s must be a whole number of minutes between %d and %d" % (
                k.replace("_", " "), lo, hi))
    t = raw.get("text_template")
    if t is not None:
        if not isinstance(t, str) or not t.strip():
            out.append("the text cannot be empty")
        elif len(t) > 320:
            out.append("the text must be 320 characters or fewer")
        elif any(w in t.lower() for w in ("$", "approved", "http")):
            out.append("the text may not mention money, approval or a link")
    cid = raw.get("caller_id")
    if cid is not None and not (isinstance(cid, str) and ten(cid)):
        out.append("the caller ID must be a full phone number")
    if "test_only" in raw and not isinstance(raw["test_only"], bool):
        out.append("test only is on or off")
    if "test_phones" in raw and (not isinstance(raw["test_phones"], list)
                                 or any(not ten(x) for x in raw["test_phones"])):
        out.append("test numbers must be full phone numbers")
    return out


def call_hours(when: datetime) -> bool:
    t = local(aware(when))
    return (t.weekday() in CALL_DAYS and t.date() not in holidays(t.year)
            and CALL_OPEN <= t.time() < CALL_CLOSE)


__all__ = ["KEY", "TEST_CUSTOMERS", "Settings", "call_hours", "ten", "validate"]
