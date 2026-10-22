"""The field technician briefing's settings: fixed rules here, the editable ones in the registry
entry's `config` (AI Agents -> Overview -> Field technician briefing)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..dispatch import config as dc

KEY = "tech_briefing"
POLL_SECONDS = 300
MAX_TEXT = 1000              # one text; a longer briefing is split between jobs
ALL_BOARDS = [dc.INSPECTION_BOARD, dc.REPAIR_BOARD, dc.RETAIL_BOARD]


@dataclass
class Settings:
    send_time: str = "18:00"                 # New York, the day before
    test_only: bool = True
    test_technicians: list[str] = field(default_factory=lambda: ["Owen Buzaglo"])
    boards: list[str] = field(default_factory=lambda: list(ALL_BOARDS))
    skip_stages: list[str] = field(default_factory=lambda: ["Reschedule Required",
                                                            "Cancelled"])

    @classmethod
    def from_config(cls, raw: dict | None) -> Settings:
        s = cls()
        raw = raw if isinstance(raw, dict) else {}
        if isinstance(raw.get("send_time"), str) and _hhmm(raw["send_time"]):
            s.send_time = raw["send_time"].strip()
        if isinstance(raw.get("test_only"), bool):
            s.test_only = raw["test_only"]
        for k in ("test_technicians", "skip_stages"):
            if isinstance(raw.get(k), list):
                setattr(s, k, [str(x).strip() for x in raw[k] if str(x).strip()])
        if isinstance(raw.get("boards"), list):
            s.boards = [b for b in raw["boards"] if b in ALL_BOARDS]
        return s

    def as_dict(self) -> dict:
        return {"send_time": self.send_time, "test_only": self.test_only,
                "test_technicians": list(self.test_technicians), "boards": list(self.boards),
                "skip_stages": list(self.skip_stages)}

    def hour_minute(self) -> tuple[int, int]:
        return _hhmm(self.send_time) or (18, 0)


def _hhmm(value: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return (h, mi) if 0 <= h <= 23 and 0 <= mi <= 59 else None


def validate(raw: dict) -> list[str]:
    out = []
    unknown = set(raw) - set(Settings().as_dict())
    if unknown:
        out.append("unknown settings: %s" % ", ".join(sorted(unknown)))
    if "send_time" in raw and not _hhmm(raw["send_time"]):
        out.append("the send time is HH:MM, 24-hour, e.g. 18:00")
    if "test_only" in raw and not isinstance(raw["test_only"], bool):
        out.append("test only is on or off")
    if "boards" in raw and (not isinstance(raw["boards"], list)
                            or any(b not in ALL_BOARDS for b in raw["boards"])):
        out.append("boards must be among: %s" % ", ".join(ALL_BOARDS))
    for k in ("test_technicians", "skip_stages"):
        if k in raw and not isinstance(raw[k], list):
            out.append("%s is a list" % k.replace("_", " "))
    return out
