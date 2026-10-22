"""Payments & collections (2026-10-21, agent #8): what each customer agreed to pay and when
(e.g. 50% before the job, 50% after), whether each part was paid, the proof - with a page, a
chat that updates it (every change can be undone), Dispatch items and the bell when a part is
due or overdue, suggestions from Zuper's invoices and from customers' screenshots that a person
confirms, and - off by default - payment reminders to customers.

    config.py   the settings edited in AI Agents -> Overview
    rules.py    splits, due dates, status, reminder spacing (PURE)
    ledger.py   every change, logged before/after, the latest undoable
    service.py  the background pass (PAYMENTS_ENABLED)
    vision.py   is this picture a payment? (never marks anything paid)
    chat.py     the chat's tools; amounts must be ones a person said or on file
    api.py      /api/payments

Nothing here writes to Zuper. Customer reminders need the registry level AND
PAYMENTS_TEXTS_ENABLED.
"""
