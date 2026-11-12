"""What callers use: dispatch(id), dispatches(since), invoices(). Cached, serialised, refusing.

  * Off (FRONTDOOR_READ_ENABLED) or no login -> `FrontdoorOff` with the sentence, BEFORE anything
    imports Playwright or opens a browser. Nothing is cached for a refusal.
  * One read at a time (`_LOCK`): two Owen AI tool calls never run two browsers.
  * An answer is kept CACHE_SECONDS (15 min) per key; `fresh=True` skips it.
  * Answers carry ids, statuses, dates and money only — never a customer's name, phone or
    address (client.EXTRACT leaves them in the page).

`main()` is the operator's check: `python -m app.frontdoor.service dispatch 99913749`.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import date, datetime, timedelta

from . import client, config, parse

log = logging.getLogger("frontdoor")

_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, dict]] = {}


class FrontdoorOff(RuntimeError):
    pass


def refusal() -> str | None:
    return config.refusal()


def _read(key: str, fn, fresh: bool = False) -> dict:
    why = config.refusal()
    if why:
        raise FrontdoorOff(why)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and not fresh and time.monotonic() - hit[0] < config.CACHE_SECONDS:
            return {**hit[1], "cached": True}
        started = time.monotonic()
        with client.session() as portal:
            value = fn(portal)
        value = {**value, "read_at": datetime.now(parse.NY).isoformat(timespec="seconds")}
        _CACHE[key] = (time.monotonic(), value)
        log.info("frontdoor: %s read in %.1fs", key.split(":")[0], time.monotonic() - started)
        return {**value, "cached": False}


KNOWN = (FrontdoorOff, ValueError, client.FrontdoorError, client.WriteRefused,
         client.ClickRefused)


def sentence(exc: BaseException) -> str:
    """A failure as a sentence safe to show and to store: ours verbatim; anything else (a
    Playwright timeout, whose call log names selectors and pages) by its class only."""
    if isinstance(exc, KNOWN):
        return str(exc)
    return "the Frontdoor page did not load as expected (%s)" % type(exc).__name__


def clear_cache() -> None:
    _CACHE.clear()


def dispatch_id(value) -> str | None:
    """'99913749', '#99913749', 'AHS 99913749' -> '99913749'; anything else -> None."""
    m = re.fullmatch(r"\s*(?:ahs\s*)?#?\s*(\d{6,10})\s*", str(value or ""), re.IGNORECASE)
    return m.group(1) if m else None


def dispatch(value, fresh: bool = False) -> dict:
    did = dispatch_id(value)
    if not did:
        raise ValueError("A Frontdoor dispatch number is 6 to 10 digits (the job's AHS Job ID).")
    return _read("dispatch:" + did,
                 lambda p: parse.dispatch_detail(p.dispatch_detail(did)), fresh)


def dispatches(since: date | None = None, fresh: bool = False) -> dict:
    since = since or (date.today() - timedelta(days=30))

    def read(p):
        rows = [r for r in (parse.dispatch_row(x) for x in p.dispatch_rows(since)) if r]
        seen, out = set(), []
        for r in rows:
            if r["dispatch_id"] not in seen:
                seen.add(r["dispatch_id"])
                out.append(r)
        return {"since": since.isoformat(), "count": len(out), "dispatches": out}
    return _read("dispatches:" + since.isoformat(), read, fresh)


def invoices(since: date | None = None, fresh: bool = False) -> dict:
    """Every row of the three Invoices tabs; `since` drops rows dated (created or submitted)
    before it."""
    def read(p):
        tabs = p.invoice_rows()
        rows = [r for tab, cells in tabs.items() for r in (parse.invoice_row(tab, c)
                                                            for c in cells) if r]
        return {"invoices": rows}
    out = _read("invoices", read, fresh)
    if since:
        keep = [r for r in out["invoices"]
                if (r.get("submitted") or r.get("created") or "9999") >= since.isoformat()]
        out = {**out, "since": since.isoformat(), "invoices": keep}
    counts: dict[str, int] = {}
    for r in out["invoices"]:
        counts[r["tab"]] = counts.get(r["tab"], 0) + 1
    return {**out, "count_by_tab": counts}


def authorization_for(ahs_job_id) -> dict | None:
    """The report's question: what did AHS authorize on this dispatch? None when reading is
    off or the dispatch shows no authorization; raises on a failed read."""
    if config.refusal() or not dispatch_id(ahs_job_id):
        return None
    d = dispatch(ahs_job_id)
    a = d.get("authorization") or {}
    if not a.get("requested"):
        return None
    return {**a, "dispatch_id": d["dispatch_id"], "autho_numbers": d.get("autho_numbers") or [],
            "status": d.get("status")}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(prog="python -m app.frontdoor.service",
                                 description="Read Frontdoor (read-only). Prints ids, statuses "
                                             "and money; no customer data.")
    ap.add_argument("what", choices=("dispatch", "dispatches", "invoices", "check"))
    ap.add_argument("id", nargs="?")
    ap.add_argument("--since", help="YYYY-MM-DD")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    since = date.fromisoformat(a.since) if a.since else None
    try:
        if a.what == "check":
            why = config.refusal()
            print(why or "Frontdoor reading is on and has a login.")
            return 1 if why else 0
        out = (dispatch(a.id) if a.what == "dispatch" else
               dispatches(since) if a.what == "dispatches" else invoices(since))
    except Exception as exc:  # noqa: BLE001 — one sentence, never a traceback with a page in it
        print("ERROR: %s" % sentence(exc))
        return 1
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
