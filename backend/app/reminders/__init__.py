"""Appointment reminder texts from Zuper's job status (2026-10-08). Built switched OFF.

While Zuper's own texting waits for its 10DLC registration, the CRM texts a customer the day
before a visit (from 10 AM) and 4 hours before it — only when the job sits in a column that
means the visit is confirmed. Owen lifted the 2026-09-15 "no automatic texts" rule for these
two reminders ONLY (DECISIONS.md, 2026-10-08). When Zuper's workflows take over, switch it Off.

It reads `dispatch_jobs` — the copy of Zuper's jobs the Dispatch pass refreshes every 150 s —
so it sends no request to Zuper at all. Texts go through the same `get_transport()` as a staff
text (the CRM line, owen-main; STOP / opted-out numbers are refused there).

Read in this order:

  config.py    the server gate, the columns that count, the times, the default wording
  rules.py     PURE: a job + now -> the reminders due (key = job + visit start + kind)
  language.py  English / Spanish from what the customer wrote and said; saved per number
  service.py   one pass (the worker's Zuper thread, after the Dispatch pass): pick, render,
               record BEFORE sending, send once, never retry. `python -m app.reminders.service`
               is a dry run.
  api.py       /api/reminders — status and log (Dispatch audience), settings (ADMIN, typed
               "TURN ON"), a customer's language (Dispatch audience)

Off three ways: ZUPER_REMINDERS_ENABLED unset (the deployment's gate), mode "off" in Settings →
Automations (the default), or the Zuper sync / Dispatch reader off (no fresh data = no text).
"""
