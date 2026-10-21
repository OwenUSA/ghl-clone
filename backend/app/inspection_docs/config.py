"""The inspection-documentation agent's settings: fixed rules here, the editable ones in the
registry entry's `config` (AI Agents -> Overview -> Inspection documentation)."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..dispatch import config as dc

KEY = "inspection_docs"
POLL_SECONDS = 300
LOOKBACK_DAYS = 2            # a visit older than this never starts a check
EXPIRE_DAYS = 3              # an open check gives up after this
NOTES_BEFORE_HOURS = 12      # notes written this long before the visit still count
CALL_AGENT = "DTR Inspection Assistant"

LIMITS = {"grace_minutes": (5, 480), "recheck_minutes": (10, 1440)}
ALL_BOARDS = [dc.INSPECTION_BOARD, dc.RETAIL_BOARD, dc.REPAIR_BOARD]


def ten(value) -> str:
    d = "".join(ch for ch in str(value or "") if ch.isdigit())
    return d[-10:] if len(d) >= 10 else ""


@dataclass
class Settings:
    test_only: bool = True
    test_jobs: list[str] = field(default_factory=lambda: ["701", "734"])
    boards: list[str] = field(default_factory=lambda: list(ALL_BOARDS))
    grace_minutes: int = 30
    recheck_minutes: int = 60
    # Zuper technician name -> phone (ten digits). Nothing maps them anywhere else.
    technicians: dict[str, str] = field(default_factory=dict)
    caller_id: str = ""                       # empty = the CRM line (crmlink)
    call_agent: str = CALL_AGENT

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
        if isinstance(raw.get("technicians"), dict):
            s.technicians = {str(k).strip(): ten(v) for k, v in raw["technicians"].items()
                             if str(k).strip() and ten(v)}
        if isinstance(raw.get("caller_id"), str):
            s.caller_id = raw["caller_id"].strip()
        if isinstance(raw.get("call_agent"), str) and raw["call_agent"].strip():
            s.call_agent = raw["call_agent"].strip()
        return s

    def as_dict(self) -> dict:
        return {"test_only": self.test_only, "test_jobs": list(self.test_jobs),
                "boards": list(self.boards), "grace_minutes": self.grace_minutes,
                "recheck_minutes": self.recheck_minutes, "technicians": dict(self.technicians),
                "caller_id": self.caller_id, "call_agent": self.call_agent}

    def phone_for(self, name: str | None) -> str | None:
        """A technician's phone by their Zuper name; a first-name match is enough when it is
        the only one."""
        n = (name or "").strip().lower()
        if not n:
            return None
        for k, v in self.technicians.items():
            if k.lower() == n:
                return v
        first = n.split()[0]
        hits = [v for k, v in self.technicians.items() if k.lower().split()[0] == first]
        return hits[0] if len(hits) == 1 else None


def validate(raw: dict) -> list[str]:
    out = []
    unknown = set(raw) - set(Settings().as_dict())
    if unknown:
        out.append("unknown settings: %s" % ", ".join(sorted(unknown)))
    for k, (lo, hi) in LIMITS.items():
        if k in raw and not (isinstance(raw[k], int) and not isinstance(raw[k], bool)
                             and lo <= raw[k] <= hi):
            out.append("%s must be between %d and %d minutes" % (k.replace("_", " "), lo, hi))
    if "technicians" in raw:
        t = raw["technicians"]
        if not isinstance(t, dict) or any(not str(k).strip() or not ten(v) for k, v in t.items()):
            out.append("each technician needs a name and a full phone number")
    if "boards" in raw and (not isinstance(raw["boards"], list)
                            or any(b not in ALL_BOARDS for b in raw["boards"])):
        out.append("boards must be among: %s" % ", ".join(ALL_BOARDS))
    if "test_only" in raw and not isinstance(raw["test_only"], bool):
        out.append("test only is on or off")
    if "caller_id" in raw and str(raw["caller_id"] or "").strip() and not ten(raw["caller_id"]):
        out.append("the caller ID must be a full phone number")
    return out
