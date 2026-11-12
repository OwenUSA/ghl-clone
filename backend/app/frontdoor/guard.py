"""The route guard and the click guard. PURE: no Playwright import, so tests drive it with fakes.

Every request the portal's page makes is decided here BEFORE it leaves the browser:

  read     GET / HEAD / OPTIONS, any host                                       -> continue
  login    POST login.frontdoorhome.com/oauth2/authorize, ONLY while logging in -> continue
  token    POST login.frontdoorhome.com/oauth2/token (the page's OAuth code
           exchange and refresh; it carries no business data)                  -> continue
  refuse   any other non-read to a frontdoorhome.com host                       -> abort + FAIL
  drop     any other non-read to anyone else (analytics, Clarity, Sentry)       -> abort, count

A refused request makes the whole read fail (`Guard.check` raises `WriteRefused`): the portal
tried to change something, and nobody asked it to.
"""
from __future__ import annotations

import contextlib
import re
from urllib.parse import urlsplit

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LOGIN_HOST = "login.frontdoorhome.com"
LOGIN_POST = (LOGIN_HOST, "/oauth2/authorize")
TOKEN_POST = (LOGIN_HOST, "/oauth2/token")

# A control whose label or text matches this is never clicked, whatever the caller asked.
# ("Log in" is the one action allowed, and it is clicked by `client.login`, not through here.)
# Whole words, so the "Submitted Invoices" tab is readable while a "Submit" button is not.
FORBIDDEN_CLICK = re.compile(
    r"\b(submit|accept|decline|reject|approve|update|request|create|upload|send|save|edit|"
    r"delete|remove|dismiss|cancel|add|assign|schedule|reschedule|confirm|transfer|logout|"
    r"log out|sign out|note|notes|estimate|authorization|autho)\b",
    re.IGNORECASE)


class WriteRefused(RuntimeError):
    """The portal tried to write and the guard stopped it. The message names method, host, path."""


class ClickRefused(RuntimeError):
    pass


def frontdoor_host(host: str | None) -> bool:
    host = (host or "").lower().rstrip(".")
    return host == "frontdoorhome.com" or host.endswith(".frontdoorhome.com")


def decide(method: str, url: str, *, logging_in: bool = False) -> str:
    method = (method or "").upper()
    if method in READ_METHODS:
        return "read"
    u = urlsplit(url)
    where = ((u.hostname or "").lower(), u.path)
    if method == "POST" and where == LOGIN_POST and logging_in:
        return "login"
    if method == "POST" and where == TOKEN_POST:
        return "token"
    return "refuse" if frontdoor_host(u.hostname) else "drop"


class Guard:
    def __init__(self) -> None:
        self.logging_in = False
        self.refused: list[str] = []
        self.dropped = 0
        self.login_posts = 0

    def handle(self, route, request) -> None:
        verdict = decide(request.method, request.url, logging_in=self.logging_in)
        if verdict in ("read", "login", "token"):
            if verdict == "login":
                self.login_posts += 1
            route.continue_()
            return
        if verdict == "refuse":
            u = urlsplit(request.url)
            self.refused.append("%s %s%s" % (request.method, u.hostname, u.path))
        else:
            self.dropped += 1
        route.abort()

    def check(self) -> None:
        if self.refused:
            raise WriteRefused("The Frontdoor page tried to write and was stopped: %s"
                               % "; ".join(self.refused[:5]))


def safe_click(locator, label: str) -> None:
    """Click `locator` only if neither `label` nor its visible text reads like an action."""
    text = ""
    with contextlib.suppress(Exception):   # a control without text is judged by its label
        text = (locator.inner_text(timeout=5000) or "").strip()
    for words in (label, text):
        if FORBIDDEN_CLICK.search(words or ""):
            raise ClickRefused("refused to click %r on Frontdoor" % (words,))
    locator.click()
