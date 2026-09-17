"""Two-way sync between this CRM and Zuper (2026-09-16). Built switched OFF.

The owner's split: the CRM is the hub for AI agents, automations, texting, calls, technicians
and scheduling; Zuper owns quotes, invoices, payments and reports. Contacts <-> customers,
opportunities <-> jobs (pipelines -> the "AHS" / "Retail" categories, stages -> statuses),
visits <-> appointments, deal notes <-> job notes, deal tasks <-> service tasks; quotes and
invoices come back read-only. See DECISIONS.md (2026-09-16, Zuper) for every rule.

Read in this order:

  config.py     the three switches (env flag, Settings switch + setup check, API key)
  client.py     the ONLY code that sends a request to Zuper: denylist, allowlist, pacing,
                429 backoff, dry-run refusal
  zapi.py       one function per Zuper endpoint (shapes UNVERIFIED — one place to fix)
  mapping.py    what a record looks like to the sync on each side; the owner's lists
  engine.py     merge by field ownership, push, pull, documents, deletes, snapshots, restore
  listener.py   CRM changes -> queued jobs (after commit); delete snapshots (before delete)
  webhooks.py   raw deliveries stored first, processed idempotently
  sweep.py      every 15 minutes: Zuper changes since the cursor, and CRM content hashes
  digest.py     the daily activity note per customer (counts only, never a message)
  worker.py     the worker's Zuper thread
  setup.py      `python -m app.zuper.setup`: the checklist verified, categories + statuses
  load.py       `python -m app.zuper.load`: the initial load, dry run by default
  api.py        Settings → Zuper, the money panels, Zuper photos, the webhook route

Deliberately light: `app/db.py` imports `listener` from every process.
"""
