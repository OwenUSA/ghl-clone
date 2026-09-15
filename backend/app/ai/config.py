"""An agent's configuration: its parts, their defaults, and what makes one valid.

The SAME dict shape is an agent's editable draft, a published version's frozen `config`,
and a template's `config`, so publishing and "Create agent from template" are copies, and
phase 3 can push a voice agent's version to owen-main as-is. Every key is optional in a
draft (a half-written agent saves); `validate_for_publish` is what a version must pass.
"""
from __future__ import annotations

import copy
import re

from .actions import CATALOGUE, actions_for_channel

TIMEZONE = "America/New_York"
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

TRIGGERS = {
    "missed_call": {
        "label": "Unanswered inbound call",
        "help": "A customer's call to the company was not answered.",
        "params": {},
    },
    "inbound_text": {
        "label": "Inbound text unanswered",
        "help": "A customer texted and nobody replied within the minutes set.",
        "params": {"minutes": "int"},
    },
    "stage_entered": {
        "label": "Opportunity enters stage",
        "help": "A deal moved into the chosen stage.",
        "params": {"pipeline_id": "int", "stage_id": "int"},
    },
    "appointment_booked": {"label": "Appointment booked", "help": "", "params": {}},
    "appointment_rescheduled": {"label": "Appointment rescheduled", "help": "", "params": {}},
    "appointment_cancelled": {"label": "Appointment cancelled", "help": "", "params": {}},
    "manual": {
        "label": "Manual",
        "help": "Staff press “Run AI agent” on a contact or an opportunity.",
        "params": {},
    },
}

CHANNELS = {
    "text": {"label": "Text / Chat", "available": True},
    # The value exists so a phase 3 voice agent needs no migration. It cannot be created
    # yet, and the browser does not draw it as an option (it is listed as phase 3).
    "voice": {"label": "Voice", "available": False, "phase": 3},
}

MAX_TEXT = 8000
MAX_LIST = 40
MAX_ITEM = 500


def default_config(channel: str = "text") -> dict:
    return {
        "channel": channel,
        "persona": "",
        "goals": [],
        "rules_do": [],
        "rules_dont": [],
        "knowledge_base_ids": [],
        "actions": ["get_context", "report_knowledge_gap"],
        "triggers": [],
        "schedule": {"enabled": False, "timezone": TIMEZONE, "days": list(DAYS[:5]),
                     "start": "08:00", "end": "18:00"},
        "sleep_on_staff_reply": True,
        "wait_minutes": 0,
        "max_messages": 3,
        "connection_id": None,
        "model": "",
        "extra_instructions": "",
        "escalation_user_ids": [],
    }


def merged(draft: dict | None) -> dict:
    """The draft over the defaults, so a config missing a key reads its default."""
    base = default_config((draft or {}).get("channel") or "text")
    for k, v in (draft or {}).items():
        if k in base:
            base[k] = copy.deepcopy(v)
    return base


class ConfigError(ValueError):
    pass


