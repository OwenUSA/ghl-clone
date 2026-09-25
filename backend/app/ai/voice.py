"""Voice agents: the ONE place a CRM agent's config meets owen-main's (phase 2b, 2026-09-25).

The CRM edits a voice agent; owen-main runs it (DECISIONS.md, 2026-09-22 "voice AI agents").
A published CRM version is mapped here into owen-main's `agent_versions.config` and pushed
(`push.py`); the one-off import maps the live owen-main config back (`import_voice_agent.py`).
Nothing else in the CRM knows owen-main's key names.

    CRM draft key          owen-main config key     notes
    ---------------------  -----------------------  ----------------------------------------
    persona + goals +      persona                  `persona_for`: the persona alone when
      rules + extra                                 nothing else is written, so an imported
                                                    persona round-trips byte for byte
    greeting               greeting
    voice                  voice                    e.g. aura-2-andromeda-en
    model                  model                    owen-voice's LLM (decision 9: gpt-4o-mini)
    llm_base_url           llm_base_url
    stt_provider           stt_provider
    tts_provider           tts_provider
    knowledge_text         knowledge                ≤ 6000 characters or Publish refuses
    actions                tools                    transfer_call → transfer, end_call,
                                                    capture_lead; nothing else maps
    guardrails             guardrails               max_call_seconds, max_silence_seconds
    transfer_targets       transfer_targets         {name: {kind, target}}
    context_provider       context_provider         e.g. {"kind": "crm_link"}
    custom_tools           custom_tools             owen-main's HTTP tool declarations
    engine                 engine                   left unset = owen-main's own choice
    owen_settings          (spread at top level)    owen-main keys the CRM does not edit,
                                                    carried unchanged so a push never drops
                                                    one (tts_instructions, …)
    owen_agent             (the push's agent_name)  which owen-main agent this publishes to

WHAT A VOICE AGENT MAY NOT HAVE, and why (refused by `clean`, not just hidden):

* `send_text`. owen-main refuses `send_sms` for the owen_voice engine that answers real calls
  (`app/agents/tools.py` `engines`), and texting stays manual (2026-09-15). Offering it would
  be offering something that can never happen.
* Every other CRM action. They are carried out by the CRM's engine on a CRM run; a call runs
  in owen-voice, which today can transfer, end the call and capture a lead — nothing else.
  Booking / rescheduling / cancelling are also out by decision 4 (no calendar writes from a
  phone call); the rest wait for the CRM's voice-tool endpoints (phase 2, not built).
* Every CRM trigger, and the CRM schedule. A voice agent is started by a call reaching it
  through an owen-main flow; business hours live in that flow and nowhere else (decision 6).
* An AI connection, knowledge bases and escalation users: owen-voice uses its own model key,
  cannot search a CRM knowledge base mid-call yet, and has no escalate tool.
"""
from __future__ import annotations

import copy
import json

from .config import ConfigError

KNOWLEDGE_MAX = 6000        # owen-main's KNOWLEDGE_MAX_CHARS (app/agents/service.py)
KNOWLEDGE_DRAFT_MAX = 30000  # a draft may run over while being cut down; Publish refuses
MAX_PASSTHROUGH = 20000

# CRM action name → owen-main tool name. The ONLY actions a voice agent may have.
OWEN_TOOLS = {"transfer_call": "transfer", "end_call": "end_call",
              "capture_lead": "capture_lead"}

# CRM key → owen-main key, for the plain text settings (only sent when set).
TEXT_KEYS = {"greeting": "greeting", "voice": "voice", "model": "model",
             "llm_base_url": "llm_base_url", "stt_provider": "stt_provider",
             "tts_provider": "tts_provider", "engine": "engine"}
TEXT_LIMITS = {"greeting": 2000, "voice": 100, "model": 100, "llm_base_url": 500,
               "stt_provider": 50, "tts_provider": 50, "engine": 50, "owen_agent": 200}
STRUCTURED = ("guardrails", "transfer_targets", "context_provider", "custom_tools")
# owen-main keys the mapping owns. `crm_*` are owen-main's own stamp on a pushed version.
OWEN_MODELLED = {"persona", "knowledge", "tools", *TEXT_KEYS.values(), *STRUCTURED,
                 "crm_version", "crm_agent_id"}
TRANSFER_KINDS = ("number", "operator", "flow", "agent")

SEND_TEXT_REFUSAL = ("A voice agent cannot send texts: the phone system's voice engine has no "
                     "texting (owen-main refuses send_sms for owen_voice), and texting "
                     "customers stays a person's job.")


