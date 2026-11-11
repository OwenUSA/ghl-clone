"""Follow-ups' settings (2026-11-11, decided with the user when the design was grilled): fixed
rules here, the few an admin edits in the registry entry's `config` (AI Agents -> Overview ->
Follow-ups), and the default sequences created the first time the page is opened."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time

from ..autoreply.config import SENDER_DISPLAY, TEST_CUSTOMERS, holidays
from ..dispatch.config import aware, local

KEY = "followups"                         # its registry entry
POLL_SECONDS = 60
LOOKBACK_HOURS = 6                        # an unanswered call older than this never starts one
GRACE_HOURS = 24                          # after the last step, wait this long for a reply
AUTO_TEXT_GAP_HOURS = 24                  # Q26: never two automatic texts to one number
AI_CALLS_PER_NUMBER_DAY = 2               # Q17
SEND_DAYS = (0, 1, 2, 3, 4, 5)            # Monday-Saturday, New York (Q13)
SEND_OPEN, SEND_CLOSE = time(8, 0), time(20, 0)
OUR_CALL_WINDOW_MINUTES = 15              # our AI call, reported back by a phone line
# Zuper status types that mean the job is over: no follow-up starts by itself for them (Q16).
CLOSED_STATUS_TYPES = frozenset({"CANCELED", "CANCELLED", "PAID", "LOST"})
STEP_TYPES = ("text", "call_me", "ai_call")
STEP_LABEL = {"text": "Text", "call_me": "Call (me)", "ai_call": "AI call"}
MAX_STEPS = 12
TEMPLATE_MAX = 320

LIMITS = {"texts_per_day": (0, 200), "ai_calls_per_day": (0, 100)}


def ten(value) -> str:
    d = "".join(ch for ch in str(value or "") if ch.isdigit())
    return d[-10:] if len(d) >= 10 else ""


@dataclass
class Settings:
    test_only: bool = True
    # Numbers (last ten digits) counted as test customers beside the Zuper TEST customers'.
    test_phones: list[str] = field(default_factory=lambda: ["5618788090"])
    texts_per_day: int = 40                   # Q25: every customer together, New York day
    ai_calls_per_day: int = 20
    call_agent: str = "DTR Retell - Brian"    # owen-main's name for Anisa (Q22)
    caller_id: str = ""                       # empty = the CRM line (crmlink, ONE place)

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
        for k in ("call_agent", "caller_id"):
            v = raw.get(k)
            if isinstance(v, str):
                setattr(s, k, v.strip() or getattr(cls(), k))
        if isinstance(raw.get("test_phones"), list):
            s.test_phones = [p for p in (ten(x) for x in raw["test_phones"]) if p]
        return s

    def as_dict(self) -> dict:
        return {"test_only": self.test_only, "test_phones": list(self.test_phones),
                "texts_per_day": self.texts_per_day, "ai_calls_per_day": self.ai_calls_per_day,
                "call_agent": self.call_agent, "caller_id": self.caller_id}


def validate(raw: dict) -> list[str]:
    """Problems with settings a person typed; empty = fine."""
    out = []
    unknown = set(raw) - set(Settings().as_dict())
    if unknown:
        out.append("unknown settings: %s" % ", ".join(sorted(unknown)))
    for k, (lo, hi) in LIMITS.items():
        if k in raw and not (isinstance(raw[k], int) and not isinstance(raw[k], bool)
                             and lo <= raw[k] <= hi):
            out.append("%s must be a whole number between %d and %d" % (
                k.replace("_", " "), lo, hi))
    if "test_only" in raw and not isinstance(raw["test_only"], bool):
        out.append("test only must be true or false")
    if "test_phones" in raw and not (isinstance(raw["test_phones"], list) and all(
            ten(x) for x in raw["test_phones"])):
        out.append("test phones must be a list of 10-digit numbers")
    return out


# ---- the default sequences (Q21) -------------------------------------------------------------
# Same shape on every board; the wording names what the call was about. Wording rules (owner,
# 2026-10-08): never say a claim is "approved", no prices, no dates promised.

def _steps(first: str, second: str) -> list[dict]:
    return [
        {"type": "text", "delay_minutes": 5, "template": first},
        {"type": "call_me", "delay_days": 1, "at": "10:00"},
        {"type": "ai_call", "delay_days": 1, "at": "14:00"},
        {"type": "text", "delay_days": 2, "at": "11:00", "template": second},
        {"type": "call_me", "delay_days": 3, "at": "10:00"},
    ]


CALL_US = "Reply here or call us back at %s." % SENDER_DISPLAY
DEFAULT_SEQUENCES: list[dict] = [
    # Most AHS jobs are made on Simple Flow since 2026-11-06 (inspection and repair on one
    # board), so its wording names neither.
    {"name": "AHS Simple Flow", "board": "AHS Simple Flow", "steps": _steps(
        "Hi {first_name}, this is Dream Team Roofing. We just tried to call you about your "
        "American Home Shield request for your roof. " + CALL_US,
        "Hi {first_name}, Dream Team Roofing again, about your American Home Shield request "
        "for your roof. Reply with a good day and time to talk, or call %s." % SENDER_DISPLAY)},
    {"name": "AHS - Inspection", "board": "AHS - Inspection", "steps": _steps(
        "Hi {first_name}, this is Dream Team Roofing. We just tried to call you about the roof "
        "inspection for your American Home Shield request. " + CALL_US,
        "Hi {first_name}, Dream Team Roofing again. We still need to set up your roof "
        "inspection for American Home Shield. Reply with a good day and time, or call %s."
        % SENDER_DISPLAY)},
    {"name": "AHS - Repair & Review", "board": "AHS - Repair & Review", "steps": _steps(
        "Hi {first_name}, this is Dream Team Roofing. We just tried to call you about your roof "
        "repair. " + CALL_US,
        "Hi {first_name}, Dream Team Roofing again. We would like to schedule your roof repair. "
        "Reply with a good day and time, or call %s." % SENDER_DISPLAY)},
    {"name": "Retail", "board": "Retail", "steps": _steps(
        "Hi {first_name}, this is Dream Team Roofing. We just tried to call you about your "
        "roof. " + CALL_US,
        "Hi {first_name}, Dream Team Roofing again. We are still here to help with your roof. "
        "Reply here or call %s whenever it suits you." % SENDER_DISPLAY)},
    {"name": "General", "board": None, "steps": _steps(
        "Hi {first_name}, this is Dream Team Roofing. We just tried to call you. " + CALL_US,
        "Hi {first_name}, Dream Team Roofing again, following up on our call. Reply here or "
        "call %s whenever it suits you." % SENDER_DISPLAY)},
]


__all__ = ["TEST_CUSTOMERS", "aware", "holidays", "local"]
