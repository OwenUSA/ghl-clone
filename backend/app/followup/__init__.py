"""The call follow-up (2026-10-21): when the team calls a customer and nobody answers, a text a
couple of minutes later, then - if still no reply or call back - Anisa (AI) calls them.

    config.py    fixed rules and the settings an admin edits in AI Agents -> Overview
    rules.py     when to text, wait, call or stop (PURE, tests/test_followup_rules.py)
    zconnect.py  its two Zuper writes, inside client.followup_write() only
    service.py   one pass: gates, open a row per unanswered call, move each row a step

Off unless CALL_FOLLOWUP_ENABLED; then each action obeys its registry level (starts at Shadow,
test customers only). Anisa really dials only when owen-main also has AGENT_OUTBOUND_ENABLED.
"""