_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _strings(value, what: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError("%s must be a list" % what)
    out = []
    for v in value:
        if not isinstance(v, str):
            raise ConfigError("%s must be a list of text" % what)
        v = v.strip()
        if len(v) > MAX_ITEM:
            raise ConfigError("an entry in %s is longer than %d characters" % (what, MAX_ITEM))
        if v:
            out.append(v)
    if len(out) > MAX_LIST:
        raise ConfigError("%s has more than %d entries" % (what, MAX_LIST))
    return out


def _ints(value, what: str) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(
            isinstance(v, int) and not isinstance(v, bool) for v in value):
        raise ConfigError("%s must be a list of ids" % what)
    return list(dict.fromkeys(value))


def clean(draft: dict) -> dict:
    """Shape-check and normalise a draft. Unknown keys are refused, not dropped: a client
    that sends `tempreature` should hear about it."""
    if not isinstance(draft, dict):
        raise ConfigError("the configuration must be an object")
    base = default_config(draft.get("channel") or "text")
    unknown = sorted(set(draft) - set(base))
    if unknown:
        raise ConfigError("unknown setting(s): %s" % ", ".join(unknown))
    c = merged(draft)
    if c["channel"] not in CHANNELS:
        raise ConfigError("unknown channel %r" % c["channel"])
    for key in ("persona", "extra_instructions", "model"):
        if not isinstance(c[key], str):
            raise ConfigError("%s must be text" % key)
        c[key] = c[key].strip()
        if len(c[key]) > MAX_TEXT:
            raise ConfigError("%s is longer than %d characters" % (key, MAX_TEXT))
    for key in ("goals", "rules_do", "rules_dont"):
        c[key] = _strings(c[key], key)
    c["knowledge_base_ids"] = _ints(c["knowledge_base_ids"], "knowledge_base_ids")
    c["escalation_user_ids"] = _ints(c["escalation_user_ids"], "escalation_user_ids")
    if not isinstance(c["actions"], list) or not all(isinstance(a, str) for a in c["actions"]):
        raise ConfigError("actions must be a list of action names")
    offered = actions_for_channel(c["channel"])
    bad = [a for a in c["actions"] if a not in offered]
    if bad:
        raise ConfigError("not an action a %s agent can use: %s" % (
            CHANNELS[c["channel"]]["label"], ", ".join(bad)))
    # Catalogue order, so the compiled prompt and the tool list are stable.
    c["actions"] = [a for a in CATALOGUE if a in set(c["actions"])]
    for key in ("sleep_on_staff_reply",):
        if not isinstance(c[key], bool):
            raise ConfigError("%s must be true or false" % key)
    for key, lo, hi in (("wait_minutes", 0, 7 * 24 * 60), ("max_messages", 0, 50)):
        v = c[key]
        if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
            raise ConfigError("%s must be a whole number from %d to %d" % (key, lo, hi))
    if c["connection_id"] is not None and (not isinstance(c["connection_id"], int)
                                           or isinstance(c["connection_id"], bool)):
        raise ConfigError("connection_id must be an id")
    c["schedule"] = _schedule(c["schedule"])
    c["triggers"] = _triggers(c["triggers"])
    return c


def _schedule(s) -> dict:
    if not isinstance(s, dict):
        raise ConfigError("schedule must be an object")
    out = {"enabled": bool(s.get("enabled", False)), "timezone": TIMEZONE,
           "days": [], "start": s.get("start", "08:00"), "end": s.get("end", "18:00")}
    days = s.get("days", [])
    if not isinstance(days, list) or any(d not in DAYS for d in days):
        raise ConfigError("schedule days must be among %s" % ", ".join(DAYS))
    out["days"] = [d for d in DAYS if d in days]
    for key in ("start", "end"):
        if not isinstance(out[key], str) or not _HHMM.match(out[key]):
            raise ConfigError("schedule %s must be HH:MM (24-hour)" % key)
    if out["enabled"] and out["start"] >= out["end"]:
        raise ConfigError("the schedule must end after it starts")
    return out


def _triggers(ts) -> list[dict]:
    if not isinstance(ts, list):
        raise ConfigError("triggers must be a list")
    out = []
    for t in ts:
        if not isinstance(t, dict) or t.get("type") not in TRIGGERS:
            raise ConfigError("unknown trigger %r" % (t.get("type") if isinstance(t, dict)
                                                       else t))
        spec = TRIGGERS[t["type"]]
        clean_t = {"type": t["type"]}
        for name in spec["params"]:
            v = t.get(name)
            if v is None:
                continue
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                raise ConfigError("%s needs a whole number for %s" % (spec["label"], name))
            clean_t[name] = v
        extra = sorted(set(t) - {"type"} - set(spec["params"]))
        if extra:
            raise ConfigError("%s does not take %s" % (spec["label"], ", ".join(extra)))
        if t["type"] == "inbound_text":
            clean_t.setdefault("minutes", 10)
            if clean_t["minutes"] > 7 * 24 * 60:
                raise ConfigError("an unanswered-text trigger waits at most a week")
        if t["type"] == "stage_entered" and ("stage_id" not in clean_t
                                             or "pipeline_id" not in clean_t):
            raise ConfigError("Opportunity enters stage needs a pipeline and a stage")
        if clean_t in out:
            continue
        out.append(clean_t)
    if len(out) > 20:
        raise ConfigError("an agent takes at most 20 triggers")
    return out


def publish_problems(c: dict) -> list[str]:
    """What stops this config becoming a version. Empty = publishable."""
    problems = []
    if c["channel"] != "text":
        problems.append("Voice agents arrive in phase 3; only Text / Chat agents can be "
                        "published.")
    if c["connection_id"] is None:
        problems.append("Choose an AI connection.")
    if not c["model"]:
        problems.append("Choose a model.")
    if not c["persona"] and not c["goals"]:
        problems.append("Write a role / persona or at least one goal.")
    if "escalate" in c["actions"] and not c["escalation_user_ids"]:
        problems.append("Escalation is allowed, so choose who it goes to.")
    if "search_knowledge" in c["actions"] and not c["knowledge_base_ids"]:
        problems.append("Search knowledge is allowed, so attach at least one knowledge base.")
    return problems
