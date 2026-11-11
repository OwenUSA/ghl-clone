"""Follow-ups (2026-11-11): follow up a customer who did not answer, the way Hatch does - a
SEQUENCE of steps (text, call me, AI call) that runs per phone number until the customer texts
or calls back, somebody reaches them, or a person stops it. The Follow-ups page in the left menu.

Named `cadences` because `app/followup` is the older, separate automatic call follow-up (one
text, then Anisa). The two never run together: with both server gates set, neither sends.

    config.py    fixed rules, the registry entry's editable settings, the default sequences
    rules.py     what a call or text means, when a step is due, the send window (PURE,
                 tests/test_cadences_rules.py)
    zconnect.py  every Zuper request: Connect text reads; the send + private note inside
                 client.followups_write() only
    service.py   one pass (start, observe, run the due step) and a person's changes
    api.py       /api/followups - the page, the "Follow up" button, sequences (ADMIN)

Off unless FOLLOWUPS_ENABLED (owner's approval for automatic texts and AI calls from THIS
module only, 2026-11-11, DECISIONS.md). Each automatic action obeys its registry level and
starts at Shadow; while test-only is on (the default) only test customers are texted or called.
"""
