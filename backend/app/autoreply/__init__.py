"""The auto-reply (2026-10-19, owner-approved): a polite text to a customer whose text or call
to the Zuper Connect line (954-420-7373) sat unanswered for 5 minutes. It sends BY ITSELF — the
owner lifted "no automatic texts" and Zuper's read-only rule for this feature only (DECISIONS.md,
2026-10-19).

  rules.py     when to answer and when never to (PURE, tests/test_autoreply_rules.py)
  zconnect.py  every Zuper request it makes; its two writes only inside client.autoreply_write()
  context.py   what the reply is written from, with phones / emails / addresses removed
  writer.py    the text: the model's, if it passes the guards, else the fixed polite wording
  service.py   one pass: gates, decide, write, record, send, note
  api.py       /api/autoreply — Dispatch → Settings

Off three ways: the server gate ZUPER_AUTOREPLY_ENABLED (default false), the switch in
Dispatch → Settings (default off), and "Pause all AI agents". While "test customers only" is on
(the default) it answers only config.TEST_CUSTOMERS. Never more than the daily limit (30), never
two texts to one number within 24 hours, never a stretch that started before it was switched on.
Nothing else is notified: a private note on the customer's Zuper job says what was sent.
"""
