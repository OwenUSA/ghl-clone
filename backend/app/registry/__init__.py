"""The agent registry (2026-10-21, DECISIONS.md "the agent system"): every bot and automation in
one list, each with a scope of work, and the level each of its actions is allowed to run at.

    catalog.py   WHAT each entry is — purpose, covers, channels, never-list, actions (PURE)
    service.py   levels, caps and spend, failure alerts, status, the activity feed
    api.py       /api/ai/registry — the AI Agents page's Overview and Activity tabs

The ladder, per action: Never -> Shadow (works it out, records it, does nothing) -> Approve each
-> Automatic. Only an ADMIN changes a level, and giving more freedom needs a reason and the typed
word PROMOTE. A module asks `service.refusal(db, key, action)` before it acts; the registry
itself never sends, calls or writes to Zuper.

Wired so far: the auto-reply (texts: Never / Shadow / Automatic, its spend and the overall cap,
an alert at its first failure of the day) and the Dispatch assistant's Zuper changes (Never /
Approve each). Voice and text agents built in AI Agents keep their own switches; the registry
shows them as levels and never sets them.
"""
