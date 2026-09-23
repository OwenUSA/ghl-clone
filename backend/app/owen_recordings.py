"""Playing a call the AI agent answered (2026-09-22).

The thread already plays mirrored Quo calls by proxying the bytes from owen-main
(`app/openphone.py`). An agent call is the same problem with a different source: the audio
is a file owen-main recorded itself, on its own disk, and OWEN's own playback is a
short-lived signed token for a signed-in OWEN user — useless to a URL written onto a CRM
event minutes earlier.

So this is the same shape as the OpenPhone proxy, deliberately: one route, authenticated as
this CRM authenticates everything else, fetching from owen-main with the link key and
answering Range requests so the player's seek bar works. Nothing is cached here — a cache
would keep playing audio after owen-main had gone away, and an operator would not learn
that the phone system was down from the one screen that would otherwise tell them.

The path is written onto the event by owen-main (`integrations/crm/events.CRM_RECORDING_PATH`),
so the two repositories agree on a string. Both ends pin it in a test.
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import assigned_access, auth, crmlink
from .byte_range import ranged_response
from .db import get_db
from .models import Conversation, ConversationEvent, NumberThreadEvent

router = APIRouter(prefix="/api/owen", tags=["owen"])

# owen-main builds this exact path into an agent call's `recording_url`. Changing it here
# without changing it there leaves a player pointing at nothing.
RECORDINGS_PATH = "/api/owen/recordings"


@router.get("/recordings/{call_id}")
def stream_agent_recording(call_id: str,
                           principal: auth.Principal = auth.ANY_USER,
                           range_header: str | None = Header(None, alias="Range"),
                           db: Session = Depends(get_db)) -> Response:
    """Play the recording of a call an AI agent answered.

    ANY_USER, matching the thread it is drawn on and the Quo player beside it: a technician
    who can read a conversation can hear the call on it.

    The failure modes stay distinguishable, because they need different things from
    whoever is listening:

      * **503** — the phone link is not configured here. Nothing is broken.
      * **404** — owen-main has no recording for that call. Ordinary, and permanent.
      * **409** — it exists but has not been fetched to owen-main's disk yet. The one
        worth trying again in a minute.
      * **502** — owen-main could not be reached, or could not read the file.
    """
    if assigned_access.restricted(principal):
        # "Only assigned data" (2026-09-15). Same rule as the Quo player: audio only for a
        # thread this reader could open. Answered as the "no recording" 404 rather than a
        # 403, so a call id cannot be probed for existence.
        url = "%s/%s" % (RECORDINGS_PATH, call_id)
        visible = assigned_access.contacts(
            select(ConversationEvent.id)
            .join(Conversation, Conversation.id == ConversationEvent.conversation_id)
            .where(ConversationEvent.recording_url == url),
            assigned_access.scope(db, principal), Conversation.contact_id)
        if db.scalar(visible.limit(1)) is None:
            # A number-only thread has no contact and so no assignment to check. A
            # restricted technician is not given a stranger's call audio on that basis:
            # the rule is "their own jobs", and an unadopted number is nobody's job.
            raise HTTPException(404, "there is no recording for that call")
    if not crmlink.configured():
        raise HTTPException(503, "the phone link is not configured")

    result = crmlink.fetch_owen_recording(call_id)
    if isinstance(result, tuple):
        audio, content_type = result
        return ranged_response(audio, content_type, range_header,
                               default_type="audio/wav")

    if result.status == 404:
        raise HTTPException(404, "there is no recording for that call")
    if result.status == 409:
        # Not an error anyone needs to act on: the recording pipeline runs on its own
        # schedule and the audio usually lands within a minute of the call ending.
        raise HTTPException(409, "the recording is not ready yet — try again shortly")
    raise HTTPException(502, result.reason or "the recording could not be fetched")


def event_has_recording(db: Session, call_id: str) -> bool:
    """Whether any thread event actually points at this call's audio.

    Not used by the route above — it asks owen-main, which is the system that knows — but
    kept beside it for the tests that assert the two repositories still agree on the path.
    """
    url = "%s/%s" % (RECORDINGS_PATH, call_id)
    if db.scalar(select(ConversationEvent.id)
                 .where(ConversationEvent.recording_url == url).limit(1)):
        return True
    return bool(db.scalar(select(NumberThreadEvent.id)
                          .where(NumberThreadEvent.recording_url == url).limit(1)))
