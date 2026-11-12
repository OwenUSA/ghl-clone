"""The ONLY code that touches Frontdoor (contractor.frontdoorhome.com). Reads; never writes.

`session()` opens one headless Chromium with the route guard (`guard.Guard`) on the whole
context, reuses the saved login when it still works, logs in when it does not, and on the way
out raises `WriteRefused` if the page tried to write anything. A `Portal` reads three pages:

  * the Dispatches list (`/dispatches?startDate=&endDate=`, turned page by page),
  * one dispatch (`/dispatches/<id>`: status, items, plan dates, the Authorization panel, the
    history table),
  * the Invoices tabs (Ready To Invoice / Draft Invoices / Submitted Invoices).

The portal loads its data as protobuf from prod.apis.frontdoorhome.com; we read the rendered
page instead, by its `data-testid` attributes (measured 2026-11-12). EXTRACT runs inside the
browser and never returns a cell whose test id names the customer, a phone, an email or an
address — those stay in the page.

Playwright is imported here only, and only when a read starts, so a server with the switch off
never loads it. Tests never reach this module's browser: tests/frontdoor_guard.py refuses the
hosts and `LAUNCH` is replaced.
"""
from __future__ import annotations

import contextlib
import logging
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from . import config, parse
from .guard import ClickRefused, Guard, WriteRefused, safe_click

log = logging.getLogger("frontdoor")

STATE_FILE = "state.json"


class FrontdoorError(RuntimeError):
    """A read failed for a reason a person can act on. The message is a sentence."""


# Inside the browser: {suffix: text} for every [data-testid] under `root` whose test id does not
# name personal data, with the record id taken out of the test id. Item chips go to `items`.
EXTRACT = r"""(root) => {
  // Personal data and free-text notes never leave the page (history notes: HISTORY, parse.py).
  const PII = /customer(?!owes)|phone|address|email|member|street|citystatezip|contact|maps|note/i;
  const LEGACY = /legacyItemName/;
  const out = {cells: {}, items: [], href: null};
  for (const e of root.querySelectorAll('[data-testid]')) {
    const id = e.getAttribute('data-testid') || '';
    if (PII.test(id) && !LEGACY.test(id)) continue;
    const parts = id.replace(/\[\d+\]/g, '').split('__').filter(p => p && !/^\d+$/.test(p));
    const text = (e.innerText || '').trim().replace(/\s+/g, ' ');
    if (parts.includes('Item') && parts[parts.length - 1] !== 'Item') {
      out.items.push(text);
      continue;
    }
    const key = parts[parts.length - 1];
    if (key && !(key in out.cells)) out.cells[key] = text;
    if (/__DispatchID$/.test(id) && e.getAttribute('href')) out.href = e.getAttribute('href');
  }
  return out;
}"""

AUTHO_PANEL = r"""() => {
  const h = [...document.querySelectorAll('h5')].find(e => e.innerText.trim() === 'Authorization');
  if (!h) return null;
  let e = h;
  for (let i = 0; i < 4 && e.parentElement; i++) {
    e = e.parentElement;
    if (e.innerText.length > 120) break;
  }
  return e.innerText;
}"""

HISTORY = r"""() => {
  const t = document.querySelector('[data-testid$="__DispatchHistory__Desktop"]');
  if (!t) return [];
  return [...t.querySelectorAll('tr')].map(r => [...r.querySelectorAll('td')]
    .map(td => (td.innerText || '').trim().replace(/\s+/g, ' '))).filter(r => r.length);
}"""

ROWS = "table.table-desktop-root tbody tr"

# The portal finished loading: either FusionAuth's login form, or the top menu's "Dispatches".
LANDED = r"""() => location.host.startsWith('login.') ||
  [...document.querySelectorAll('a,div,span')].some(e => e.childElementCount === 0 &&
                                                         e.innerText === 'Dispatches')"""


def _launch(playwright):
    exe = config.chromium()
    return playwright.chromium.launch(headless=True, **({"executable_path": exe} if exe else {}))


LAUNCH = _launch        # tests replace it so no browser can start


