"""Agent #10, "Daily report & insights" (2026-10-22). Read this first.

    config.py   gate, tags, settings (AI Agents -> Overview -> Daily report & insights)
    tagger.py   the AI reads each call and text ONCE; every tag keeps the customer's exact words
    metrics.py  every KPI from one place, over day / week / month / quarter + filters
    service.py  the hourly pass: tag, raise "Customer issues" in Dispatch, keep a daily snapshot
    ask.py      "Ask for a KPI": the AI composes the fixed metrics, never writes a query
    excel.py    the report as an Excel workbook with native charts
    api.py      /api/insights - the Insights tab on Reporting (Dispatch's audience)

Off unless INSIGHTS_ENABLED (the reading); the report itself is computed on demand. Nothing is
sent to anyone and nothing is written to Zuper.
"""
