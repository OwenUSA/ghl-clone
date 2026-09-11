"""The CRM end of the OpenPhone mirror: one route, and it plays audio.

OpenPhone is a second phone system the company is migrating away from. owen-main
mirrors its calls and texts onto our conversation threads so each customer has ONE
timeline (see DECISIONS.md, 2026-09-11). Everything about that mirror lives on the
owen-main side — it reads OpenPhone and posts to `POST /api/events` like any other
telephony feed, and this repository needed no ingest code for it at all.

What it DID need is a way to play a mirrored call's recording, because the audio
lives in OpenPhone and the key that unlocks it is one we must never hold.

## The three hops, and what each one drops

    browser ──cookie──▶ CRM ──X-OWEN-Key──▶ owen-main ──OpenPhone key──▶ OpenPhone
                                                      ──no credential──▶ share.quo.com

A mirrored CALL event's `recording_url` is `/api/openphone/recordings/<call id>` —
a path on THIS server. So the browser's `<audio src>` resolves it same-origin and
sends the operator's httpOnly session cookie, which is the one credential it
legitimately has. An `<audio>` tag cannot send an Authorization header, which is
exactly why a CRM-relative path is the right shape and a direct link to owen-main
would not have worked.

This module then asks owen-main with the CRM-link machine key it already holds, and
owen-main asks OpenPhone with the OpenPhone key. **Neither the OpenPhone key nor the
share.quo.com media URL ever reaches the browser.** The media URL matters as well as
the key: it is forwardable and outlives the session, so streaming the bytes rather
than redirecting is what keeps a customer's call audio inside the trust boundary.

## The tradeoff, stated rather than discovered later

Nothing is copied into this database. **Cancelling the OpenPhone account breaks this
audio** — the transcript and the thread entry survive, because those ARE ours, but
the recording stops playing. The owner accepted that; it is recorded in DECISIONS.md
so nobody rediscovers it as a bug.

## Off unless configured

With `CRM_LINK_BASE_URL` or `CRM_LINK_API_KEY` unset, `crmlink.configured()` is
False and this answers 503 without making a request — the same default as every
other path into owen-main, and the default in tests and in a fresh checkout.

## It reads. It cannot send.

There is no route here that sends a message, places a call or answers one, and
there is no OpenPhone send path anywhere in this repository. The composer sends
over BulkVS and only BulkVS. See `ConversationsPage.tsx`, which says so on screen
in any thread that holds a mirrored event.
"""
import logging

from fastapi import APIRouter, HTTPException, Response

from . import auth, crmlink

log = logging.getLogger("openphone")

router = APIRouter(prefix="/api/openphone", tags=["openphone"])

# owen-main builds this exact path into every mirrored call's `recording_url`
# (`integrations/openphone/events.CRM_RECORDING_PATH`). Two repositories agreeing on
# a string is the kind of thing that rots silently, so both ends pin it in a test:
# `test_openphone_thread.py` here, `test_openphone_mirror.py` there.
RECORDINGS_PATH = "/api/openphone/recordings"


@router.get("/recordings/{call_id}")
def stream_recording(call_id: str,
                     _: auth.Principal = auth.ANY_USER) -> Response:
    """Play one mirrored OpenPhone call recording.

    ANY_USER, matching the rest of the thread: a TECH who can read a conversation
    can hear the call on it. Narrowing this to staff would mean a tech could see
    "Inbound OpenPhone call, 3m04s" and be unable to hear it, which is not a
    privacy boundary anyone asked for.

    The three failure modes stay distinguishable, because they need different
    things from the operator:

      * **503** — the phone link is not configured. Nothing is wrong; this
        deployment has not been told where owen-main is.
      * **404** — OpenPhone has no recording for that call. Ordinary for a missed
        call, and not worth a retry.
      * **502** — owen-main or OpenPhone could not be reached. Might work later.
    """
    if not crmlink.configured():
        raise HTTPException(503, "the phone link is not configured")

    result = crmlink.fetch_openphone_recording(call_id)
    if isinstance(result, tuple):
        audio, content_type = result
        # `private` because this is one customer's call audio: it must not sit in a
        # shared cache. The short max-age lets the player scrub without re-fetching
        # the whole file back through three hops.
        return Response(content=audio, media_type=content_type or "audio/mpeg",
                        headers={"Cache-Control": "private, max-age=300"})

    # A LinkResult: owen-main answered, or could not be reached.
    if result.status == 404:
        raise HTTPException(404, "there is no recording for that call")
    raise HTTPException(502, result.reason or "the recording could not be fetched")
