"""The post-inspection report's settings: fixed rules here, the editable ones in the registry
entry's `config` (AI Agents -> Overview -> Post-inspection report)."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..dispatch import config as dc

KEY = "inspection_report"
POLL_SECONDS = 120
LOOKBACK_DAYS = 2
REPORT_PX = 1600            # the stored copy, for the page and the PDF
AI_PX = 1024                # what the vision model sees
JPEG_QUALITY = 82
MAX_FILE_BYTES = 25 * 1024 * 1024
LIMITS = {"quiet_minutes": (10, 240), "max_photos": (2, 12), "max_candidates": (4, 30)}
ALL_BOARDS = [dc.INSPECTION_BOARD, dc.RETAIL_BOARD]


@dataclass
class Settings:
    test_only: bool = True
    test_jobs: list[str] = field(default_factory=lambda: ["701", "734"])
    boards: list[str] = field(default_factory=lambda: list(ALL_BOARDS))
    quiet_minutes: int = 30
    max_photos: int = 6
    max_candidates: int = 16
    vision_connection_id: int | None = None
    vision_model: str = ""

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
        if isinstance(raw.get("test_jobs"), list):
            s.test_jobs = [str(x).strip().lstrip("#") for x in raw["test_jobs"] if str(x).strip()]
        if isinstance(raw.get("boards"), list):
            s.boards = [b for b in raw["boards"] if b in ALL_BOARDS]
        v = raw.get("vision_connection_id")
        if isinstance(v, int) and not isinstance(v, bool) and v > 0:
            s.vision_connection_id = v
        elif isinstance(v, str) and v.strip().isdigit():
            s.vision_connection_id = int(v)
        if isinstance(raw.get("vision_model"), str):
            s.vision_model = raw["vision_model"].strip()
        return s

    def as_dict(self) -> dict:
        return {"test_only": self.test_only, "test_jobs": list(self.test_jobs),
                "boards": list(self.boards), "quiet_minutes": self.quiet_minutes,
                "max_photos": self.max_photos, "max_candidates": self.max_candidates,
                "vision_connection_id": self.vision_connection_id,
                "vision_model": self.vision_model}


def validate(raw: dict) -> list[str]:
    out = []
    unknown = set(raw) - set(Settings().as_dict())
    if unknown:
        out.append("unknown settings: %s" % ", ".join(sorted(unknown)))
    for k, (lo, hi) in LIMITS.items():
        if k in raw and not (isinstance(raw[k], int) and not isinstance(raw[k], bool)
                             and lo <= raw[k] <= hi):
            out.append("%s must be between %d and %d" % (k.replace("_", " "), lo, hi))
    if "boards" in raw and (not isinstance(raw["boards"], list)
                            or any(b not in ALL_BOARDS for b in raw["boards"])):
        out.append("boards must be among: %s" % ", ".join(ALL_BOARDS))
    v = raw.get("vision_connection_id")
    if v not in (None, "") and not (isinstance(v, int) and not isinstance(v, bool) and v > 0) \
            and not (isinstance(v, str) and v.strip().isdigit()):
        out.append("the vision connection is the number of an AI connection")
    if "test_only" in raw and not isinstance(raw["test_only"], bool):
        out.append("test only is on or off")
    return out
