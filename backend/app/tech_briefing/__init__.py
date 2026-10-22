"""The field technician briefing (2026-10-21, agent #7): at 6 PM the day before, one text per
technician with tomorrow's visits - when, where, what the inspection found, the time it takes and
what to bring (an AI suggestion only when nothing was recorded, and marked so).

    config.py   the settings edited in AI Agents -> Overview
    rules.py    when, grouping, the job lines, the text split (PURE)
    suggest.py  suggested tools & materials (DeepSeek, one call per briefing)
    service.py  one pass: brief each technician once a day

Reads the CRM's copy of Zuper only; texts only technicians. Off unless TECH_BRIEFING_ENABLED;
texts start at Shadow; test technicians only.
"""
