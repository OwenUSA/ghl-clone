"""The post-inspection report (2026-10-21, agent #6): when an inspection's pictures have been quiet
for 30 minutes, a DRAFT report - the pictures a vision model picked, with captions, a summary and
the findings in plain words - for the office to review, edit and approve in Dispatch -> Reports.

    config.py   fixed rules and the settings edited in AI Agents -> Overview
    rules.py    when to build, and what of the model's answer is kept (PURE)
    photos.py   the visit's pictures: listed, downloaded, JPEG, kept in the media store
    vision.py   the one vision call (OpenAI or Anthropic)
    pdf.py      the PDF, built on demand
    service.py  one pass: open a report per quiet inspection, build it, raise the item
    api.py      /api/inspection-reports

It reads Zuper only and sends NOTHING to anyone. Off unless INSPECTION_REPORT_ENABLED; test
jobs only until test-only is switched off; needs a vision connection in its settings.
"""