class Portal:
    def __init__(self, page, guard: Guard) -> None:
        self.page = page
        self.guard = guard
        self._last = 0.0

    # ---------------------------------------------------------------- navigation
    def _pace(self) -> None:
        wait = config.PAGE_GAP_SECONDS - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def goto(self, path: str) -> None:
        self._pace()
        self.page.goto(config.BASE + path, wait_until="load", timeout=config.TIMEOUT_MS)

    def on_login_page(self) -> bool:
        return "login.frontdoorhome.com" in (self.page.url or "")

    def ensure_login(self) -> bool:
        """True when a fresh login was needed. Raises FrontdoorError when it fails."""
        self.goto("/home")
        page = self.page
        page.wait_for_function(LANDED, timeout=config.TIMEOUT_MS)
        if not self.on_login_page():
            return False
        creds = config.credentials()
        if creds is None:
            raise FrontdoorError(config.NO_LOGIN_SENTENCE)
        self.guard.logging_in = True
        try:
            # `from None` below: nothing of these steps (a call log naming the fields) may ride
            # along on the error.
            page.wait_for_selector("#loginId", timeout=config.TIMEOUT_MS)
            page.fill("#loginId", creds[0])
            page.fill("#password", creds[1])
            page.click("#login-btn")
            page.wait_for_url(lambda u: urlsplit(u).hostname == "contractor.frontdoorhome.com"
                              and urlsplit(u).path == "/home", timeout=config.TIMEOUT_MS)
            page.wait_for_function(LANDED, timeout=config.TIMEOUT_MS)
        except Exception:  # noqa: BLE001 — the sentence below is what a person needs
            raise FrontdoorError("Frontdoor did not accept the login (still on %s). Check the "
                                 "user and password, or whether AHS now asks for a code."
                                 % ("the login page" if self.on_login_page() else "a redirect")
                                 ) from None
        finally:
            self.guard.logging_in = False
        return True

    # ---------------------------------------------------------------- reads
    def dispatch_rows(self, since: date, max_pages: int = config.MAX_LIST_PAGES) -> list[dict]:
        start = int(datetime.combine(since, datetime.min.time()).timestamp())
        end = int((datetime.now() + timedelta(days=1)).timestamp())
        self.goto("/dispatches?startDate=%d&endDate=%d" % (start, end))
        page = self.page
        try:
            page.wait_for_selector(ROWS + " td", timeout=config.TIMEOUT_MS)
        except Exception:  # noqa: BLE001 — an empty list renders no row
            return []
        out: list[dict] = []
        for _ in range(max_pages):
            rows = [page.locator(ROWS).nth(i).evaluate(EXTRACT)
                    for i in range(page.locator(ROWS).count())]
            out += rows
            # On the last page the arrow is still in the page, hidden
            # (`pagination-arrow-button-hidden`); a second pager exists for phones.
            nxt = page.locator("button[aria-label='Next page']:visible").first
            if not rows or nxt.count() == 0 or nxt.is_disabled() or \
                    nxt.get_attribute("aria-disabled") == "true":
                break
            first = (rows[0].get("cells") or {}).get("DispatchID")
            self._pace()
            safe_click(nxt, "Next page")
            try:
                page.wait_for_function(
                    "(f) => { const a = document.querySelector('" + ROWS +
                    " a[data-testid$=\"__DispatchID\"]'); return a && a.innerText.trim() !== f; }",
                    arg=first or "", timeout=config.TIMEOUT_MS)
            except Exception:  # noqa: BLE001 — the last page does not change
                break
        return out

    def dispatch_detail(self, dispatch_id: str) -> dict:
        self.goto("/dispatches/" + dispatch_id)
        page = self.page
        try:
            page.wait_for_selector("[data-testid='DispatchDetails__DispatchStatusLabel']",
                                   timeout=config.TIMEOUT_MS)
        except Exception as exc:
            raise FrontdoorError("Frontdoor shows no dispatch %s to this account."
                                 % dispatch_id) from exc
        with contextlib.suppress(Exception):   # the Authorization panel loads on its own
            page.wait_for_function(
                "() => [...document.querySelectorAll('h5')].some(e => "
                "/^(Authorization|No Authorization Requested)$/.test(e.innerText.trim())) && "
                "(document.body.innerText.includes('Authorized To Contractor') || "
                "document.body.innerText.includes('No Authorization Requested'))",
                timeout=20_000)
        with contextlib.suppress(Exception):
            page.wait_for_selector("[data-testid$='__DispatchHistory__Desktop'] td",
                                   timeout=10_000)
        top = page.locator("body").evaluate(EXTRACT)
        return {"dispatch_id": dispatch_id, "cells": top.get("cells") or {},
                "items": top.get("items") or [], "autho_text": page.evaluate(AUTHO_PANEL),
                "history": page.evaluate(HISTORY)}

    def invoice_rows(self) -> dict[str, list[dict]]:
        self.goto("/invoice")
        page = self.page
        out: dict[str, list[dict]] = {}
        for key, label in parse.TABS.items():
            tab = page.get_by_text(label, exact=True).first
            with contextlib.suppress(Exception):
                tab.wait_for(timeout=config.TIMEOUT_MS)
            if key != "ready":
                self._pace()
                safe_click(tab, label)
            with contextlib.suppress(Exception):
                page.wait_for_selector(ROWS + " td", timeout=8_000)
            out[key] = [(page.locator(ROWS).nth(i).evaluate(EXTRACT).get("cells") or {})
                        for i in range(page.locator(ROWS).count())]
        return out


def _state_path() -> Path:
    d = config.state_dir()
    d.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(d, 0o700)
    return d / STATE_FILE


@contextlib.contextmanager
def session():
    """One guarded browser. Yields a logged-in Portal; saves the login; fails loudly on a
    refused write. Callers check `config.refusal()` first (service.py does)."""
    from playwright.sync_api import sync_playwright

    guard = Guard()
    state = _state_path()
    with sync_playwright() as p:
        browser = LAUNCH(p)
        try:
            ctx = browser.new_context(
                storage_state=str(state) if state.is_file() else None,
                timezone_id=config.TZ, viewport={"width": 1600, "height": 1000})
            ctx.route("**/*", guard.handle)
            page = ctx.new_page()
            page.set_default_timeout(config.TIMEOUT_MS)
            portal = Portal(page, guard)
            fresh = portal.ensure_login()
            if fresh:
                log.info("frontdoor: logged in again (saved login expired or absent)")
            guard.check()
            yield portal
            guard.check()
            ctx.storage_state(path=str(state))
            with contextlib.suppress(OSError):
                os.chmod(state, 0o600)
        finally:
            if guard.dropped:
                log.debug("frontdoor: dropped %d telemetry writes", guard.dropped)
            browser.close()


__all__ = ["ClickRefused", "FrontdoorError", "Portal", "WriteRefused", "session"]
