"""The /api/ai routes. One router, mounted by main.py under the app-level auth gate.

WHO MAY DO WHAT (owner's decisions, 2026-09-15):

* ADMIN — everything.
* DISPATCHER — may open the module, read agents, knowledge bases and templates, run an
  agent on a customer by hand, approve or dismiss suggestions, and switch an agent OFF
  (the emergency stop). May not edit, publish, switch an agent to Suggest or Auto-pilot,
  use Try-it, see Agent Logs or metrics, knowledge gaps, connections or settings.
* TECH, and any user with "Only assigned data" on — nothing: 403 on every route.

Only an ADMIN switches an agent to Auto-pilot, and the request must carry `confirm: true`.
Nothing here returns a provider API key: a connection reads "•••• last4".
"""
from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import assigned_access, auth, pipeline_access
from ..db import get_db
from ..models import (
    AiAgent,
    AiAgentVersion,
    AiAlert,
    AiConnection,
    AiFolder,
    AiKbChunk,
    AiKbItem,
    AiKnowledgeBase,
    AiKnowledgeGap,
    AiRun,
    AiRunStep,
    AiSettings,
    AiSuggestion,
    AiTemplate,
    Contact,
    Opportunity,
    Role,
    User,
)
from ..phones import format_phone, store_phone
from . import actions, config, engine, knowledge, pricing, providers, push, triggers, vault, voice
from .prompt import compile_prompt

router = APIRouter(prefix="/api/ai")

NOT_AVAILABLE = ("AI Agents are available to admins and dispatchers only — not to a "
                 "technician or a user with “Only assigned data” on.")


def _viewer(principal: auth.Principal | None = Depends(auth.current_principal)
            ) -> auth.Principal:
    if principal is None:
        raise HTTPException(401, "authentication required")
    if principal.role not in (Role.ADMIN, Role.DISPATCHER) or assigned_access.restricted(
            principal):
        raise HTTPException(403, NOT_AVAILABLE)
    return principal


def _admin(principal: auth.Principal = Depends(_viewer)) -> auth.Principal:
    if principal.role is not Role.ADMIN:
        raise HTTPException(403, "only an admin can do this in AI Agents")
    return principal


VIEW = Depends(_viewer)
ADMIN = Depends(_admin)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime | None) -> str | None:
    return engine.aware(dt).isoformat() if dt else None


# ============================================================ catalogue & settings

@router.get("/catalogue")
def ai_catalogue(_: auth.Principal = VIEW):
    """What the builder offers: actions, triggers, channels, providers, known prices."""
    return {
        "actions": actions.catalogue_payload(),
        "triggers": [{"type": k, **v} for k, v in config.TRIGGERS.items()],
        "channels": [{"value": k, **v} for k, v in config.CHANNELS.items()],
        "providers": [
            {"value": "anthropic", "label": "Anthropic", "default_model": "claude-sonnet-5",
             "needs_base_url": False},
            {"value": "openai", "label": "OpenAI", "default_model": "gpt-5-mini",
             "needs_base_url": False},
            {"value": "openai_compatible", "label": "OpenAI-compatible (custom base URL)",
             "default_model": "", "needs_base_url": True}],
        "known_prices": {m: {"input": pricing.dollars(i), "output": pricing.dollars(o)}
                         for m, (i, o) in pricing.KNOWN_PRICES.items()},
        "modes": list(AiAgent.MODES),
        "days": list(config.DAYS),
        "timezone": config.TIMEZONE,
        "secrets_configured": vault.configured(),
        "default_config": config.default_config(),
    }


def _settings_out(s: AiSettings) -> dict:
    return {"paused": bool(s.paused), "on_call_phone": s.on_call_phone,
            "on_call_phone_display": format_phone(s.on_call_phone) if s.on_call_phone
            else None, "updated_at": _iso(s.updated_at),
            "secrets_configured": vault.configured()}


class SettingsIn(BaseModel):
    paused: bool | None = None
    on_call_phone: str | None = Field(None, max_length=40)


@router.get("/settings")
def ai_get_settings(db: Session = Depends(get_db), _: auth.Principal = VIEW):
    return _settings_out(engine.settings(db))


@router.put("/settings")
def ai_put_settings(body: SettingsIn, db: Session = Depends(get_db),
                    principal: auth.Principal = ADMIN):
    s = db.get(AiSettings, 1)
    if s is None:
        s = AiSettings(id=1, paused=False)
        db.add(s)
    data = body.model_dump(exclude_unset=True)
    if "paused" in data and data["paused"] is not None:
        s.paused = data["paused"]
    if "on_call_phone" in data:
        raw = (data["on_call_phone"] or "").strip()
        s.on_call_phone = store_phone(raw) if raw else None
    s.updated_at, s.updated_by_id = _now(), principal.user_id
    db.commit()
    return _settings_out(s)


# ============================================================ connections

def _connection_out(c: AiConnection, db: Session) -> dict:
    used_by = sum(1 for a in db.scalars(select(AiAgent).where(AiAgent.archived_at.is_(None)))
                  if (a.draft or {}).get("connection_id") == c.id)
    return {"id": c.id, "name": c.name, "provider": c.provider, "base_url": c.base_url,
            "api_key": vault.masked(c.api_key_last4), "default_model": c.default_model,
            "price_input": pricing.dollars(c.price_input_micros),
            "price_output": pricing.dollars(c.price_output_micros),
            "created_at": _iso(c.created_at), "updated_at": _iso(c.updated_at),
            "agents": used_by}


class ConnectionIn(BaseModel):
    name: str = Field(max_length=120)
    provider: str
    base_url: str | None = Field(None, max_length=500)
    api_key: str | None = Field(None, max_length=500)
    default_model: str = Field(max_length=200)
    price_input: str | float | None = None
    price_output: str | float | None = None


class ConnectionPatch(BaseModel):
    name: str | None = Field(None, max_length=120)
    base_url: str | None = Field(None, max_length=500)
    api_key: str | None = Field(None, max_length=500)
    default_model: str | None = Field(None, max_length=200)
    price_input: str | float | None = None
    price_output: str | float | None = None


def _price(value, what: str) -> int | None:
    try:
        return pricing.to_micros(value)
    except (ValueError, ArithmeticError):
        raise HTTPException(400, "%s must be a price in dollars per 1M tokens, like 2.50"
                            % what) from None


def _clean_base_url(provider: str, url: str | None) -> str | None:
    url = (url or "").strip() or None
    if provider != AiConnection.COMPATIBLE:
        return None
    if not url:
        raise HTTPException(400, "an OpenAI-compatible connection needs its base URL")
    if not url.startswith(("https://", "http://")):
        raise HTTPException(400, "the base URL must start with https:// (or http://)")
    return url.rstrip("/")


@router.get("/connections")
def ai_list_connections(db: Session = Depends(get_db), _: auth.Principal = ADMIN):
    return [_connection_out(c, db) for c in
            db.scalars(select(AiConnection).order_by(AiConnection.id)).all()]


@router.get("/connection-names")
def ai_connection_names(db: Session = Depends(get_db), _: auth.Principal = VIEW):
    """What an agent's page names its connection by — no URL, no key, no price."""
    return [{"id": c.id, "name": c.name, "provider": c.provider,
             "default_model": c.default_model}
            for c in db.scalars(select(AiConnection).order_by(AiConnection.id)).all()]


