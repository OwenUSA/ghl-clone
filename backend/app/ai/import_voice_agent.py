"""Import the LIVE voice agent from owen-main into the CRM, once (phase 2b, 2026-09-25).

    uv run python -m app.ai.import_voice_agent                 # DRY RUN. Writes nothing.
    uv run python -m app.ai.import_voice_agent --commit        # ...and this one writes.
    uv run python -m app.ai.import_voice_agent --agent Intake  # when owen-main has several

It reads `GET /api/crm-link/agent-versions` (read-only on owen-main) through `crmlink`, takes
the agent's ACTIVE config, maps it with `voice.from_owen`, and creates:

  * a CRM Voice agent of the same name with that config as its draft, ANSWERING CALLS (it
    is — owen-main reported that version active) and with the write mode OFF (phase 2c,
    2026-09-25). "Answers the phone, writes nothing here" is the supervised state the owner
    wants on day one, and it is only sayable because the two are separate switches. Before
    phase 2c this created the agent "Off", which read as "not answering" about an agent that
    was live on the phone — and making Off block activation would have made that lie true
    by taking the receptionist off the phone. That is why answering is its own switch.
  * and
  * version 1 of it — when the config passes the CRM's publish rules. If it does not (no
    model set, knowledge over 6000), the draft is created and the report says what to fix.
  * When version 1 maps back to EXACTLY the live config, it is recorded as live, "imported
    from the phone system's version N" — nothing is pushed, because it is already there.
    Otherwise it reads "Answering calls — not with this version": the phone system's own
    version keeps answering until somebody presses Retry or publishes, which is the truth.

It NEVER overwrites: a CRM agent with that name, or a voice agent already pointed at that
owen-main agent, makes it stop with "already imported" and write nothing. Running it again is
therefore safe. It never writes to owen-main. The report prints lengths and counts, never the
persona, the knowledge, or a custom tool's headers.

Exit codes: 0 done (or already imported) · 2 the link is not configured · 4 no such agent ·
5 owen-main has several and none was named · 6 owen-main could not be reached or refused ·
8 the live config is not one the CRM can hold.
"""
from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select

from .. import crmlink
from ..db import SessionLocal
from ..models import AiAgent, AiAgentVersion, AiVoicePush
from . import config, voice


def _existing(db, name: str) -> AiAgent | None:
    for a in db.scalars(select(AiAgent).where(AiAgent.archived_at.is_(None))).all():
        if a.name.strip().lower() == name.strip().lower():
            return a
        if a.channel == AiAgent.VOICE and (a.draft or {}).get("owen_agent") == name:
            return a
    return None


def plan(agents: list[dict], wanted: str | None) -> tuple[dict | None, int, str]:
    """(the chosen agent, exit code, sentence)."""
    live = [a for a in agents if a.get("active_version")]
    if wanted:
        match = [a for a in agents if a.get("name") == wanted]
        if not match:
            return None, 4, "owen-main has no agent named %r (it has: %s)" % (
                wanted, ", ".join(repr(a.get("name")) for a in agents) or "none")
        if len(match) > 1:
            return None, 5, "owen-main has %d agents named %r" % (len(match), wanted)
        if not match[0].get("active_version"):
            return None, 4, "owen-main's agent %r has no active version to import" % wanted
        return match[0], 0, ""
    if len(live) == 1:
        return live[0], 0, ""
    if not live:
        return None, 4, "owen-main has no agent with an active version"
    return None, 5, ("owen-main has %d agents with an active version — name one with "
                     "--agent: %s" % (len(live), ", ".join(repr(a["name"]) for a in live)))


