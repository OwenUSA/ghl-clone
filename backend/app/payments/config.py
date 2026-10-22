"""Payments & collections settings: fixed rules here, the editable ones in the registry entry's
`config` (AI Agents -> Overview -> Payments & collections)."""
from __future__ import annotations

from dataclasses import dataclass

KEY = "payments"
POLL_SECONDS = 600
LIMITS = {"deposit_days_before": (0, 14), "grace_days": (0, 30), "remind_every_days": (1, 30),
          "max_reminders": (1, 10)}
DEFAULT_REMINDER = ("Hi {first_name}, a friendly reminder from Dream Team Roofing: the {label} "
                    "of {amount} for your roof work is due. If you already paid, reply here and "
                    "we'll update it. Thank you!")


@dataclass
class Settings:
    deposit_days_before: int = 1
    grace_days: int = 3
    remind_every_days: int = 3
    max_reminders: int = 3
    vision_connection_id: int | None = None
    vision_model: str = ""
    reminder_text: str = DEFAULT_REMINDER

    @classmethod
    def from_config(cls, raw: dict | None) -> Settings:
        s = cls()
        raw = raw if isinstance(raw, dict) else {}
        for k, (lo, hi) in LIMITS.items():
            v = raw.get(k)
            if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi:
                setattr(s, k, v)
        v = raw.get("vision_connection_id")
        if isinstance(v, int) and not isinstance(v, bool) and v > 0:
            s.vision_connection_id = v
        elif isinstance(v, str) and v.strip().isdigit():
            s.vision_connection_id = int(v)
        for k in ("vision_model", "reminder_text"):
            if isinstance(raw.get(k), str) and (raw[k].strip() or k == "vision_model"):
                setattr(s, k, raw[k].strip())
        return s

    def as_dict(self) -> dict:
        return {"deposit_days_before": self.deposit_days_before, "grace_days": self.grace_days,
                "remind_every_days": self.remind_every_days,
                "max_reminders": self.max_reminders,
                "vision_connection_id": self.vision_connection_id,
                "vision_model": self.vision_model, "reminder_text": self.reminder_text}


def validate(raw: dict) -> list[str]:
    out = []
    unknown = set(raw) - set(Settings().as_dict())
    if unknown:
        out.append("unknown settings: %s" % ", ".join(sorted(unknown)))
    for k, (lo, hi) in LIMITS.items():
        if k in raw and not (isinstance(raw[k], int) and not isinstance(raw[k], bool)
                             and lo <= raw[k] <= hi):
            out.append("%s must be between %d and %d" % (k.replace("_", " "), lo, hi))
    t = raw.get("reminder_text")
    if t is not None and (not isinstance(t, str) or not t.strip() or len(t) > 320
                          or "http" in t.lower()):
        out.append("the reminder is up to 320 characters, with no link")
    v = raw.get("vision_connection_id")
    if v not in (None, "") and not (isinstance(v, int) and not isinstance(v, bool) and v > 0) \
            and not (isinstance(v, str) and v.strip().isdigit()):
        out.append("the vision connection is the number of an AI connection")
    return out