@router.post("/connections", status_code=201)
def ai_create_connection(body: ConnectionIn, db: Session = Depends(get_db),
                         principal: auth.Principal = ADMIN):
    if body.provider not in AiConnection.PROVIDERS:
        raise HTTPException(400, "unknown provider %r" % body.provider)
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "a connection needs a name")
    key = (body.api_key or "").strip()
    if not key:
        raise HTTPException(400, "enter the API key")
    model = body.default_model.strip()
    if not model:
        raise HTTPException(400, "choose a default model")
    try:
        encrypted = vault.encrypt(key)
    except vault.SecretsUnavailable as e:
        raise HTTPException(503, str(e)) from None
    c = AiConnection(name=name, provider=body.provider,
                     base_url=_clean_base_url(body.provider, body.base_url),
                     api_key_encrypted=encrypted, api_key_last4=vault.last4(key),
                     default_model=model, created_by_id=principal.user_id,
                     price_input_micros=_price(body.price_input, "price per 1M input tokens"),
                     price_output_micros=_price(body.price_output,
                                                "price per 1M output tokens"))
    db.add(c)
    db.commit()
    return _connection_out(c, db)


def _get_connection(db: Session, connection_id: int) -> AiConnection:
    c = db.get(AiConnection, connection_id)
    if c is None:
        raise HTTPException(404, "connection not found")
    return c


@router.patch("/connections/{connection_id}")
def ai_update_connection(connection_id: int, body: ConnectionPatch,
                         db: Session = Depends(get_db), _: auth.Principal = ADMIN):
    c = _get_connection(db, connection_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("api_key"):
        try:
            c.api_key_encrypted = vault.encrypt(data["api_key"].strip())
        except vault.SecretsUnavailable as e:
            raise HTTPException(503, str(e)) from None
        c.api_key_last4 = vault.last4(data["api_key"])
    if data.get("name") is not None:
        if not data["name"].strip():
            raise HTTPException(400, "a connection needs a name")
        c.name = data["name"].strip()
    if "base_url" in data:
        c.base_url = _clean_base_url(c.provider, data["base_url"])
    if data.get("default_model") is not None:
        if not data["default_model"].strip():
            raise HTTPException(400, "choose a default model")
        c.default_model = data["default_model"].strip()
    if "price_input" in data:
        c.price_input_micros = _price(data["price_input"], "price per 1M input tokens")
    if "price_output" in data:
        c.price_output_micros = _price(data["price_output"], "price per 1M output tokens")
    c.updated_at = _now()
    db.commit()
    return _connection_out(c, db)


@router.delete("/connections/{connection_id}")
def ai_delete_connection(connection_id: int, db: Session = Depends(get_db),
                         _: auth.Principal = ADMIN):
    c = _get_connection(db, connection_id)
    using = [a.name for a in db.scalars(select(AiAgent).where(AiAgent.archived_at.is_(None)))
             .all() if (a.draft or {}).get("connection_id") == c.id]
    if using:
        raise HTTPException(409, "agents still use this connection: %s" % ", ".join(using))
    db.delete(c)
    db.commit()
    return {"deleted": connection_id}


class ConnectionProbe(BaseModel):
    """Test / Load models for a connection that is not saved yet. Writes nothing."""
    provider: str
    base_url: str | None = Field(None, max_length=500)
    api_key: str | None = Field(None, max_length=500)
    model: str | None = Field(None, max_length=200)
    connection_id: int | None = None


def _probe_provider(db: Session, body: ConnectionProbe) -> providers.Provider:
    if body.provider not in AiConnection.PROVIDERS:
        raise HTTPException(400, "unknown provider %r" % body.provider)
    key = (body.api_key or "").strip()
    base_url = _clean_base_url(body.provider, body.base_url)
    if not key and body.connection_id is not None:
        c = _get_connection(db, body.connection_id)
        try:
            key = vault.decrypt(c.api_key_encrypted)
        except vault.SecretsUnavailable as e:
            raise HTTPException(503, str(e)) from None
    if not key:
        raise HTTPException(400, "enter the API key")
    return providers.build(body.provider, key, base_url)


@router.post("/connections/test")
def ai_test_connection(body: ConnectionProbe, db: Session = Depends(get_db),
                       _: auth.Principal = ADMIN):
    """ONE minimal request to the provider. Answers OK or the error sentence and the
    latency, and writes nothing — not even to the connection."""
    model = (body.model or "").strip()
    if not model:
        raise HTTPException(400, "choose a model to test")
    result = _probe_provider(db, body).test(model)
    return {"ok": result.ok, "sentence": result.sentence, "latency_ms": result.latency_ms}


@router.post("/connections/models")
def ai_load_models(body: ConnectionProbe, db: Session = Depends(get_db),
                   _: auth.Principal = ADMIN):
    try:
        models = _probe_provider(db, body).list_models()
    except providers.ProviderError as e:
        return {"ok": False, "sentence": e.sentence, "models": []}
    return {"ok": True, "sentence": "%d models" % len(models), "models": models}


# ============================================================ folders

class FolderIn(BaseModel):
    name: str = Field(max_length=120)


@router.get("/folders")
def ai_list_folders(db: Session = Depends(get_db), _: auth.Principal = VIEW):
    counts = dict(db.execute(select(AiAgent.folder_id, func.count(AiAgent.id))
                             .where(AiAgent.archived_at.is_(None))
                             .group_by(AiAgent.folder_id)).all())
    return [{"id": f.id, "name": f.name, "agents": counts.get(f.id, 0)}
            for f in db.scalars(select(AiFolder).order_by(AiFolder.position, AiFolder.id))]


@router.post("/folders", status_code=201)
def ai_create_folder(body: FolderIn, db: Session = Depends(get_db),
                     _: auth.Principal = ADMIN):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "a folder needs a name")
    n = db.scalar(select(func.count(AiFolder.id))) or 0
    f = AiFolder(name=name, position=n)
    db.add(f)
    db.commit()
    return {"id": f.id, "name": f.name, "agents": 0}


@router.patch("/folders/{folder_id}")
def ai_rename_folder(folder_id: int, body: FolderIn, db: Session = Depends(get_db),
                     _: auth.Principal = ADMIN):
    f = db.get(AiFolder, folder_id)
    if f is None:
        raise HTTPException(404, "folder not found")
    if not body.name.strip():
        raise HTTPException(400, "a folder needs a name")
    f.name = body.name.strip()
    db.commit()
    return {"id": f.id, "name": f.name}


@router.delete("/folders/{folder_id}")
def ai_delete_folder(folder_id: int, db: Session = Depends(get_db),
                     _: auth.Principal = ADMIN):
    """Deleting a folder moves its agents out of it; no agent is touched otherwise."""
    f = db.get(AiFolder, folder_id)
    if f is None:
        raise HTTPException(404, "folder not found")
    moved = 0
    for a in db.scalars(select(AiAgent).where(AiAgent.folder_id == f.id)).all():
        a.folder_id = None
        moved += 1
    db.delete(f)
    db.commit()
    return {"deleted": folder_id, "agents_moved_out": moved}


# ============================================================ agents

def _compiled(c: dict, name: str) -> str:
    """What the model is told: the CRM's system prompt for a text agent; for a voice agent,
    the persona owen-main receives (`voice.persona_for`)."""
    return voice.persona_for(c) if c.get("channel") == "voice" else compile_prompt(c, name)


