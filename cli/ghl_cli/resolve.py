"""Turn a human reference into an id.

Every command takes names where a person would use one: `--contact "jane doe"`,
`--stage "Inspection"`. An agent should not have to look up an id first, and it
should never have to guess when a name is ambiguous.

The rule is the same everywhere:

    all digits          -> that id, used as-is
    one exact match     -> use it (case-insensitive)
    one partial match   -> use it
    no match            -> exit 4, with the closest names as a hint
    several matches     -> exit 5, listing every candidate WITH its id

Never silently pick the first of several. The measured pipeline contains two
distinct stages both named "Call Back" (DECISIONS.md), so this is a real case,
not a hypothetical one.
"""
from .errors import Ambiguous, NotFound


def _match(rows: list[dict], ref: str, key: str) -> list[dict]:
    needle = ref.strip().lower()
    exact = [r for r in rows if str(r.get(key, "")).lower() == needle]
    if exact:
        return exact
    return [r for r in rows if needle in str(r.get(key, "")).lower()]


def _describe(rows: list[dict], key: str, extra: str | None = None) -> list[str]:
    out = []
    for r in rows:
        line = "%s  (id %s)" % (r.get(key), r.get("id"))
        if extra and r.get(extra) is not None:
            line += "  %s: %s" % (extra, r.get(extra))
        out.append(line)
    return out


def pick(rows: list[dict], ref: str, *, kind: str, key: str = "name",
         extra: str | None = None) -> dict:
    """Resolve `ref` against `rows`, or raise with an actionable message."""
    if ref is None:
        raise NotFound("a %s is required" % kind)
    ref = str(ref).strip()
    if ref.isdigit():
        wanted = int(ref)
        for r in rows:
            if r.get("id") == wanted:
                return r
        raise NotFound("no %s with id %s" % (kind, ref))

    hits = _match(rows, ref, key)
    if not hits:
        names = [str(r.get(key)) for r in rows][:10]
        raise NotFound(
            "no %s matching %r" % (kind, ref),
            {"available": names})
    if len(hits) > 1:
        raise Ambiguous(
            "%r matches %d %ss — narrow it, or pass the id" % (ref, len(hits), kind),
            {"candidates": _describe(hits, key, extra)})
    return hits[0]


# ---- per-resource helpers -------------------------------------------------

def contact(client, ref: str) -> dict:
    """Contacts are searched server-side; the grid can hold hundreds."""
    if str(ref).strip().isdigit():
        return client.get("/api/contacts/%d" % int(ref))
    page = client.get("/api/contacts", params={"q": ref, "page_size": 50})
    return pick(page["items"], ref, kind="contact", extra="phone")


def pipeline(client, ref: str | None) -> dict:
    rows = client.get("/api/pipelines")
    if ref is None:
        if len(rows) == 1:
            return rows[0]
        raise Ambiguous(
            "several pipelines exist — pass --pipeline",
            {"candidates": _describe(rows, "name")})
    return pick(rows, ref, kind="pipeline")


def stage(client, pipeline_row: dict, ref: str) -> dict:
    return pick(pipeline_row["stages"], ref, kind="stage", extra="position")


def user(client, ref: str) -> dict:
    return pick(client.get("/api/users"), ref, kind="user", extra="role")


def calendar(client, ref: str) -> dict:
    return pick(client.get("/api/calendars"), ref, kind="calendar")


def conversation(client, ref: str) -> dict:
    """Accept a conversation id, a contact id, or a contact name."""
    rows = client.get("/api/conversations")
    if str(ref).strip().isdigit():
        wanted = int(ref)
        for r in rows:
            if r["id"] == wanted:
                return r
        for r in rows:               # fall back to treating it as a contact id
            if r["contact_id"] == wanted:
                return r
        raise NotFound("no conversation with id %s" % ref)
    return pick(rows, ref, kind="conversation", key="contact_name",
                extra="contact_phone")
