# Orchestrated test-and-fix run

A four-step run against the live deployment, driven by `run.ps1`. One Claude Code session
per step, launched headless by the script; **you** invoke each step.

This file records the decisions, not just the mechanics. Same contract as `DECISIONS.md`:
do not re-litigate these without being asked.

## Why a script and not an agent

Every gate in this run is mechanically checkable — a file exists, `ruff` is clean, `pytest`
is green, a suite that was passing still passes. None of them needs a model to evaluate,
and a model in the control loop is how "one step at a time" quietly becomes "it decided to
skip step 2's gate". So the control flow is PowerShell, and the only judgement in the loop
is yours, at the triage gate.

## The worker

`claude -p "<prompt>" --output-format json --dangerously-skip-permissions`

- **Process exit is the completion signal.** This is the whole reason for headless.
  Cross-session `SendMessage` can deliver a prompt to a session that keeps its context, but
  the sender gets delivery confirmation only — never a *completion* event. The receiver has
  to volunteer a reply, and a worker that dies mid-step never does, so the orchestrator
  waits forever. Process exit cannot be forgotten.
- **Bypass permissions is deliberate**, and only safe because of the blast-radius rules
  below: the worker cannot deploy, cannot `ssh`, cannot push. `.claude/settings.json` still
  denies `app.seed`.
- **One session per step, not one throughout.** `--resume` would preserve context, but by
  the fix step that context holds a full app map plus three testers worth of Playwright
  output, and auto-compaction would summarise away the precise findings the fixes depend
  on. The artefact file is the handoff. A fresh session reading a structured findings file
  beats a compacted one remembering it. Session ids are still recorded under `state/`, so
  you can `claude --resume <id>` any step by hand and ask it what it did.

## The steps

| Step | What runs | Gate to advance |
|------|-----------|-----------------|
| `inventory` | 1 worker | `findings/inventory.md` exists, lists routes + interactive elements per surface |
| `test` | 3 workers, parallel | 3 x `findings/slice-N.md` exist; every entry classified |
| `triage` | **you** | you have read the merged list and deleted or reclassified the noise |
| `fix` | 1 worker | baseline suite still green; every finding `fixed`, `blocked` or `needs-prod-verify` |
| `verify` | **you**, then 3 workers | you reviewed and deployed the branch; testers confirm on production |

### inventory

The original plan opened with "check how this project works end to end", then "map out
everything deeply". Both would burn a large context rediscovering what is already written
down in `DECISIONS.md` (40KB), `AUDIT.md`, `design-tokens.md` (44KB) and
`NORMALIZATIONS.md` — and a fresh session mapping from source may *contradict* locked
decisions, which `CLAUDE.md` forbids.

So the worker reads those first, and its output is not prose: it is a **test-surface
inventory** — routes, interactive elements, expected behaviour — which becomes the input
contract for the testers. "Test everything" with no enumeration means three agents overlap
on the easy surface and all three miss the same corner.

### test

Three workers, disjoint slices, each with **its own production account** and **its own
Playwright storage-state file**:

| Slice | Surface | Account |
|-------|---------|---------|
| 1 | Contacts, Conversations | `qa-dispatcher@` (DISPATCHER) |
| 2 | Opportunities incl. drag-and-drop, Calendars | `qa-tech@` (TECH) |
| 3 | Auth/session, roles, Reporting, cross-cutting: scroll, empty states, responsive | `qa-admin@` (ADMIN) |

Three accounts rather than one shared admin, for three reasons: refresh-token rotation
across concurrent sessions as the same user produces phantom sign-outs that read as bugs;
the roles give real permission coverage the original plan never tested (a TECH can move a
stage but not edit a record); and agent activity stays attributable, separate from `owen@`.

**Target is production**, at your call, to avoid standing up local resources. What that
costs, and how it is contained:

- **Mutation is allowed, cleanup is mandatory.** Every record created or edited is recorded
  and restored or deleted at the end. A worker never touches a record it did not create.
- **No opportunity is ever deleted.** `Opportunity.custom_fields.owen_call_id` is a live
  join key to the telephony project.
- **No direct database access.** Everything through the UI or API, so the rules of the app
  apply.
- **A cleanup that fails is reported as a finding, never swallowed.** This is the
  `ui_check.py` lesson from `CLAUDE.md`: a silent restore failure once renamed three real
  contacts to "Kimberly".
- **The production database is empty of real data.** Empty states are *expected*, not bugs.
  Measured at scaffold time: `/api/health` reports `LoggingTransport`, so a message send
  writes a row and transmits nothing.

Every finding is self-classified `bug` / `environment` / `uncertain`. `environment` is for
the false positives this design guarantees: empty-state screens, concurrent-session
sign-outs, cleanup failures.