def _agent_row(db: Session, a: AiAgent) -> dict:
    version = db.get(AiAgentVersion, a.published_version_id) if a.published_version_id \
        else None
    last = db.scalar(select(AiRun).where(AiRun.agent_id == a.id, AiRun.is_test.is_(False))
                     .order_by(AiRun.created_at.desc(), AiRun.id.desc()).limit(1))
    folder = db.get(AiFolder, a.folder_id) if a.folder_id else None
    return {"id": a.id, "name": a.name, "description": a.description, "channel": a.channel,
            "mode": a.mode, "folder_id": a.folder_id,
            "folder_name": folder.name if folder else None,
            "published_version": version.version if version else None,
            "published_at": _iso(version.published_at) if version else None,
            "has_unpublished_changes": version is None or config.merged(a.draft) != \
            config.merged(version.config),
            "last_run_at": _iso(last.created_at) if last else None,
            "last_outcome": last.outcome if last else None,
            "updated_at": _iso(a.updated_at or a.created_at),
            "triggers": [t["type"] for t in config.merged(a.draft)["triggers"]],
            # What actually runs: "Run AI agent" offers only agents whose PUBLISHED
            # version has the Manual trigger.
            "published_triggers": [t["type"] for t in config.merged(version.config)["triggers"]]
            if version else [],
            # Voice only: is the published version the one answering the phone?
            "live_on_phone_system": (push.state(db, a) or {}).get("live")
            if a.channel == AiAgent.VOICE else None}


def _get_agent(db: Session, agent_id: int) -> AiAgent:
    a = db.get(AiAgent, agent_id)
    if a is None or a.archived_at is not None:
        raise HTTPException(404, "agent not found")
    return a


def _agent_detail(db: Session, a: AiAgent, principal: auth.Principal) -> dict:
    draft = config.merged(a.draft)
    versions = db.scalars(select(AiAgentVersion).where(AiAgentVersion.agent_id == a.id)
                          .order_by(AiAgentVersion.version.desc())).all()
    publishers = {u.id: u.name for u in db.scalars(select(User).where(
        User.id.in_({v.published_by_id for v in versions if v.published_by_id})))}
    return {**_agent_row(db, a), "draft": draft,
            "compiled_prompt": _compiled(draft, a.name),
            "phone_system": push.state(db, a),
            "publish_problems": config.publish_problems(draft),
            "versions": [{"id": v.id, "version": v.version, "published_at": _iso(v.published_at),
                          "published_by": publishers.get(v.published_by_id),
                          "current": v.id == a.published_version_id} for v in versions],
            "can_edit": principal.role is Role.ADMIN}


class AgentCreate(BaseModel):
    name: str = Field(max_length=120)
    description: str | None = Field(None, max_length=2000)
    channel: str = "text"
    folder_id: int | None = None
    template_id: int | None = None


class AgentPatch(BaseModel):
    name: str | None = Field(None, max_length=120)
    description: str | None = Field(None, max_length=2000)
    folder_id: int | None = None
    draft: dict | None = None


def _check_folder(db: Session, folder_id: int | None) -> None:
    if folder_id is not None and db.get(AiFolder, folder_id) is None:
        raise HTTPException(400, "unknown folder")


def _clean_draft(db: Session, draft: dict) -> dict:
    try:
        c = config.clean(draft)
    except config.ConfigError as e:
        raise HTTPException(400, str(e)) from None
    if c["connection_id"] is not None and db.get(AiConnection, c["connection_id"]) is None:
        raise HTTPException(400, "that AI connection does not exist")
    known_kbs = set(db.scalars(select(AiKnowledgeBase.id)).all())
    missing = [k for k in c["knowledge_base_ids"] if k not in known_kbs]
    if missing:
        raise HTTPException(400, "unknown knowledge base(s): %s" % missing)
    for uid in c["escalation_user_ids"]:
        u = db.get(User, uid)
        if u is None or not u.is_active or u.role not in (Role.ADMIN, Role.DISPATCHER) \
                or (u.only_assigned_data and u.role is not Role.ADMIN):
            raise HTTPException(400, "escalations go to active admins or dispatchers — "
                                "user %s is not one" % uid)
    for t in c["triggers"]:
        if t["type"] == "stage_entered":
            from ..models import Stage
            stage = db.get(Stage, t["stage_id"])
            if stage is None or stage.pipeline_id != t["pipeline_id"]:
                raise HTTPException(400, "the trigger's stage is not in its pipeline")
    return c


@router.get("/agents")
def ai_list_agents(db: Session = Depends(get_db), folder_id: int | None = None,
                   q: str | None = None, _: auth.Principal = VIEW):
    stmt = select(AiAgent).where(AiAgent.archived_at.is_(None))
    if folder_id is not None:
        stmt = stmt.where(AiAgent.folder_id == folder_id)
    rows = db.scalars(stmt.order_by(AiAgent.name, AiAgent.id)).all()
    if q and q.strip():
        needle = q.strip().lower()
        rows = [a for a in rows if needle in a.name.lower()
                or needle in (a.description or "").lower()]
    return [_agent_row(db, a) for a in rows]


@router.post("/agents", status_code=201)
def ai_create_agent(body: AgentCreate, db: Session = Depends(get_db),
                    principal: auth.Principal = ADMIN):
    """Every agent is created OFF, a Voice agent included (2026-09-25)."""
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "an agent needs a name")
    if body.channel not in config.CHANNELS:
        raise HTTPException(400, "the channel is text or voice")
    channel = body.channel
    draft = config.default_config(channel)
    if body.template_id is not None:
        t = db.get(AiTemplate, body.template_id)
        if t is None:
            raise HTTPException(404, "template not found")
        draft = config.merged(t.config)
        channel = t.channel
    _check_folder(db, body.folder_id)
    draft = _clean_draft(db, {**draft, "channel": channel})
    a = AiAgent(name=name, description=(body.description or "").strip() or None,
                channel=channel, folder_id=body.folder_id, mode=AiAgent.OFF, draft=draft,
                created_by_id=principal.user_id, draft_updated_at=_now())
    db.add(a)
    db.commit()
    return _agent_detail(db, a, principal)


@router.get("/agents/{agent_id}")
def ai_get_agent(agent_id: int, db: Session = Depends(get_db),
                 principal: auth.Principal = VIEW):
    return _agent_detail(db, _get_agent(db, agent_id), principal)


@router.patch("/agents/{agent_id}")
def ai_update_agent(agent_id: int, body: AgentPatch, db: Session = Depends(get_db),
                    principal: auth.Principal = ADMIN):
    """Saves the DRAFT. The published version — what runs — does not change."""
    a = _get_agent(db, agent_id)
    data = body.model_dump(exclude_unset=True)
    if "name" in data:
        if not (data["name"] or "").strip():
            raise HTTPException(400, "an agent needs a name")
        a.name = data["name"].strip()
    if "description" in data:
        a.description = (data["description"] or "").strip() or None
    if "folder_id" in data:
        _check_folder(db, data["folder_id"])
        a.folder_id = data["folder_id"]
    if data.get("draft") is not None:
        if data["draft"].get("channel", a.channel) != a.channel:
            raise HTTPException(400, "an agent's channel cannot be changed")
        a.draft = _clean_draft(db, {**data["draft"], "channel": a.channel})
        a.draft_updated_at = _now()
    a.updated_at = _now()
    db.commit()
    return _agent_detail(db, a, principal)


class PromptPreview(BaseModel):
    draft: dict
    name: str | None = None


