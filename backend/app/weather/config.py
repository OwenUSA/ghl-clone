"""The weather agent's settings: fixed rules here, the editable ones in the registry entry's
`config` (AI Agents -> Overview -> Weather). Decided with the user on 2026-10-22."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..followup.config import ten

KEY = "weather"
TICK_SECONDS = 600                 # the thread wakes every 10 minutes ...
FETCH_EVERY_HOURS = 3              # ... and re-reads the forecast every 3 hours
HORIZON_DAYS = 7
SURE_DAYS = 3                      # beyond this the NWS forecast is "less certain"
URGENT_HOURS = 48                  # a flag this close rings the bell and texts at once
HEAVY_MM = 12.7                    # half an inch of rain in 48 h = "heavy rain coming"
DRY_PCT = 30                       # a daytime hour under this chance is "dry"
DAY_OPEN, DAY_CLOSE = 8, 17        # the working hours a "dry day" is judged on
STORM_LOOKBACK_HOURS = 24          # a storm that ended within this is "just passed"
REPAIRED_LOOKBACK_DAYS = 60        # roofs repaired within this are checked after a storm
# Official NWS events that count as a storm worth checking roofs after.
STORM_EVENTS = ("Severe Thunderstorm Warning", "Tornado Warning", "Tropical Storm Warning",
                "Hurricane Warning", "Flash Flood Warning", "Flood Warning",
                "Storm Surge Warning", "Extreme Wind Warning")
NWS = "https://api.weather.gov"
STATE = "FL"
# The NWS asks every caller to identify itself; no address or customer data is ever sent - only
# rounded map positions.
USER_AGENT = "(crm.dreamteamroofingfl.com, weather agent)"
LIMITS = {"rain_pct": (10, 100), "digest_hour": (5, 12)}


def enabled() -> bool:
    raw = os.getenv("WEATHER_ENABLED", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


OFF_SENTENCE = "The weather agent is switched off on this server (WEATHER_ENABLED is not set)."


@dataclass
class Settings:
    rain_pct: int = 50                       # a visit is flagged at this chance of rain or more
    phones: list[str] = field(default_factory=list)   # who gets the texts; empty = nobody
    digest_hour: int = 7                     # the morning summary, New York

    @classmethod
    def from_config(cls, raw: dict | None) -> Settings:
        s = cls()
        raw = raw if isinstance(raw, dict) else {}
        for k, (lo, hi) in LIMITS.items():
            v = raw.get(k)
            if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi:
                setattr(s, k, v)
        if isinstance(raw.get("phones"), list):
            s.phones = [p for p in (ten(x) for x in raw["phones"]) if p]
        return s

    def as_dict(self) -> dict:
        return {"rain_pct": self.rain_pct, "phones": list(self.phones),
                "digest_hour": self.digest_hour}


def validate(raw: dict) -> list[str]:
    out = []
    unknown = set(raw) - set(Settings().as_dict())
    if unknown:
        out.append("unknown settings: %s" % ", ".join(sorted(unknown)))
    for k, (lo, hi) in LIMITS.items():
        if k in raw and not (isinstance(raw[k], int) and not isinstance(raw[k], bool)
                             and lo <= raw[k] <= hi):
            out.append("%s must be between %d and %d" % (k.replace("_", " "), lo, hi))
    if "phones" in raw and (not isinstance(raw["phones"], list)
                            or any(not ten(x) for x in raw["phones"])):
        out.append("phones is a list of 10-digit numbers")
    return out