### triage — the human gate

The merged list goes to you before any code changes. `environment` entries never reach the
fix worker. This is the cheapest place in the run to prevent damage: ten minutes reading a
list, against an unattended bypass-mode worker rewriting working code to satisfy an
imaginary bug.

### fix

- **Branch only.** No `ssh owen-main`, no `deploy.sh`, no push to `main`. `deploy.sh`
  already refuses a dirty tree and will not migrate unless asked — that is a guard for a
  human operator, not a licence for an unattended agent.
- **Two attempts per finding**, then `blocked` with the reason, and move on.
- **Hard stop** if a suite that was green in `state/baseline.json` goes red and the worker
  cannot recover it. That is the one failure mode where continuing does real damage: a bad
  fix breaking a passing baseline, then five more fixes stacked on top of it.
- **A fix needing an Alembic revision is flagged, not applied.** It cannot be verified
  against production without a migration, and migrations do not run on container start.

### verify — why the fix step cannot verify itself

Findings come from production. Fixes land on a branch that is never deployed. So "make sure
they work as expected" cannot mean "re-check on production" — the fix is not there. The
worker verifies locally; then **you** review the branch and deploy it, and the testers
re-run against production to close out anything marked `needs-prod-verify`.

Accept the consequence knowingly: some production findings **will not reproduce locally** —
Traefik, cookie domain, build output, HTTPS-only behaviour. Those are exactly the ones the
local suite cannot verify, and they must be marked `needs-prod-verify`, never quietly
closed.

## Credentials

Three production accounts exist and their logins are verified (`POST /api/auth/login` → 200
for all three). The script reads them from an env file **outside the repo** and exports them
into the worker environment. They are never interpolated into prompt text, because prompt
text is transcript text.

```powershell
.\orchestrate\run.ps1 test -CredsFile "$env:TEMP\...\qa-creds.txt"
```

`orchestrate/.gitignore` blocks `*creds*` as a second line of defence. If that file is ever
committed, rotate all three with `app.bootstrap set-password`.

## Amendments from the first run (2026-09-09)

Four things the design got wrong, corrected here rather than rediscovered later.

**Slices run one at a time, not three at once.** Three workers each driving a Chromium
exhausted this machine and the OS killed the parent. The workers themselves survived the
kill, kept writing, and were then orphaned with no supervisor. Coverage is unaffected --
the slices are disjoint by design -- so this costs wall-clock only. Use `-Slice N` per
slice. Only revisit parallelism on a machine with the memory for it.

**A killed run leaves production dirty.** The workers cleaned up on exit; being killed
skips that. Slice 1 left 11 contacts behind (including its adversarial-input payloads),
removed afterwards by hand. Any interrupted run must be followed by an audit of
production, not an assumption.

**The production database is NOT empty.** `CLAUDE.md` says it is; it holds 12 contacts, 10
opportunities, 6 calls, 8 appointments and 1 pipeline. Empty-state findings are therefore
rarer than expected, and the "never touch what you did not create" rules are load-bearing
rather than precautionary.

**A worker destroyed a live credential.** The `callmon` machine token (id 1,
`events:write`, owner `owen@telephony.local`) was permanently revoked by a probe that
assumed the call would be refused. A replacement was minted; no ingest was actually
interrupted (`/api/events` has never been called in the retained logs, and the telephony
project holds no `ghl_pat_` value in its configuration), but the credential itself was
unrecoverable. `prompts/step2-test-slice.md` now forbids touching any token the worker did
not mint, and forbids probing a destructive call against a real record to see whether it
is refused. The general lesson is stronger than the specific rule: **an unattended worker
with ADMIN rights will find the endpoints the UI cannot reach.** Prefer the least role
that can exercise a surface.

## Gates that may not be available here

`capture/diff.py` (37/37) and `capture/diff_panel.py` (35/35) need `references/`, which is
gitignored and machine-local. `run.ps1 baseline` probes them: if they cannot run on this
machine they are recorded as `skipped` and are not treated as a regression later. They are
never silently assumed to pass.

## Usage

```powershell
.\orchestrate\run.ps1 baseline                      # record what is green before anything runs
.\orchestrate\run.ps1 inventory
.\orchestrate\run.ps1 test      -CredsFile <path>   # 3 workers in parallel
.\orchestrate\run.ps1 merge                         # collate slices into findings/merged.md
.\orchestrate\run.ps1 fix                           # after you have triaged merged.md
.\orchestrate\run.ps1 verify    -CredsFile <path>   # after you have deployed the branch
.\orchestrate\run.ps1 status                        # what exists, what is green
```

Each step is independently re-runnable. `test` accepts `-Slice 2` to re-run one slice, which
you will want the first time one of the three half-fails.
