"""Frontdoor — READING AHS's contractor portal (contractor.frontdoorhome.com), 2026-11-12.

The after-inspection report, the Good / Better / Best proposal and Owen AI's answers want AHS's
own numbers (what AHS authorized to us, what the customer owes, the dispatch's status, the
invoice's status) instead of the office typing them. AHS offers contractors no API we know of,
so this drives a headless Chromium logged in as the company's portal user — and ONLY reads.

  * `config.py` — the switches. OFF unless FRONTDOOR_READ_ENABLED; with it off nothing here
    imports Playwright, opens a browser or makes a request.
  * `guard.py` — PURE. Every request the browser makes passes `Guard.handle`: GET / HEAD /
    OPTIONS go; the login form's POST (only while logging in) and the OAuth token exchange go;
    any other write to a frontdoorhome.com host is ABORTED before it leaves and the read then
    fails loudly (`WriteRefused`); a write to anyone else (Google Analytics, Clarity, Sentry —
    the portal's telemetry) is dropped and counted. Clicks go through `safe_click`, which
    refuses a control whose label reads like Submit / Accept / Update / Request / Create / …
  * `client.py` — the ONLY code that touches Frontdoor. One browser per read, the login saved
    (owner-only file) and reused until it expires, pages spaced out, and customer names /
    phones / addresses never read out of the page (the extraction skips those cells inside the
    browser).
  * `parse.py` — PURE: the cells the client extracted -> dicts (money as numbers, dates ISO).
  * `service.py` — what callers use: `dispatch(id)`, `dispatches(since)`, `invoices()`, cached
    15 minutes, one read at a time, refusing with a sentence when off or without a login.

The join key: the dispatch number = Zuper's "AHS Job ID" field = CRM custom_fields.ahs_job_id.
Submitting anything to AHS stays NEVER (registry). See DECISIONS.md, 2026-11-12.
"""
