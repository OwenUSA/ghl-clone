"""The inspection-documentation agent (2026-10-21, agent #4): after a technician finishes an
inspection or repair, it checks the job has where the leak is, tools & materials, time to
complete, photos and a video - reading the fields, checklist answers, notes and the technician's
replies - and flags what is missing (a Dispatch item and the bell), texts the technician, and
later can have the technician call agent ask for it.

    config.py   fixed rules and the settings edited in AI Agents -> Overview
    rules.py    what a visit needs and what is missing (PURE)
    reader.py   the evidence for one visit (reads only)
    extract.py  the AI that finds written details, every value with its quote
    service.py  one pass: open a check per finished visit, decide, flag, text, call

It writes NOTHING to Zuper. Off unless INSPECTION_DOCS_ENABLED; texts start at Shadow, the call
at Never, test jobs only.
"""