@router.post("/agents/{agent_id}/compiled-prompt")
def ai_preview_prompt(agent_id: int, body: PromptPreview, db: Session = Depends(get_db),
                      _: auth.Principal = VIEW):
    """The compiled prompt for an UNSAVED draft. Read-only; writes nothing."""
    a = _get_agent(db, agent_id)
    try:
        c = config.clean({**body.draft, "channel": a.channel})
    except config.ConfigError as e:
        raise HTTPException(400, str(e)) from None
    return {"compiled_prompt": _compiled(c, (body.name or a.name).strip() or a.name),
            "publish_problems": config.publish_problems(c)}


@router.post("/agents/{agent_id}/publish", status_code=201)
def ai_publish_agent(agent_id: int, db: Session = Depends(get_db),
                     principal: auth.Principal = ADMIN):
    """Freeze the draft as a new, IMMUTABLE version. Runs from now on use it. A VOICE
    version is then queued for owen-main (`push.py`) — the publish never waits on it, and
    succeeds whether or not the phone system is reachable."""
    a = _get_agent(db, agent_id)
    c = _clean_draft(db, {**(a.draft or {}), "channel": a.channel})
    problems = config.publish_problems(c)
    if problems:
        raise HTTPException(400, "not ready to publish: " + " ".join(problems))
    n = db.scalar(select(func.max(AiAgentVersion.version)).where(
        AiAgentVersion.agent_id == a.id)) or 0
    v = AiAgentVersion(agent_id=a.id, version=n + 1, config=c,
                       published_by_id=principal.user_id)
    db.add(v)
    db.flush()
    a.published_version_id = v.id
    a.updated_at = _now()
    if a.channel == AiAgent.VOICE:
        push.request(db, a, v)
    db.commit()
    return _agent_detail(db, a, principal)


@router.post("/agents/{agent_id}/push", status_code=202)
def ai_push_agent(agent_id: int, db: Session = Depends(get_db),
                  principal: auth.Principal = ADMIN):
    """Retry: send a voice agent's published version to the phone system again. Queued,
    like the push Publish makes; the answer is the agent, with its phone-system state."""
    a = _get_agent(db, agent_id)
    try:
        push.retry(db, a)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    db.commit()
    return _agent_detail(db, a, principal)


@router.get("/agents/{agent_id}/versions/{version}")
def ai_get_version(agent_id: int, version: int, db: Session = Depends(get_db),
                   _: auth.Principal = VIEW):
    a = _get_agent(db, agent_id)
    v = db.scalar(select(AiAgentVersion).where(AiAgentVersion.agent_id == a.id,
                                               AiAgentVersion.version == version))
    if v is None:
        raise HTTPException(404, "version not found")
    return {"id": v.id, "version": v.version, "config": v.config,
            "published_at": _iso(v.published_at),
            "compiled_prompt": _compiled(config.merged(v.config), a.name)}


class ModeIn(BaseModel):
    mode: str
    confirm: bool = False


@router.post("/agents/{agent_id}/mode")
def ai_set_mode(agent_id: int, body: ModeIn, db: Session = Depends(get_db),
                principal: auth.Principal = VIEW):
    """Off / Suggest / Auto-pilot. A DISPATCHER may only switch an agent OFF. Auto-pilot
    is ADMIN-only and must be confirmed. Suggest and Auto-pilot need a published version."""
    a = _get_agent(db, agent_id)
    if body.mode not in AiAgent.MODES:
        raise HTTPException(400, "mode is off, suggest or auto")
    if body.mode != AiAgent.OFF and principal.role is not Role.ADMIN:
        raise HTTPException(403, "only an admin can switch an agent on — a dispatcher can "
                                 "switch one off")
    if body.mode != AiAgent.OFF and a.published_version_id is None:
        raise HTTPException(400, "publish the agent before switching it on")
    if body.mode == AiAgent.AUTO and a.mode != AiAgent.AUTO and not body.confirm:
        raise HTTPException(409, "Auto-pilot lets “%s” text customers and change records "
                                 "without asking. Confirm to switch it on." % a.name)
    a.mode = body.mode
    a.updated_at = _now()
    db.commit()
    return _agent_detail(db, a, principal)


@router.delete("/agents/{agent_id}")
def ai_delete_agent(agent_id: int, db: Session = Depends(get_db),
                    _: auth.Principal = ADMIN):
    """ARCHIVES the agent: it is switched off and hidden; its runs, versions and
    suggestions still read in the logs. Its queued runs are cancelled."""
    a = _get_agent(db, agent_id)
    a.mode = AiAgent.OFF
    a.archived_at = _now()
    from ..models import Job
    cancelled = 0
    for job in db.scalars(select(Job).where(Job.type == engine.JOB_TYPE,
                                            Job.status == "pending")).all():
        run = db.get(AiRun, (job.payload or {}).get("run_id"))
        if run is not None and run.agent_id == a.id and run.outcome == engine.QUEUED:
            job.status = "cancelled"
            if job.dedupe_key:
                job.payload = {**job.payload, "superseded_key": job.dedupe_key}
                job.dedupe_key = None
            engine.skip(db, run, "the agent was deleted")
            cancelled += 1
    db.commit()
    return {"deleted": agent_id, "archived": True, "queued_runs_cancelled": cancelled}


@router.post("/agents/{agent_id}/duplicate", status_code=201)
def ai_duplicate_agent(agent_id: int, db: Session = Depends(get_db),
                       principal: auth.Principal = ADMIN):
    a = _get_agent(db, agent_id)
    draft = config.merged(a.draft)
    if a.channel == AiAgent.VOICE:
        # Two CRM agents publishing to one phone-system agent would overwrite each other's
        # persona on every publish; the copy has to be pointed somewhere on purpose.
        draft["owen_agent"] = ""
    copy = AiAgent(name=(a.name + " (copy)")[:120], description=a.description,
                   channel=a.channel, folder_id=a.folder_id, mode=AiAgent.OFF,
                   draft=draft, created_by_id=principal.user_id,
                   draft_updated_at=_now())
    db.add(copy)
    db.commit()
    return _agent_detail(db, copy, principal)


class TryIn(BaseModel):
    messages: list[dict] = Field(min_length=1, max_length=40)
    contact_id: int | None = None
    opportunity_id: int | None = None
    draft: dict | None = None


def _subject(db: Session, principal: auth.Principal, contact_id: int | None,
             opportunity_id: int | None) -> tuple[int | None, int | None]:
    """A contact / opportunity a person chose. The deal must be visible to that person
    (per-pipeline access) and belong to the contact; the deal alone names its contact."""
    if opportunity_id is not None:
        o = pipeline_access.get_opportunity(db, principal, opportunity_id)
        if contact_id is not None and o.contact_id != contact_id:
            raise HTTPException(400, "that opportunity is not this contact's")
        if o.contact_id is None:
            raise HTTPException(400, "that opportunity has no contact, and an agent always "
                                     "acts for a customer")
        contact_id = o.contact_id
    if contact_id is not None:
        assigned_access.get_contact(db, principal, contact_id)
    return contact_id, opportunity_id


