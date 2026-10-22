"""Insights (agent #10) settings: fixed rules here, the editable ones in the registry entry's
`config` (AI Agents -> Overview -> Daily report & insights)."""
from __future__ import annotations

from dataclasses import dataclass, field

KEY = "insights"
POLL_SECONDS = 3600            # tag every hour; the snapshot once a day
NIGHT_HOUR = 3                 # with tag_hourly off, the reading runs once, in this hour
LOOKBACK_DAYS = 3              # a call or text older than this is never read for the first time
PER_RUN = 200                  # at most this many calls / texts read in one pass
BATCH = 20                     # read together in one AI request
MIN_CHARS = 20                 # shorter than this says nothing worth reading

TAGS = {
    "still_leaking": "Still leaking",
    "callback_request": "Asked us to come back",
    "complaint": "Complaint",
    "upsell_interest": "Interested in more work",
    "payment_issue": "Payment issue",
    "praise": "Praise",
    "schedule_change": "Schedule change",
}
ALERT_TAGS = ("still_leaking", "callback_request", "complaint")

STAGE_INSPECTED = ("Inspection Completed", "Proposal Made")
STAGE_REPAIRED = ("Repair Complete",)
STAGE_AHS_APPROVED = ("AHS Approved",)


def ten(value) -> str:
    d = "".join(ch for ch in str(value or "") if ch.isdigit())
    return d[-10:] if len(d) >= 10 else ""


@dataclass
class Settings:
    exclude_phones: list[str] = field(default_factory=list)
    tag_hourly: bool = True

    @classmethod
    def from_config(cls, raw: dict | None) -> Settings:
        s = cls()
        raw = raw if isinstance(raw, dict) else {}
        if isinstance(raw.get("exclude_phones"), list):
            s.exclude_phones = [p for p in (ten(x) for x in raw["exclude_phones"]) if p]
        if isinstance(raw.get("tag_hourly"), bool):
            s.tag_hourly = raw["tag_hourly"]
        return s

    def as_dict(self) -> dict:
        return {"exclude_phones": list(self.exclude_phones), "tag_hourly": self.tag_hourly}


def validate(raw: dict) -> list[str]:
    out = []
    unknown = set(raw) - set(Settings().as_dict())
    if unknown:
        out.append("unknown settings: %s" % ", ".join(sorted(unknown)))
    if "exclude_phones" in raw and (not isinstance(raw["exclude_phones"], list)
                                    or any(not ten(x) for x in raw["exclude_phones"])):
        out.append("excluded numbers must be full phone numbers")
    if "tag_hourly" in raw and not isinstance(raw["tag_hourly"], bool):
        out.append("tag hourly is on or off")
    return out
