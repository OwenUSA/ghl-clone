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
import re

from fastapi import APIRouter, Header, HTTPException, Response

from . import auth, crmlink

log = logging.getLogger("openphone")

router = APIRouter(prefix="/api/openphone", tags=["openphone"])

# owen-main builds this exact path into every mirrored call's `recording_url`
# (`integrations/openphone/events.CRM_RECORDING_PATH`). Two repositories agreeing on
# a string is the kind of thing that rots silently, so both ends pin it in a test:
# `test_openphone_thread.py` here, `test_openphone_mirror.py` there.
RECORDINGS_PATH = "/api/openphone/recordings"


# ---- seeking (2026-09-14) -------------------------------------------------------
#
# The thread's player has a seek bar, and Chrome will not move `currentTime` on a
# media response that does not honour HTTP Range: it restarts from 0. Measured in a
# real Chromium against a full-body 200 with no Accept-Ranges — what this route used
# to answer — and again against this route once it honoured Range. Since the bytes are already
# held in full (see `crmlink.fetch_openphone_recording`), answering a Range request
# is a slice, not a stream.
#
# A seek is a second request, and it travels CRM -> owen-main -> Quo again. Nothing is
# cached here on purpose: a cache would keep playing audio after owen-main or Quo went
# away, and `test_an_openphone_outage_leaves_the_thread_rendering` pins that it stops.
# The browser may still reuse what it has (`Cache-Control: private, max-age=300`).
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def audio_response(audio: bytes, content_type: str, range_header: str | None) -> Response:
    """200 with the whole file, or 206 with the one byte range asked for.

    `bytes=a-b`, `bytes=a-` and `bytes=-n` are honoured. A range past the end is
    416; a header this does not understand (several ranges, other units) gets the
    whole file, which is always a correct answer to a Range request.
    """
    total = len(audio)
    headers = {"Cache-Control": "private, max-age=300",
               "Accept-Ranges": "bytes"}
    media_type = content_type or "audio/mpeg"
    match = _RANGE.match((range_header or "").strip())
    if not match or match.groups() == ("", ""):
        return Response(content=audio, media_type=media_type, headers=headers)
    first, last = match.groups()
    if first == "":
        length = int(last)
        if length == 0:
            return Response(status_code=416, headers={**headers,
                            "Content-Range": "bytes */%d" % total})
        start, end = max(0, total - length), total - 1
    else:
        start = int(first)
        end = min(int(last), total - 1) if last else total - 1
    if start >= total or start > end:
        return Response(status_code=416, headers={**headers,
                        "Content-Range": "bytes */%d" % total})
    headers["Content-Range"] = "bytes %d-%d/%d" % (start, end, total)
    return Response(content=audio[start:end + 1], status_code=206, media_type=media_type,
                    headers=headers)


@router.get("/recordings/{call_id}")
def stream_recording(call_id: str,
                     _: auth.Principal = auth.ANY_USER,
                     range_header: str | None = Header(None, alias="Range")) -> Response:
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
        return audio_response(audio, content_type, range_header)

    # A LinkResult: owen-main answered, or could not be reached.
    if result.status == 404:
        raise HTTPException(404, "there is no recording for that call")
    raise HTTPException(502, result.reason or "the recording could not be fetched")