def defaults() -> dict:
    return {"greeting": "", "voice": "", "llm_base_url": "", "stt_provider": "",
            "tts_provider": "", "engine": "", "knowledge_text": "",
            "guardrails": {}, "transfer_targets": {}, "context_provider": None,
            "custom_tools": [], "owen_settings": {}, "owen_agent": ""}


DEFAULT_ACTIONS = ["capture_lead", "end_call"]


def _json_size(value) -> int:
    return len(json.dumps(value, default=str))


def clean(c: dict) -> dict:
    """Validate a voice agent's config (already through `config.clean`'s shared checks)."""
    for key, limit in TEXT_LIMITS.items():
        if not isinstance(c[key], str):
            raise ConfigError("%s must be text" % key)
        c[key] = c[key].strip()
        if len(c[key]) > limit:
            raise ConfigError("%s is longer than %d characters" % (key, limit))
    if c["llm_base_url"] and not c["llm_base_url"].lower().startswith(("http://",
                                                                       "https://")):
        raise ConfigError("llm_base_url must start with http:// or https://")
    if not isinstance(c["knowledge_text"], str):
        raise ConfigError("knowledge_text must be text")
    c["knowledge_text"] = c["knowledge_text"].strip()
    if len(c["knowledge_text"]) > KNOWLEDGE_DRAFT_MAX:
        raise ConfigError("the in-call knowledge is %d characters — even a draft holds at "
                          "most %d" % (len(c["knowledge_text"]), KNOWLEDGE_DRAFT_MAX))

    if "send_text" in c["actions"]:
        raise ConfigError(SEND_TEXT_REFUSAL)
    other = [a for a in c["actions"] if a not in OWEN_TOOLS]
    if other:
        raise ConfigError("a voice agent can only transfer the call, end the call and "
                          "capture a lead — not %s" % ", ".join(other))
    if c["triggers"]:
        raise ConfigError("a voice agent is started by a call reaching it through the phone "
                          "system's flow, so it takes no CRM triggers")
    if c["schedule"]["enabled"]:
        raise ConfigError("a voice agent's hours are the phone system flow's business "
                          "hours, never a CRM schedule — switch the schedule off")
    if c["connection_id"] is not None:
        raise ConfigError("a voice agent runs on the phone system's own model, so it takes "
                          "no AI connection")
    if c["knowledge_base_ids"]:
        raise ConfigError("a voice agent cannot search a knowledge base during a call yet — "
                          "put what it must know in its in-call knowledge")
    if c["escalation_user_ids"]:
        raise ConfigError("a voice agent has no escalate tool yet, so it takes no "
                          "escalation users")

    g = c["guardrails"]
    if not isinstance(g, dict):
        raise ConfigError("guardrails must be an object")
    for key, hi in (("max_call_seconds", 4 * 3600), ("max_silence_seconds", 600)):
        v = g.get(key)
        if v is None:
            g.pop(key, None)
        elif not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= hi:
            raise ConfigError("guardrails.%s must be a whole number of seconds from 1 to %d"
                              % (key, hi))

    t = c["transfer_targets"]
    if not isinstance(t, dict):
        raise ConfigError("transfer_targets must be an object of {name: {kind, target}}")
    if len(t) > 20:
        raise ConfigError("an agent takes at most 20 transfer targets")
    for name, entry in t.items():
        if not isinstance(entry, dict):
            raise ConfigError("transfer target %r must be an object" % name)
        if str(entry.get("kind") or "number") not in TRANSFER_KINDS:
            raise ConfigError("transfer target %r has unknown kind %r (%s)" % (
                name, entry.get("kind"), " | ".join(TRANSFER_KINDS)))
        if not str(entry.get("target") or "").strip():
            raise ConfigError("transfer target %r has no target" % name)

    if c["context_provider"] is not None and not isinstance(c["context_provider"], dict):
        raise ConfigError("context_provider must be an object, e.g. {\"kind\": \"crm_link\"}")
    if not isinstance(c["custom_tools"], list) or not all(
            isinstance(x, dict) for x in c["custom_tools"]):
        raise ConfigError("custom_tools must be a list of tool declarations")
    if len(c["custom_tools"]) > 20:
        raise ConfigError("an agent takes at most 20 custom tools")

    o = c["owen_settings"]
    if not isinstance(o, dict):
        raise ConfigError("owen_settings must be an object")
    clash = sorted(set(o) & OWEN_MODELLED)
    if clash:
        raise ConfigError("owen_settings may not hold %s — the CRM edits those itself"
                          % ", ".join(clash))
    for key, value in (("custom_tools", c["custom_tools"]),
                       ("context_provider", c["context_provider"]),
                       ("transfer_targets", t), ("guardrails", g), ("owen_settings", o)):
        if _json_size(value) > MAX_PASSTHROUGH:
            raise ConfigError("%s is too large" % key)
    return c


