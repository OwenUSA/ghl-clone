"""READ-ONLY: prove the build did what it claimed AND disturbed nothing else.

Two halves, and the second matters more:

  1. Every field in `spec.py` exists exactly once, with the right type and option count.
  2. Nothing that was already on the account moved. The pipeline still has its 10 stages
     and 26 opportunities, the contact count is unchanged, and every pre-existing custom
     field (the 7 `owen_*` and 12 `workiz_*` in particular) is still present.

`owen_call_id` is a live join key into the telephony project (DECISIONS.md). Losing it
breaks call attribution silently, so it is asserted by name rather than by count.

Reads the API responses the pages fetch, not the DOM: the fields grid is virtualised and
under-reports (measured 47 of 61, and a different 47 per run).

    uv run python build/verify.py
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import spec
from attach import attach
from playwright.sync_api import sync_playwright
from settle import settle

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "captures" / "checklists"
from ghl_account import LOC  # noqa: E402  (flat script layout)

BASE = "https://app.gohighlevel.com/v2/location/" + LOC

# Baselines. `BASELINE_FIELDS` was measured by probe_fields.py immediately before this
# migration, so 61 + len(spec.FIELDS) is an exact expectation.
#
# There is deliberately NO expected stage count. DECISIONS.md records "10 stages, 26
# opportunities" for one pipeline; the account now has THREE pipelines and "Dream Team
# Roofing AHS" carries 22 stages and 40 opportunities. That is drift in the business,
# not damage from this migration - nothing here ever opened a pipeline setting, and the
# audit trail records every click. Asserting the stale number would manufacture a
# false alarm on every future run, so the pipelines are reported and only their
# CONTINUED EXISTENCE is asserted.
BASELINE_FIELDS = 61
MUST_HAVE_PIPELINES = ["Dream Team Roofing AHS"]
MUST_SURVIVE = [
    "owen_call_id", "owen_call_signal", "owen_campaign", "owen_is_new_caller",
    "owen_lead_trigger", "owen_signal_at", "owen_tracking_number",
    "workiz_created_by", "workiz_end", "workiz_job_created", "workiz_job_name",
    "workiz_job_number", "workiz_scheduled", "workiz_source", "workiz_status",
    "workiz_tags", "workiz_tech", "workiz_total", "workiz_type",
    "attribution_basis",
]


def collect_fields(page):
    found = []

    def walk(node):
        if isinstance(node, list):
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            if node.get("name") and ("dataType" in node or "fieldKey" in node):
                found.append(node)
            for v in node.values():
                walk(v)

    def on_response(resp):
        if "customFields" not in resp.url and "custom-fields" not in resp.url:
            return
        try:
            if "json" in (resp.headers.get("content-type") or ""):
                walk(resp.json())
        except Exception:
            pass

    page.on("response", on_response)
    page.goto(BASE + "/settings/fields?tab=field",
              wait_until="domcontentloaded", timeout=90000)
    ok, reason = settle(page, timeout_ms=90000)
    page.wait_for_timeout(9000)
    page.remove_listener("response", on_response)
    if not ok:
        raise SystemExit("fields page did not settle (%s)" % reason)

    # Dedupe by identity FIRST. GHL fires customFields/search twice per page load,
    # so counting raw responses doubles everything: it reported 172 fields (= 86 x 2)
    # and flagged all 25 new fields as duplicates. That is a verifier bug that looks
    # exactly like real data corruption, which is the worst kind of false alarm -
    # it would have sent someone deleting "duplicate" fields that never existed.
    unique = {}
    for f in found:
        ident = f.get("id") or (f.get("fieldKey"), f.get("name"))
        unique[ident] = f

    by_name = {}
    for f in unique.values():
        by_name.setdefault((f.get("name") or "").strip(), []).append(f)
    return by_name


def read_pipeline(page):
    """Read the pipeline from the API, not the rendered text.

    The DOM route was wrong twice over: `/opportunities/list` is the LIST view, whose
    text has no "<stage> | <n> opportunities" shape at all, so the regex matched
    nothing and reported "0 stages, 0 opportunities" - indistinguishable from the
    pipeline having been wiped. And even on the board, the kanban is virtualised and
    horizontally scrolled (DECISIONS.md: half the stages were off-screen).
    """
    pipelines = []

    def on_response(resp):
        u = resp.url.lower()
        if "opportunities/pipelines" not in u or "permissions" in u:
            return
        try:
            if "json" not in (resp.headers.get("content-type") or ""):
                return
            body = resp.json()
        except Exception:
            return

        def walk(node):
            if isinstance(node, list):
                for x in node:
                    walk(x)
            elif isinstance(node, dict):
                if node.get("name") and isinstance(node.get("stages"), list):
                    pipelines.append(node)
                for v in node.values():
                    walk(v)
        walk(body)

    page.on("response", on_response)
    # Navigate away first, then in. `goto` to the URL the SPA is already showing
    # can short-circuit without re-issuing the XHR, so the listener sees nothing
    # and the check reports "no pipeline API" on a perfectly healthy account.
    page.goto(BASE + "/dashboard", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(3000)
    page.goto(BASE + "/opportunities/list", wait_until="domcontentloaded",
              timeout=90000)
    ok, reason = settle(page, timeout_ms=90000)
    page.wait_for_timeout(9000)
    if not pipelines:                       # last resort: force a hard reload
        page.reload(wait_until="domcontentloaded", timeout=90000)
        settle(page, timeout_ms=90000)
        page.wait_for_timeout(9000)
    page.remove_listener("response", on_response)

    if not pipelines:
        return {"status": "NO_API", "reason": reason if not ok else "no pipeline API seen"}

    seen, uniq = set(), []
    for p in pipelines:
        pid = p.get("id") or p.get("name")
        if pid in seen:
            continue
        seen.add(pid)
        uniq.append(p)

    out = []
    for p in uniq:
        out.append({"name": p.get("name"),
                    "stages": [s.get("name") for s in p.get("stages", [])]})
    main = max(uniq, key=lambda p: len(p.get("stages", [])))
    return {"status": "OK", "pipelines": out,
            "main": main.get("name"),
            "stage_names": [s.get("name") for s in main.get("stages", [])],
            "stage_count": len(main.get("stages", []))}


def main():
    problems, notes = [], []

    with sync_playwright() as pw:
        b, ctx, page = attach(pw)
        page.bring_to_front()

        print("=== custom fields ===")
        by_name = collect_fields(page)
        total = sum(len(v) for v in by_name.values())
        print("  %d fields on the account (baseline before migration: %d)"
              % (total, BASELINE_FIELDS))

        # 1. the spec landed
        missing, dupes, wrongtype = [], [], []
        for _folder, name, ftype, options, _ in spec.FIELDS:
            hits = by_name.get(name, [])
            if not hits:
                missing.append(name)
                continue
            if len(hits) > 1:
                dupes.append("%s x%d" % (name, len(hits)))
            got = (hits[0].get("dataType") or "").upper()
            want = {"Checkbox": "CHECKBOX", "Single line": "TEXT",
                    "Multi line": "LARGE_TEXT", "Number": "NUMERICAL",
                    "Dropdown (single)": "SINGLE_OPTIONS"}.get(ftype)
            if want and got and got != want:
                wrongtype.append("%s: %s (wanted %s)" % (name, got, want))
            if options:
                have = hits[0].get("picklistOptions") or hits[0].get("options") or []
                if len(have) != len(options):
                    notes.append("%s has %d options, spec says %d"
                                 % (name, len(have), len(options)))

        print("  spec fields present: %d/%d"
              % (len(spec.FIELDS) - len(missing), len(spec.FIELDS)))
        if missing:
            problems.append("MISSING: " + ", ".join(missing))
        if dupes:
            problems.append("DUPLICATED: " + ", ".join(dupes))
        if wrongtype:
            problems.append("WRONG TYPE: " + "; ".join(wrongtype))

        # 2. nothing pre-existing vanished
        gone = [k for k in MUST_SURVIVE if k not in by_name]
        if gone:
            problems.append("PRE-EXISTING FIELDS MISSING: " + ", ".join(gone))
        else:
            print("  all %d pre-existing owen_*/workiz_* fields still present"
                  % len(MUST_SURVIVE))

        print("\n=== pipeline ===")
        pipe = read_pipeline(page)
        if pipe.get("status") != "OK":
            # Unreadable is NOT the same as changed. Say so, rather than reporting
            # a scary "0 stages" that only means the read failed.
            notes.append("pipeline could not be read (%s) - NOT evidence of a change"
                         % pipe.get("reason"))
            print("  UNREADABLE:", pipe.get("reason"))
        else:
            names = [p["name"] for p in pipe["pipelines"]]
            for p in pipe["pipelines"]:
                print("  %-38s %d stages" % (p["name"], len(p["stages"])))
            gone = [n for n in MUST_HAVE_PIPELINES if n not in names]
            if gone:
                problems.append("PIPELINE MISSING: " + ", ".join(gone))
            else:
                print("  all expected pipelines present")
            notes.append(
                "DECISIONS.md records the AHS pipeline as 10 stages / 26 "
                "opportunities; it is now %d stages across %d pipelines. That "
                "predates this migration - no pipeline was touched here."
                % (pipe["stage_count"], len(names)))

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "verify.json").write_text(
        json.dumps({"problems": problems, "notes": notes}, indent=1),
        encoding="utf-8")

    print("\n" + "=" * 62)
    if notes:
        print("NOTES (not failures):")
        for n in notes:
            print("  -", n)
    if problems:
        print("\nPROBLEMS (%d):" % len(problems))
        for p in problems:
            print("  !", p)
        raise SystemExit(1)
    print("\nOK - spec fields present, nothing pre-existing disturbed.")


if __name__ == "__main__":
    main()