@router.post("/agents/{agent_id}/try")
def ai_try_agent(agent_id: int, body: TryIn, db: Session = Depends(get_db),
                 principal: auth.Principal = ADMIN):
    """Try-it: chat as a customer against the DRAFT (the unsaved one, when sent). The model
    and the read-only tools run for real; no write action ever runs — each comes back as a
    "Would: …" card. Logged as a test run."""
    a = _get_agent(db, agent_id)
    if a.channel == AiAgent.VOICE:
        raise HTTPException(400, "Try-it chats with a text agent. A voice agent runs on the "
                                 "phone system — call a number its flow answers to try it.")
    c = _clean_draft(db, body.draft if body.draft is not None else (a.draft or {}))
    if c["connection_id"] is None or not c["model"]:
        raise HTTPException(400, "choose an AI connection and a model to try the agent")
    chat = []
    for m in body.messages:
        role, content = m.get("role"), m.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str) \
                or not content.strip() or len(content) > 4000:
            raise HTTPException(400, "each message needs a role (user or assistant) and "
                                     "text up to 4000 characters")
        chat.append({"role": role, "content": content})
    if chat[-1]["role"] != "user":
        raise HTTPException(400, "the last message must be the customer's")
    contact_id, opportunity_id = _subject(db, principal, body.contact_id, body.opportunity_id)
    run, result = engine.try_it(db, a, c, chat, contact_id=contact_id,
                                opportunity_id=opportunity_id, created_by_id=principal.user_id)
    return {"run_id": run.id, "outcome": result.outcome, "reason": result.reason,
            "reply": result.reply, "would": result.would,
            "tokens": {"input": run.input_tokens, "output": run.output_tokens,
                       "cache_write": run.cache_write_tokens,
                       "cache_read": run.cache_read_tokens},
            "cost": pricing.dollars(run.cost_micros), "latency_ms": run.latency_ms,
            "steps": [_step_out(s) for s in _steps(db, run.id)]}


class RunIn(BaseModel):
    contact_id: int | None = None
    opportunity_id: int | None = None


@router.post("/agents/{agent_id}/run", status_code=202)
def ai_run_agent(agent_id: int, body: RunIn, db: Session = Depends(get_db),
                 principal: auth.Principal = VIEW):
    """"Run AI agent" from a contact or an opportunity. Queued for the worker now."""
    a = _get_agent(db, agent_id)
    if a.mode == AiAgent.OFF:
        raise HTTPException(400, "“%s” is Off — switch it to Suggest or Auto-pilot first"
                            % a.name)
    if a.published_version_id is None:
        raise HTTPException(400, "“%s” has no published version" % a.name)
    version = db.get(AiAgentVersion, a.published_version_id)
    if not any(t["type"] == "manual" for t in config.merged(version.config)["triggers"]):
        raise HTTPException(400, "“%s” does not have the Manual trigger" % a.name)
    if body.contact_id is None and body.opportunity_id is None:
        raise HTTPException(400, "choose a contact or an opportunity")
    contact_id, opportunity_id = _subject(db, principal, body.contact_id, body.opportunity_id)
    run = triggers.manual(db, a, contact_id=contact_id, opportunity_id=opportunity_id,
                          user_id=principal.user_id)
    db.commit()
    return {"run_id": run.id, "outcome": run.outcome, "reason": run.reason,
            "mode": a.mode}


# ============================================================ templates

class TemplateIn(BaseModel):
    agent_id: int
    name: str = Field(max_length=120)
    description: str | None = Field(None, max_length=2000)


@router.get("/templates")
def ai_list_templates(db: Session = Depends(get_db), _: auth.Principal = VIEW):
    return [{"id": t.id, "name": t.name, "description": t.description, "channel": t.channel,
             "created_at": _iso(t.created_at),
             "actions": config.merged(t.config)["actions"],
             "triggers": [x["type"] for x in config.merged(t.config)["triggers"]]}
            for t in db.scalars(select(AiTemplate).order_by(AiTemplate.name, AiTemplate.id))]


@router.post("/templates", status_code=201)
def ai_save_template(body: TemplateIn, db: Session = Depends(get_db),
                     principal: auth.Principal = ADMIN):
    """"Save as template": a copy of the agent's DRAFT. The connection is kept as a
    reference; no key is ever part of a template."""
    a = _get_agent(db, body.agent_id)
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "a template needs a name")
    t = AiTemplate(name=name, description=(body.description or "").strip() or None,
                   channel=a.channel, config=config.merged(a.draft),
                   created_by_id=principal.user_id)
    db.add(t)
    db.commit()
    return {"id": t.id, "name": t.name}


@router.delete("/templates/{template_id}")
def ai_delete_template(template_id: int, db: Session = Depends(get_db),
                       _: auth.Principal = ADMIN):
    t = db.get(AiTemplate, template_id)
    if t is None:
        raise HTTPException(404, "template not found")
    db.delete(t)
    db.commit()
    return {"deleted": template_id}


# ============================================================ knowledge bases

class KbIn(BaseModel):
    name: str = Field(max_length=120)
    description: str | None = Field(None, max_length=2000)


def _kb_out(db: Session, kb: AiKnowledgeBase) -> dict:
    counts = dict(db.execute(select(AiKbItem.kind, func.count(AiKbItem.id))
                             .where(AiKbItem.kb_id == kb.id).group_by(AiKbItem.kind)).all())
    agents = [a.name for a in db.scalars(select(AiAgent).where(AiAgent.archived_at.is_(None)))
              if kb.id in (config.merged(a.draft)["knowledge_base_ids"])]
    return {"id": kb.id, "name": kb.name, "description": kb.description,
            "faqs": counts.get("faq", 0), "articles": counts.get("article", 0),
            "files": counts.get("file", 0), "agents": agents,
            "updated_at": _iso(kb.updated_at or kb.created_at)}