def publish_problems(c: dict) -> list[str]:
    problems = []
    if not c["owen_agent"]:
        problems.append("Name the phone system agent this publishes to.")
    if not c["model"]:
        problems.append("Choose a model.")
    if not c["persona"] and not c["goals"]:
        problems.append("Write a role / persona or at least one goal.")
    n = len(c["knowledge_text"])
    if n > KNOWLEDGE_MAX:
        cut = n - KNOWLEDGE_MAX
        problems.append("The in-call knowledge is %d characters; the phone system takes at "
                        "most %d. Cut %d character%s." % (n, KNOWLEDGE_MAX, cut,
                                                         "" if cut == 1 else "s"))
    return problems


def persona_for(c: dict) -> str:
    """The persona owen-main receives. Only the sections the owner wrote, in a fixed order,
    so the persona ALONE compiles to itself — an imported agent pushes back unchanged."""
    parts = []
    if c.get("persona"):
        parts.append(c["persona"])
    if c.get("goals"):
        parts.append("Goals:\n" + "\n".join("%d. %s" % (i + 1, g)
                                            for i, g in enumerate(c["goals"])))
    if c.get("rules_do"):
        parts.append("Always:\n" + "\n".join("- " + r for r in c["rules_do"]))
    if c.get("rules_dont"):
        parts.append("Never:\n" + "\n".join("- " + r for r in c["rules_dont"]))
    if c.get("extra_instructions"):
        parts.append(c["extra_instructions"])
    return "\n\n".join(parts)


def to_owen(c: dict) -> dict:
    """A published CRM version as owen-main's `agent_versions.config`."""
    out = copy.deepcopy(c.get("owen_settings") or {})
    out["persona"] = persona_for(c)
    for crm_key, owen_key in TEXT_KEYS.items():
        if c.get(crm_key):
            out[owen_key] = c[crm_key]
    if c.get("knowledge_text"):
        out["knowledge"] = c["knowledge_text"]
    out["tools"] = {OWEN_TOOLS[a]: True for a in c.get("actions") or [] if a in OWEN_TOOLS}
    for key in STRUCTURED:
        if c.get(key):
            out[key] = copy.deepcopy(c[key])
    return out


def from_owen(cfg: dict, agent_name: str) -> tuple[dict, list[str]]:
    """owen-main's live config as a CRM voice draft, and what could not come across."""
    from .config import default_config

    cfg = cfg if isinstance(cfg, dict) else {}
    notes = []
    d = default_config("voice")
    d["owen_agent"] = agent_name
    d["persona"] = str(cfg.get("persona") or "")
    for crm_key, owen_key in TEXT_KEYS.items():
        d[crm_key] = str(cfg.get(owen_key) or "")
    d["knowledge_text"] = str(cfg.get("knowledge") or "")
    tools = cfg.get("tools") if isinstance(cfg.get("tools"), dict) else {}
    to_crm = {owen: crm for crm, owen in OWEN_TOOLS.items()}
    d["actions"] = [to_crm[name] for name in OWEN_TOOLS.values() if tools.get(name)]
    for name, on in tools.items():
        if on and name not in to_crm:
            notes.append("tool %r is on in the phone system and has no CRM equivalent — "
                         "left off%s" % (name, " (a voice agent cannot text)"
                                         if name == "send_sms" else ""))
    for key in STRUCTURED:
        if cfg.get(key):
            d[key] = copy.deepcopy(cfg[key])
    d["owen_settings"] = {k: copy.deepcopy(v) for k, v in cfg.items()
                          if k not in OWEN_MODELLED}
    return d, notes


def comparable(owen_cfg: dict) -> dict:
    """An owen-main config minus what does not change behaviour: the CRM stamp, tools
    toggled OFF (absent and false mean the same thing to `enabled_tools`), and whitespace
    at the ends of a text setting (the CRM trims every one it stores)."""
    out = {k: (v.strip() if isinstance(v, str) else v)
           for k, v in (owen_cfg or {}).items() if k not in ("crm_version", "crm_agent_id")}
    tools = out.get("tools") if isinstance(out.get("tools"), dict) else {}
    out["tools"] = {k: True for k, v in tools.items() if v}
    return {k: v for k, v in out.items() if v not in ("", None, {}, [])
            or k == "tools"}
