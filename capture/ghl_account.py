"""The GoHighLevel account under measurement.

The location id identifies a real, live GoHighLevel sub-account. It is a
credential-adjacent identifier -- anyone holding it can address that account's
URLs directly -- so it is deliberately NOT committed. Every capture script reads
it from here rather than hardcoding it, which previously put it in 26 files.

Set it before running anything under capture/:

    export GHL_LOCATION_ID=...        # see .env.example

The capture harness is read-only against the live account (DECISIONS.md); this
module only decides *which* account it reads.
"""
import os

LOC = os.environ.get("GHL_LOCATION_ID", "").strip()

if not LOC:
    raise SystemExit(
        "GHL_LOCATION_ID is not set.\n"
        "  It is the GoHighLevel location id of the account to measure, and is\n"
        "  deliberately not committed to the repository. Export it first:\n"
        "      export GHL_LOCATION_ID=...\n"
        "  See .env.example."
    )