def _get_kb(db: Session, kb_id: int) -> AiKnowledgeBase:
    kb = db.get(AiKnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(404, "knowledge base not found")
    return kb


@router.get("/knowledge-bases")
def ai_list_kbs(db: Session = Depends(get_db), _: auth.Principal = VIEW):
    return [_kb_out(db, kb) for kb in db.scalars(
        select(AiKnowledgeBase).order_by(AiKnowledgeBase.name, AiKnowledgeBase.id))]


@router.post("/knowledge-bases", status_code=201)
def ai_create_kb(body: KbIn, db: Session = Depends(get_db), _: auth.Principal = ADMIN):
    if not body.name.strip():
        raise HTTPException(400, "a knowledge base needs a name")
    kb = AiKnowledgeBase(name=body.name.strip(),
                         description=(body.description or "").strip() or None)
    db.add(kb)
    db.commit()
    return _kb_out(db, kb)


@router.patch("/knowledge-bases/{kb_id}")
def ai_update_kb(kb_id: int, body: KbIn, db: Session = Depends(get_db),
                 _: auth.Principal = ADMIN):
    kb = _get_kb(db, kb_id)
    if not body.name.strip():
        raise HTTPException(400, "a knowledge base needs a name")
    kb.name, kb.description = body.name.strip(), (body.description or "").strip() or None
    kb.updated_at = _now()
    db.commit()
    return _kb_out(db, kb)


@router.delete("/knowledge-bases/{kb_id}")
def ai_delete_kb(kb_id: int, db: Session = Depends(get_db), _: auth.Principal = ADMIN):
    """Refused while any agent's draft or published version attaches it."""
    kb = _get_kb(db, kb_id)
    using = set()
    for a in db.scalars(select(AiAgent).where(AiAgent.archived_at.is_(None))).all():
        if kb.id in config.merged(a.draft)["knowledge_base_ids"]:
            using.add(a.name)
        v = db.get(AiAgentVersion, a.published_version_id) if a.published_version_id else None
        if v is not None and kb.id in config.merged(v.config)["knowledge_base_ids"]:
            using.add(a.name)
    if using:
        raise HTTPException(409, "agents use this knowledge base: %s — detach it and publish "
                                 "them first" % ", ".join(sorted(using)))
    for item in db.scalars(select(AiKbItem).where(AiKbItem.kb_id == kb.id)).all():
        db.delete(item)
    db.flush()
    db.delete(kb)
    db.commit()
    return {"deleted": kb_id}


def _item_out(i: AiKbItem, full: bool = False) -> dict:
    out = {"id": i.id, "kb_id": i.kb_id, "kind": i.kind, "title": i.title,
           "content_type": i.content_type, "size_bytes": i.size_bytes,
           "created_at": _iso(i.created_at), "updated_at": _iso(i.updated_at or i.created_at),
           "characters": len(i.body or "")}
    out["body"] = i.body if full or i.kind != AiKbItem.FILE else (i.body or "")[:600]
    return out


@router.get("/knowledge-bases/{kb_id}/items")
def ai_list_kb_items(kb_id: int, kind: str | None = None, db: Session = Depends(get_db),
                     _: auth.Principal = VIEW):
    kb = _get_kb(db, kb_id)
    stmt = select(AiKbItem).where(AiKbItem.kb_id == kb.id)
    if kind:
        stmt = stmt.where(AiKbItem.kind == kind)
    return [_item_out(i) for i in db.scalars(stmt.order_by(AiKbItem.id.desc()))]


class ItemIn(BaseModel):
    kind: str
    title: str = Field(max_length=500)
    body: str = Field(max_length=knowledge.MAX_TEXT_CHARS)


class ItemPatch(BaseModel):
    title: str | None = Field(None, max_length=500)
    body: str | None = Field(None, max_length=knowledge.MAX_TEXT_CHARS)


def _add_item(db: Session, kb: AiKnowledgeBase, kind: str, title: str, body: str,
              user_id: int, **extra) -> AiKbItem:
    title, body = (title or "").strip(), (body or "").strip()
    if not title:
        raise HTTPException(400, "a question needs its wording" if kind == AiKbItem.FAQ
                            else "a title is needed")
    if not body:
        raise HTTPException(400, "an FAQ needs its answer" if kind == AiKbItem.FAQ
                            else "the text is empty")
    item = AiKbItem(kb_id=kb.id, kind=kind, title=title, body=body, created_by_id=user_id,
                    **extra)
    db.add(item)
    db.flush()
    knowledge.reindex(db, item)
    kb.updated_at = _now()
    return item


@router.post("/knowledge-bases/{kb_id}/items", status_code=201)
def ai_add_kb_item(kb_id: int, body: ItemIn, db: Session = Depends(get_db),
                   principal: auth.Principal = ADMIN):
    """An FAQ or an article. Files go through /files."""
    kb = _get_kb(db, kb_id)
    if body.kind not in (AiKbItem.FAQ, AiKbItem.ARTICLE):
        raise HTTPException(400, "kind is faq or article")
    item = _add_item(db, kb, body.kind, body.title, body.body, principal.user_id)
    db.commit()
    return _item_out(item, True)


class FileIn(BaseModel):
    filename: str = Field(max_length=255)
    content_type: str | None = Field(None, max_length=120)
    # base64 of the file. JSON rather than multipart: no new dependency, and the browser's
    # CSRF-carrying `send()` works unchanged.
    data: str = Field(max_length=15_000_000)


@router.post("/knowledge-bases/{kb_id}/files", status_code=201)
def ai_upload_kb_file(kb_id: int, body: FileIn, db: Session = Depends(get_db),
                      principal: auth.Principal = ADMIN):
    """A PDF or a Word .docx. Its TEXT is extracted and kept with its name, type and size;
    the bytes are not stored (this CRM has no file store)."""
    kb = _get_kb(db, kb_id)
    try:
        raw = base64.b64decode(body.data, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "the file did not arrive intact (bad base64)") from None
    try:
        text, kind = knowledge.extract_text(body.filename, body.content_type, raw)
    except knowledge.ExtractionError as e:
        raise HTTPException(400, str(e)) from None
    item = _add_item(db, kb, AiKbItem.FILE, body.filename.strip() or "file", text,
                     principal.user_id, content_type=kind, size_bytes=len(raw))
    db.commit()
    return _item_out(item, True)


def _get_item(db: Session, item_id: int) -> AiKbItem:
    i = db.get(AiKbItem, item_id)
    if i is None:
        raise HTTPException(404, "item not found")
    return i


@router.get("/kb-items/{item_id}")
def ai_get_kb_item(item_id: int, db: Session = Depends(get_db), _: auth.Principal = VIEW):
    return _item_out(_get_item(db, item_id), True)


@router.patch("/kb-items/{item_id}")
def ai_update_kb_item(item_id: int, body: ItemPatch, db: Session = Depends(get_db),
                      _: auth.Principal = ADMIN):
    i = _get_item(db, item_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("title") is not None:
        if not data["title"].strip():
            raise HTTPException(400, "a title is needed")
        i.title = data["title"].strip()
    if data.get("body") is not None:
        if i.kind == AiKbItem.FILE:
            raise HTTPException(400, "a file's text comes from the file — upload it again")
        if not data["body"].strip():
            raise HTTPException(400, "the text is empty")
        i.body = data["body"].strip()
    i.updated_at = _now()
    knowledge.reindex(db, i)
    db.commit()
    return _item_out(i, True)


@router.delete("/kb-items/{item_id}")
def ai_delete_kb_item(item_id: int, db: Session = Depends(get_db),
                      _: auth.Principal = ADMIN):
    i = _get_item(db, item_id)
    for c in db.scalars(select(AiKbChunk).where(AiKbChunk.item_id == i.id)).all():
        db.delete(c)
    db.delete(i)
    db.commit()
    return {"deleted": item_id}


class SearchIn(BaseModel):
    query: str = Field(max_length=500)
    knowledge_base_ids: list[int] = Field(min_length=1, max_length=50)


@router.post("/knowledge-bases/search")
def ai_search_kbs(body: SearchIn, db: Session = Depends(get_db), _: auth.Principal = VIEW):
    """What an agent attached to these knowledge bases would find. Records no gap."""
    hits = knowledge.RETRIEVER.search(db, body.knowledge_base_ids, body.query)
    return {"useful": knowledge.useful(hits),
            "results": [{"item_id": h.item_id, "kb_id": h.kb_id, "title": h.title,
                         "kind": h.kind, "text": h.text, "score": round(h.score, 3)}
                        for h in hits]}


# ============================================================ knowledge gaps (ADMIN)

@router.get("/knowledge-gaps")
def ai_list_gaps(status: str = "open", db: Session = Depends(get_db),
                 _: auth.Principal = ADMIN):
    stmt = select(AiKnowledgeGap)
    if status != "all":
        stmt = stmt.where(AiKnowledgeGap.status == status)
    rows = db.scalars(stmt.order_by(AiKnowledgeGap.last_seen_at.desc(),
                                    AiKnowledgeGap.id.desc())).all()
    names = {a.id: a.name for a in db.scalars(select(AiAgent))}
    return [{"id": g.id, "question": g.question, "status": g.status, "count": g.count,
             "first_seen_at": _iso(g.first_seen_at), "last_seen_at": _iso(g.last_seen_at),
             "last_run_id": g.last_run_id, "agent_id": g.agent_id,
             "agent_name": names.get(g.agent_id), "resolved_item_id": g.resolved_item_id}
            for g in rows]


class GapResolve(BaseModel):
    knowledge_base_id: int
    answer: str = Field(max_length=20000)
    question: str | None = Field(None, max_length=500)


@router.post("/knowledge-gaps/{gap_id}/resolve")
def ai_resolve_gap(gap_id: int, body: GapResolve, db: Session = Depends(get_db),
                   principal: auth.Principal = ADMIN):
    """Resolve by answering: the FAQ is created in the chosen knowledge base."""
    g = db.get(AiKnowledgeGap, gap_id)
    if g is None:
        raise HTTPException(404, "knowledge gap not found")
    if g.status != AiKnowledgeGap.OPEN:
        raise HTTPException(409, "this gap was already %s" % g.status)
    kb = _get_kb(db, body.knowledge_base_id)
    item = _add_item(db, kb, AiKbItem.FAQ, body.question or g.question[:500], body.answer,
                     principal.user_id)
    g.status, g.resolved_item_id = AiKnowledgeGap.RESOLVED, item.id
    g.decided_at, g.decided_by_id = _now(), principal.user_id
    db.commit()
    return {"id": g.id, "status": g.status, "faq": _item_out(item, True)}


@router.post("/knowledge-gaps/{gap_id}/dismiss")
def ai_dismiss_gap(gap_id: int, db: Session = Depends(get_db),
                   principal: auth.Principal = ADMIN):
    g = db.get(AiKnowledgeGap, gap_id)
    if g is None:
        raise HTTPException(404, "knowledge gap not found")
    if g.status != AiKnowledgeGap.OPEN:
        raise HTTPException(409, "this gap was already %s" % g.status)
    g.status, g.decided_at, g.decided_by_id = (AiKnowledgeGap.DISMISSED, _now(),
                                               principal.user_id)
    db.commit()
    return {"id": g.id, "status": g.status}


# ============================================================ agent logs (ADMIN)

def _run_out(r: AiRun) -> dict:
    return {"id": r.id, "agent_id": r.agent_id, "agent_name": r.agent_name,
            "version": r.version, "trigger": r.trigger,
            "trigger_label": config.TRIGGERS.get(r.trigger, {}).get("label",
                                                                   "Try-it" if r.is_test
                                                                   else r.trigger),
            "contact_id": r.contact_id, "opportunity_id": r.opportunity_id,
            "appointment_id": r.appointment_id, "subject": r.subject_label,
            "mode": r.mode, "is_test": r.is_test, "outcome": r.outcome, "reason": r.reason,
            "provider": r.provider, "model": r.model,
            "tokens": {"input": r.input_tokens, "output": r.output_tokens,
                       "cache_write": r.cache_write_tokens, "cache_read": r.cache_read_tokens},
            "cost": pricing.dollars(r.cost_micros), "latency_ms": r.latency_ms,
            "created_at": _iso(r.created_at), "run_after": _iso(r.run_after),
            "started_at": _iso(r.started_at), "finished_at": _iso(r.finished_at)}


def _steps(db: Session, run_id: int) -> list[AiRunStep]:
    return list(db.scalars(select(AiRunStep).where(AiRunStep.run_id == run_id)
                           .order_by(AiRunStep.position, AiRunStep.id)).all())


def _step_out(s: AiRunStep) -> dict:
    return {"id": s.id, "position": s.position, "kind": s.kind, "text": s.text,
            "tool_name": s.tool_name, "tool_call_id": s.tool_call_id, "data": s.data,
            "action_status": s.action_status, "created_at": _iso(s.created_at)}


def _parse_day(value: str | None, what: str) -> datetime | None:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, "%s must be a date, YYYY-MM-DD" % what) from None
    return d if d.tzinfo else d.replace(tzinfo=actions.TZ)


@router.get("/runs")
def ai_list_runs(db: Session = Depends(get_db), agent_id: int | None = None,
                 outcome: str | None = None, since: str | None = None,
                 until: str | None = None, include_tests: bool = True,
                 contact_id: int | None = None, opportunity_id: int | None = None,
                 page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                 _: auth.Principal = ADMIN):
    stmt = select(AiRun)
    if agent_id is not None:
        stmt = stmt.where(AiRun.agent_id == agent_id)
    if outcome:
        stmt = stmt.where(AiRun.outcome == outcome)
    start, end = _parse_day(since, "since"), _parse_day(until, "until")
    if start:
        stmt = stmt.where(AiRun.created_at >= start.astimezone(UTC))
    if end:
        stmt = stmt.where(AiRun.created_at < (end + timedelta(days=1)).astimezone(UTC))
    if not include_tests:
        stmt = stmt.where(AiRun.is_test.is_(False))
    if contact_id is not None:
        stmt = stmt.where(AiRun.contact_id == contact_id)
    if opportunity_id is not None:
        stmt = stmt.where(AiRun.opportunity_id == opportunity_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(AiRun.created_at.desc(), AiRun.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [_run_out(r) for r in rows], "total": total, "page": page,
            "page_size": page_size, "pages": max(1, -(-total // page_size))}


@router.get("/runs/{run_id}")
def ai_get_run(run_id: int, db: Session = Depends(get_db), _: auth.Principal = ADMIN):
    r = db.get(AiRun, run_id)
    if r is None:
        raise HTTPException(404, "run not found")
    suggestions = db.scalars(select(AiSuggestion).where(AiSuggestion.run_id == r.id)).all()
    return {**_run_out(r), "steps": [_step_out(s) for s in _steps(db, r.id)],
            "actions": [_step_out(s) for s in _steps(db, r.id) if s.kind == "action"],
            "suggestions": [_suggestion_out(s) for s in suggestions]}


@router.get("/metrics")
def ai_metrics(days: int = Query(30, ge=1, le=366), agent_id: int | None = None,
               db: Session = Depends(get_db), _: auth.Principal = ADMIN):
    """Runs per day, outcomes, cost per day and per agent, average latency, top actions,
    open knowledge gaps. Test runs are left out. Days are America/New_York days."""
    since = _now() - timedelta(days=days)
    stmt = select(AiRun).where(AiRun.created_at >= since, AiRun.is_test.is_(False))
    if agent_id is not None:
        stmt = stmt.where(AiRun.agent_id == agent_id)
    runs = db.scalars(stmt).all()
    per_day: dict[str, dict] = {}
    outcomes: dict[str, int] = {}
    per_agent: dict[int, dict] = {}
    latencies = [r.latency_ms for r in runs if r.latency_ms is not None]
    for r in runs:
        day = engine.aware(r.created_at).astimezone(actions.TZ).strftime("%Y-%m-%d")
        # cost_micros stays None until a run with a KNOWN cost lands: unknown is "—",
        # never "$0.00".
        d = per_day.setdefault(day, {"day": day, "runs": 0, "cost_micros": None,
                                     "cost_unknown_runs": 0})
        d["runs"] += 1
        outcomes[r.outcome] = outcomes.get(r.outcome, 0) + 1
        a = per_agent.setdefault(r.agent_id, {"agent_id": r.agent_id, "agent_name": r.agent_name,
                                              "runs": 0, "cost_micros": None,
                                              "cost_unknown_runs": 0})
        a["runs"] += 1
        if r.cost_micros is not None:
            d["cost_micros"] = (d["cost_micros"] or 0) + r.cost_micros
            a["cost_micros"] = (a["cost_micros"] or 0) + r.cost_micros
        elif r.provider is not None:      # a model was called and its price is unknown
            d["cost_unknown_runs"] += 1
            a["cost_unknown_runs"] += 1
    run_ids = [r.id for r in runs]
    top: dict[str, dict] = {}
    if run_ids:
        for s in db.scalars(select(AiRunStep).where(AiRunStep.run_id.in_(run_ids),
                                                    AiRunStep.kind == "action")).all():
            t = top.setdefault(s.tool_name or "?", {"action": s.tool_name, "executed": 0,
                                                    "suggested": 0, "refused": 0})
            if s.action_status in t:
                t[s.action_status] += 1

    def money(rows):
        for row in rows:
            row["cost"] = pricing.dollars(row.pop("cost_micros"))
        return rows

    return {
        "days": days,
        "runs": len(runs),
        "per_day": money(sorted(per_day.values(), key=lambda x: x["day"])),
        "outcomes": outcomes,
        "per_agent": money(sorted(per_agent.values(), key=lambda x: -x["runs"])),
        "avg_latency_ms": int(sum(latencies) / len(latencies)) if latencies else None,
        "top_actions": sorted(top.values(), key=lambda x: -(x["executed"] + x["suggested"]
                                                           + x["refused"]))[:10],
        "open_knowledge_gaps": db.scalar(select(func.count(AiKnowledgeGap.id)).where(
            AiKnowledgeGap.status == AiKnowledgeGap.OPEN)) or 0,
        "total_cost": pricing.dollars(sum(r.cost_micros for r in known))
        if (known := [r for r in runs if r.cost_micros is not None]) else None,
        "cost_unknown_runs": sum(1 for r in runs if r.cost_micros is None
                                 and r.provider is not None),
    }


# ============================================================ suggestions (STAFF)

def _suggestion_out(s: AiSuggestion, names: dict | None = None) -> dict:
    return {"id": s.id, "run_id": s.run_id, "agent_id": s.agent_id,
            "agent_name": (names or {}).get(s.agent_id), "action": s.action,
            "label": actions.CATALOGUE[s.action].label if s.action in actions.CATALOGUE
            else s.action, "summary": s.summary, "args": s.args,
            "contact_id": s.contact_id, "opportunity_id": s.opportunity_id,
            "status": s.status, "result": s.result, "created_at": _iso(s.created_at),
            "decided_at": _iso(s.decided_at)}


def _suggestion_visible(db: Session, principal: auth.Principal, s: AiSuggestion) -> bool:
    if s.opportunity_id is not None:
        o = db.get(Opportunity, s.opportunity_id)
        if o is not None and not pipeline_access.can_see(db, principal, o.pipeline_id):
            return False
    return True


@router.get("/suggestions")
def ai_list_suggestions(db: Session = Depends(get_db), status: str = "pending",
                        contact_id: int | None = None, opportunity_id: int | None = None,
                        principal: auth.Principal = VIEW):
    stmt = select(AiSuggestion)
    if status != "all":
        stmt = stmt.where(AiSuggestion.status == status)
    if contact_id is not None:
        stmt = stmt.where(AiSuggestion.contact_id == contact_id)
    if opportunity_id is not None:
        stmt = stmt.where(AiSuggestion.opportunity_id == opportunity_id)
    rows = [s for s in db.scalars(stmt.order_by(AiSuggestion.created_at.desc(),
                                                AiSuggestion.id.desc()).limit(200)).all()
            if _suggestion_visible(db, principal, s)]
    names = {a.id: a.name for a in db.scalars(select(AiAgent))}
    contacts = {c.id: c.name for c in db.scalars(select(Contact).where(
        Contact.id.in_({s.contact_id for s in rows if s.contact_id})))}
    return [{**_suggestion_out(s, names), "contact_name": contacts.get(s.contact_id)}
            for s in rows]


def _decide(db: Session, principal: auth.Principal, suggestion_id: int, fn) -> dict:
    s = db.get(AiSuggestion, suggestion_id)
    if s is None or not _suggestion_visible(db, principal, s):
        raise HTTPException(404, "suggestion not found")
    try:
        s = fn(db, suggestion_id, principal.user_id)
    except engine.SuggestionError as e:
        raise HTTPException(e.status, e.sentence) from None
    names = {a.id: a.name for a in db.scalars(select(AiAgent))}
    return _suggestion_out(s, names)


@router.post("/suggestions/{suggestion_id}/approve")
def ai_approve_suggestion(suggestion_id: int, db: Session = Depends(get_db),
                          principal: auth.Principal = VIEW):
    """Carry it out — once — through the same path Auto-pilot uses."""
    return _decide(db, principal, suggestion_id, engine.approve)


@router.post("/suggestions/{suggestion_id}/dismiss")
def ai_dismiss_suggestion(suggestion_id: int, db: Session = Depends(get_db),
                          principal: auth.Principal = VIEW):
    return _decide(db, principal, suggestion_id, engine.dismiss)


# ============================================================ agent state on a customer

@router.post("/agents/{agent_id}/wake")
def ai_wake_agent(agent_id: int, body: RunIn, db: Session = Depends(get_db),
                  principal: auth.Principal = ADMIN):
    """Let an agent act again on a conversation a staff reply put it to sleep on."""
    a = _get_agent(db, agent_id)
    if body.contact_id is None:
        raise HTTPException(400, "choose the contact")
    state = engine.thread_state(db, a.id, body.contact_id)
    if state is None or state.asleep_at is None:
        return {"awake": True, "changed": False}
    state.asleep_at, state.asleep_reason, state.updated_at = None, None, _now()
    db.commit()
    return {"awake": True, "changed": True}


# ============================================================ the bell

@router.get("/alerts")
def ai_list_alerts(db: Session = Depends(get_db), principal: auth.Principal = VIEW):
    rows = db.scalars(select(AiAlert).where(AiAlert.user_id == principal.user_id)
                      .order_by(AiAlert.created_at.desc(), AiAlert.id.desc()).limit(50)).all()
    unread = db.scalar(select(func.count(AiAlert.id)).where(
        AiAlert.user_id == principal.user_id, AiAlert.read_at.is_(None))) or 0
    return {"unread": unread, "items": [
        {"id": x.id, "kind": x.kind, "title": x.title, "body": x.body, "urgent": x.urgent,
         "run_id": x.run_id, "agent_id": x.agent_id, "contact_id": x.contact_id,
         "opportunity_id": x.opportunity_id, "task_id": x.task_id,
         "created_at": _iso(x.created_at), "read": x.read_at is not None} for x in rows]}


@router.post("/alerts/{alert_id}/read")
def ai_read_alert(alert_id: int, db: Session = Depends(get_db),
                  principal: auth.Principal = VIEW):
    x = db.get(AiAlert, alert_id)
    if x is None or x.user_id != principal.user_id:
        raise HTTPException(404, "alert not found")
    if x.read_at is None:
        x.read_at = _now()
        db.commit()
    return {"id": x.id, "read": True}


@router.post("/alerts/read-all")
def ai_read_all_alerts(db: Session = Depends(get_db), principal: auth.Principal = VIEW):
    n = 0
    for x in db.scalars(select(AiAlert).where(AiAlert.user_id == principal.user_id,
                                              AiAlert.read_at.is_(None))).all():
        x.read_at = _now()
        n += 1
    db.commit()
    return {"marked": n}