def run(db, *, wanted: str | None = None, commit: bool = False) -> tuple[int, dict]:
    report: dict = {"commit": commit, "wrote": False}
    if not crmlink.configured():
        report["error"] = ("the phone link is not configured on this server (set "
                           "CRM_LINK_BASE_URL and CRM_LINK_API_KEY)")
        return 2, report
    r = crmlink.agent_versions()
    if not r.ok:
        report["error"] = "could not read owen-main's agents: %s" % r.reason
        return 6, report
    agents = (r.data or {}).get("agents") or []
    report["owen_agents"] = [{"name": a.get("name"),
                              "active_version": (a.get("active_version") or {}).get("version")}
                             for a in agents]
    chosen, code, why = plan(agents, wanted)
    if chosen is None:
        report["error"] = why
        return code, report
    name = chosen["name"]
    live = chosen["active_version"]
    live_cfg = live.get("config") or {}
    report["agent"] = {"name": name, "owen_agent_id": chosen.get("agent_id"),
                       "owen_version": live.get("version")}

    existing = _existing(db, name)
    if existing is not None:
        report["outcome"] = "already imported"
        report["crm_agent_id"] = existing.id
        report["message"] = ("the CRM already has agent #%d %r for it — left untouched"
                             % (existing.id, existing.name))
        return 0, report

    draft, notes = voice.from_owen(live_cfg, name)
    try:
        c = config.clean(draft)
    except config.ConfigError as e:
        report["error"] = "the live config cannot be held by the CRM: %s" % e
        return 8, report
    problems = config.publish_problems(c)
    same = voice.comparable(voice.to_owen(c)) == voice.comparable(live_cfg)
    report.update({
        "persona_chars": len(c["persona"]),
        "greeting_chars": len(c["greeting"]),
        "knowledge_chars": len(c["knowledge_text"]),
        "model": c["model"] or None,
        "voice": c["voice"] or None,
        "tools": [voice.OWEN_TOOLS[a] for a in c["actions"]],
        "transfer_targets": len(c["transfer_targets"]),
        "custom_tools": len(c["custom_tools"]),
        "context_provider": (c["context_provider"] or {}).get("kind"),
        "other_settings_carried": sorted(c["owen_settings"]),
        "notes": notes,
        "publish_problems": problems,
        "version_1": ("would be created" if not commit else "created") if not problems
        else "not created (fix the problems, then Publish)",
        "matches_live": same,
    })
    report["answering_calls"] = True     # owen-main reported this version ACTIVE
    report["mode"] = AiAgent.OFF
    report["outcome"] = "would import" if not commit else "imported"
    if not commit:
        return 0, report

    # Answering: owen-main reported this version active, so it IS answering calls. Writes
    # nothing in the CRM (mode Off) until an admin says otherwise.
    a = AiAgent(name=name[:120], channel=AiAgent.VOICE, mode=AiAgent.OFF, draft=c,
                answering_calls=True,
                description="Imported from the phone system's version %s."
                % live.get("version"), draft_updated_at=_now())
    db.add(a)
    db.flush()
    if not problems:
        v = AiAgentVersion(agent_id=a.id, version=1, config=c)
        db.add(v)
        db.flush()
        a.published_version_id = v.id
        live_number = live.get("version") if isinstance(live.get("version"), int) else None
        if same:
            db.add(AiVoicePush(version_id=v.id, agent_id=a.id, status=AiVoicePush.LIVE,
                               imported=True, pushed_at=_now(), owen_answering=True,
                               owen_active_version=live_number,
                               owen_agent_id=str(chosen.get("agent_id") or "") or None,
                               owen_version_id=str(live.get("id") or "") or None,
                               owen_version=live_number))
        else:
            # Version 1 is not what owen-main runs; owen-main's own version still answers.
            # Recorded as exactly that — never as "live", never as "not answering".
            db.add(AiVoicePush(version_id=v.id, agent_id=a.id,
                               status=AiVoicePush.NOT_ANSWERING, owen_answering=True,
                               owen_active_version=live_number,
                               owen_agent_id=str(chosen.get("agent_id") or "") or None))
    db.commit()
    report["wrote"] = True
    report["crm_agent_id"] = a.id
    return 0, report


def _now():
    from datetime import UTC, datetime
    return datetime.now(UTC)


def _print(code: int, report: dict) -> None:
    head = "IMPORT (writes)" if report.get("commit") else "DRY RUN — nothing written"
    print(head)
    for a in report.get("owen_agents") or []:
        print("  owen-main agent %-30r active version %s" % (a["name"], a["active_version"]))
    if "error" in report:
        print("\n" + report["error"])
        return
    ag = report.get("agent") or {}
    print("\nchosen: %r (owen-main version %s)" % (ag.get("name"), ag.get("owen_version")))
    if report.get("outcome") == "already imported":
        print(report["message"])
        return
    for key in ("persona_chars", "greeting_chars", "knowledge_chars", "model", "voice",
                "tools", "transfer_targets", "custom_tools", "context_provider",
                "other_settings_carried", "version_1", "matches_live",
                "answering_calls", "mode"):
        print("  %-24s %s" % (key, report.get(key)))
    for n in report.get("notes") or []:
        print("  note: " + n)
    for p in report.get("publish_problems") or []:
        print("  to publish: " + p)
    how = "answering calls, writes Off"
    print("\n" + ("created CRM agent #%s (%s)" % (report["crm_agent_id"], how)
                  if report["wrote"] else "run again with --commit to create it (%s)" % how))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.ai.import_voice_agent",
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--agent", help="the owen-main agent's name (needed when there are several)")
    ap.add_argument("--commit", action="store_true", help="write; the default is a dry run")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args(argv)
    db = SessionLocal()
    try:
        code, report = run(db, wanted=args.agent, commit=args.commit)
        if not args.commit:
            db.rollback()
    finally:
        db.close()
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print(code, report)
    return code


if __name__ == "__main__":
    sys.exit(main())
