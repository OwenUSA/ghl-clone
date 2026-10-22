"""Speak instead of type (2026-10-22): the mic button beside every AI chat box - the Dispatch
assistant, Collections, Reporting -> Insights' "Ask for a KPI", an agent's Try it.

    POST /api/speech/transcribe   multipart `file` (the browser's recording) -> {"text"}

The words go into the box for the person to read and send; nothing is sent by itself and the
recording is not kept. Transcribed by OpenAI (`providers.transcribe`, the same call the
auto-reply uses for Zuper's recordings): the connection chosen for transcription in Dispatch ->
Settings, else the first OpenAI connection - DeepSeek has no speech-to-text. English and Spanish
are detected by themselves. "Pause all AI agents" stops it like every other AI request. The
chats' audience: Dispatch's rule (ADMIN and unrestricted DISPATCHER).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import auth
from .ai import engine as ai_engine
from .ai import providers, vault
from .db import get_db
from .dispatch import ai as dai
from .dispatch.api import VIEW
from .models import AiConnection

router = APIRouter(prefix="/api/speech", tags=["speech"])
MAX_BYTES = 10 * 1024 * 1024                 # ~2 minutes of browser audio is well under this
KIND = "speech"

# What a browser's MediaRecorder produces, by its first bytes - the type header is not trusted.
MAGIC = ((b"\x1a\x45\xdf\xa3", "webm"), (b"OggS", "ogg"), (b"RIFF", "wav"), (b"ID3", "mp3"),
         (b"\xff\xfb", "mp3"), (b"\xff\xf3", "mp3"))


def sniff(data: bytes) -> str | None:
    for head, ext in MAGIC:
        if data.startswith(head):
            return ext
    if data[4:8] == b"ftyp":                  # Safari / iPhone: audio in an MP4 box
        return "mp4"
    return None


def connection(db: Session) -> AiConnection | None:
    chosen = dai.settings(db).autoreply_transcribe_connection_id
    conn = db.get(AiConnection, chosen) if chosen else None
    if conn is not None and conn.provider == "openai":
        return conn
    return db.scalar(select(AiConnection).where(AiConnection.provider == "openai")
                     .order_by(AiConnection.id).limit(1))


@router.post("/transcribe")
async def speech_transcribe(file: UploadFile = File(...), principal: auth.Principal = VIEW,
                            db: Session = Depends(get_db)):
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "That recording is too long - keep it under 2 minutes.")
    ext = sniff(data)
    if ext is None:
        raise HTTPException(422, "That is not a recording this can read.")
    if ai_engine.settings(db).paused:
        raise HTTPException(409, "All AI agents are paused (Settings -> AI Connections), so "
                                 "speaking is off too. Type instead.")
    conn = connection(db)
    if conn is None:
        raise HTTPException(409, "Speaking needs an OpenAI connection (Settings -> AI "
                                 "Connections). Type instead.")
    try:
        prov = providers.build(conn.provider, vault.decrypt(conn.api_key_encrypted),
                               conn.base_url)
        text = prov.transcribe(data, "speech." + ext)
    except vault.SecretsUnavailable as e:
        raise HTTPException(409, str(e)) from None
    except providers.ProviderError as e:
        dai._record(db, KIND, providers.TRANSCRIBE_MODEL, None, user_id=principal.user_id,
                    error=str(e)[:300])
        db.commit()
        raise HTTPException(502, "It could not be heard: %s" % e) from None
    dai._record(db, KIND, providers.TRANSCRIBE_MODEL, None, user_id=principal.user_id)
    db.commit()
    return {"text": " ".join((text or "").split())}
