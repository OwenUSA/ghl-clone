"""Dispatch (2026-09-30): what the office has to do next, from Zuper and every call.

The owner's design, grilled 2026-09-30 (DECISIONS.md): fixed rules find the work, and the page
shows it as queues — new jobs nobody called, inspections and repairs just finished, missed
calls and texts, visits to book, Zuper not updated after a conversation, today's visits, jobs
gone quiet. A few moments ring the bell. Phase 1 READS Zuper and never writes to it.

Read in this order:

  config.py   the boards, the stage groups and the owner's time limits (business hours)
  reader.py   the reads: Zuper's job list, changed jobs and their notes, Zuper Connect calls
  comms.py    every call and text by number: Quo and the CRM line (already here) + Zuper's
  rules.py    PURE: jobs + calls + now -> items and bells
  alerts.py   the bell: once per event, the Dispatch audience, pipeline permissions honoured
  service.py  one pass (the worker runs it; `python -m app.dispatch.service` by hand)
  api.py      /api/dispatch — ADMIN + unrestricted DISPATCHER, like AI Agents
"""
