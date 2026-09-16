"""One HTTP Range implementation, shared by everything this server hands a browser.

Extracted from `app/openphone.py` on 2026-09-16, unchanged, when picture messages needed
the same behaviour. It was written for the thread's call player, and the measurement behind
it is in that module: Chrome will not move `currentTime` on a media response that does not
honour `Range` — it restarts from 0. The bytes are always already held in full (a recording
relayed from owen-main, a picture read off disk), so answering a Range request is a slice
rather than a stream.

It is here rather than imported out of `openphone` so that a second caller does not make
the OpenPhone mirror a dependency of unrelated media. There is exactly one of these in the
product, and this is it.
"""
import re

from fastapi import Response

_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def ranged_response(audio: bytes, content_type: str, range_header: str | None, *,
                    default_type: str = "application/octet-stream",
                    extra_headers: dict | None = None) -> Response:
    """200 with the whole file, or 206 with the one byte range asked for.

    `bytes=a-b`, `bytes=a-` and `bytes=-n` are honoured. A range past the end is
    416; a header this does not understand (several ranges, other units) gets the
    whole file, which is always a correct answer to a Range request.
    """
    total = len(audio)
    headers = {"Cache-Control": "private, max-age=300",
               "Accept-Ranges": "bytes", **(extra_headers or {})}
    media_type = content_type or default_type
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
