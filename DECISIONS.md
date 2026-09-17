# GHL Clone — Locked Decisions

> Read first each session. Do not re-litigate without explicit request.

## Purpose

Replace a live GoHighLevel subscription for **one roofing company** (Dream Team Roofing,
Bradenton FL). Single-tenant. Not a SaaS, not an agency product.

## Scope — v1

**In:** Contacts · Conversations · Opportunities · Calendars

**Out:** all marketing (funnels, sites, email campaigns, reputation, memberships),
Payments, AI Agents, Reporting, App Marketplace, and the entire agency layer
(sub-accounts, snapshots, rebilling, SaaS mode).

**Workflows:** no visual builder in v1. ~4 hard-coded automations in Python
(missed-call → text back, new lead → notify, appointment reminders, status → customer text).
Build the canvas in v2 only if these need weekly editing.

**SMS + email: UI only.** Both sit behind one seam:

```python
class MessageTransport(Protocol):
    def send_sms(self, to: str, body: str, from_number: str) -> MessageRef: ...
    def send_email(self, to: str, subject: str, html: str) -> MessageRef: ...
```

`LoggingTransport` is the only implementation in v1. A separate project supplies
real phone/SMS later as a second implementation — no UI changes.

## Telephony integration — LOCKED (option A)

**One database, one app.** The telephony project writes calls, recordings and transcripts
directly into this app's schema; Conversations reads them natively. No cross-service API
call per thread.

Driver: measured — Conversations already interleaves inbound **call records with audio
playback** alongside messages. The thread view is a timestamp-ordered join across calls and
messages, so a network round-trip per thread would be the wrong shape for a 4-user company.

Consequence: the schema must model a unified `conversation` timeline from day one —
messages and calls are sibling event types on a thread, not separate features bolted together.
The `MessageTransport` seam still governs *outbound side effects* only; inbound call and
message records are plain data we own and display.

### AMENDED at deployment (2026-09-04): same server, not the same database

The literal form of option A turned out to be impossible, and the amendment was forced by
what is actually running on `owen-main`, not by preference.

The telephony project deploys there as **`callmon`** (containers `callmon_app`,
`callmon_worker`, `callmon_frontend`, `owen_voice`, plus Asterisk natively on the host),
and it owns a database called `callmon` — **not** `dtr_ghl_clone`. That database already
contains its own `calls`, `messages`, `jobs`, `users` and, fatally, its own
`alembic_version`. Two Alembic histories cannot share one version table, so merging our
schema into it is not a migration problem to be solved carefully; it is not possible.

**What we do instead:** the CRM owns `dtr_ghl_clone` on the *same* PostgreSQL 18 server,
and telephony feeds it through the already-built, already-tested `POST /api/events`
ingest using a machine account scoped to `events:write`.

**The driver above still holds, which is why this is an amendment and not a reversal.**
Call records still land in our own tables, so a Conversations thread is still a local
timestamp-ordered join with no per-thread network round-trip. What changed is only *how*
the rows arrive: an ingest endpoint rather than a foreign writer in our schema.
`owen_call_id` remains the join key.

**Consequence to be aware of:** the host Postgres is now a shared dependency of three
production systems (callmon, craigslist, and this). Our uptime is coupled to that
instance. `pg_hba.conf` carries a `ghl_clone` line for `172.16.0.0/12`, matching the
existing per-role entries; ufw already restricts 5432 to the docker subnets.

## The telephony project ALREADY writes into GHL — measured

Opening one opportunity on the live account (`captures/opportunities/detail_modal.json`)
revealed four existing custom fields, labelled "(from OWEN)":

| Field | GHL's own description |
|---|---|
| `owen_campaign` | "Campaign that produced this lead (from OWEN)" |
| `owen_tracking_number` | "Tracking number dialled (from OWEN)" |
| `owen_call_id` | **"OWEN call id (join key)"** |
| `owen_is_new_caller` | "First time this caller reached this campaign" |

This is not a plan — it is a live integration contract that already exists. Consequences:

- **`owen_call_id` is the join key** between a call record and an opportunity. Any migration
  or dual-run must preserve it, or attribution history breaks.
- It independently confirms the locked telephony decision (one shared database): the two
  systems are already coupled through these fields, just via GHL as the middleman today.
- `Opportunity.custom_fields` is JSONB and seeded with these four keys so the contract is
  represented in our schema rather than rediscovered later.
- Opportunity tags measured on the live record: `ahs-job`, `dispatch-service:roof` —
  evidence of an existing dispatch integration too. Not yet modelled.

## Design system — LOCKED

**Generate from measured usage, not from declared tokens.** Measured: GHL's most-used text
colour is `rgb(96,113,121)` (1,902 uses across all four views) and it is **not among the 479
declared custom properties**. GHL ships an Untitled-UI palette and renders something else on
top of it. A config generated from the declared tokens would be subtly wrong everywhere and
would still pass a token-level review.

**Normalisation policy:** match GHL exactly on anything structural. Collapse only
*near-duplicates* — within 24 RGB euclidean or 1.5px — plus explicit, named overrides.
Every deviation is logged in `NORMALIZATIONS.md`. Currently 36 normalisations, of which two
are deliberate defect fixes:

- `rgb(51,51,51)` (161 uses, Contacts only) — Tabulator's own default text colour leaking
  through. The grid is being rebuilt on TanStack Table, so it has no reason to exist.
- `29px` radius (20 uses, Contacts only) — matches nothing else in the system.

**A frequency threshold alone is wrong and was rejected after it did damage.** The first
version mapped the contact-avatar pastels (each used once, because every avatar gets its own
hue) onto grey, destroying a deliberate palette. Low frequency does not mean incidental.
Translucent colours are never normalised either — alpha carries meaning that an RGB-only
match discards.

## Responsive behaviour — LOCKED (option C)

**Desktop (1440×900) matches GHL 1:1.** That is the fidelity bar and the only breakpoint
we capture from GHL.

**Mobile is our own design.** Measured: at 390×844 GHL has no mobile layout — the sidebar
collapses to icons but the content pane overflows horizontally and the table is unusable.
GHL's real answer to mobile is a separate native app. Copying that is copying a defect.

Our mobile target is a card list, not a squeezed table, built for the actual field use case
(look up a contact → call or text them). Design is ours; no GHL reference exists or is wanted.
GHL mobile captures are therefore **not** collected.

## Stack — LOCKED

| Layer | Choice |
|---|---|
| Backend | **FastAPI** + **SQLAlchemy 2.0** (typed) + **Alembic** |
| Database | **PostgreSQL 18** (`dtr_ghl_clone`) — own database on the shared server, JSONB for custom fields |
| Python tooling | **uv** + repo-root `.venv` (3.12). Never `pip`, never the global interpreter. |
| API types | **openapi-typescript** generated from FastAPI's OpenAPI schema |
| Frontend | **React 19** + **Vite** + **TypeScript** |
| Server state | **TanStack Query** (polling ~30s) |
| Client state | **Zustand** |
| Routing | **TanStack Router** |
| Styling | **Tailwind + Radix primitives**, no component library |
| Design tokens | `tailwind.config.ts` generated **mechanically from the 451 captured CSS custom properties** — never hand-typed |
| Table | **TanStack Table + virtualizer** (must match Tabulator behaviour) |
| Kanban | **dnd-kit** |
| Forms | **react-hook-form + zod** |
| Auth | **JWT in httpOnly cookie**, 15m access + 7d refresh |
| Roles | Hard-coded enum: `ADMIN`, `DISPATCHER`, `TECH` |
| Realtime | **Polling**, no WebSockets/SSE in v1 |
| Jobs | **Postgres-backed queue** (`FOR UPDATE SKIP LOCKED`), no Redis |
| Lint | **Ruff** (Python) + **Biome** (TypeScript) |
| Testing | **pytest + httpx** integration; a few Playwright smokes |
| Deploy | Docker Compose on `owen-main`, behind the **Traefik** already running there |

Revisit if proven wrong by use: **polling vs SSE** for the Conversations inbox — 30s staleness
is most likely to feel wrong on that one screen.

## Reference account

- Location: the live GoHighLevel sub-account. Its id is **not committed** — set
  `GHL_LOCATION_ID` in the environment (see `.env.example`); `capture/ghl_account.py`
  is the single place that reads it.
- Populated: **268 contacts**, 14 pages @ 20/page
- Pipeline **"Dream Team Roofing AHS"** — **10 stages, 26 opportunities**. Full measured list
  and the horizontal-scroll fix are under "Known measurement gaps"; an earlier note here
  claiming 5 stages / 25 opportunities was the incomplete first capture.
  Note the AHS (home-warranty) shape and the stage-name typo `Approved- Repair`.
  Opportunity titles follow `<CLAIM# or NAME> - <status>`.
- Conversations carries **inbound call records with recordings + playback**, not just SMS.
  This matters: the Conversations clone is not an SMS-only inbox.
- Captures from a different location are NOT comparable — location ID is in every filename.

## Measurement harness

- Real Chrome, **fresh profile** at `.capture-profile/`, `--remote-debugging-port=9222`.
  Not a clone of the user's profile (live-profile copies are corrupt-prone, session cookies
  don't persist, and Chrome 127+ app-bound cookie encryption makes it flakier).
  The user's normal Chrome is untouched.
- Playwright attaches over CDP (`capture/attach.py`). Never launches its own browser.
- `capture/extract.js` is the **single shared extraction module** — must measure both GHL
  and our clone. Never fork it.
- It records `placeholder` as well as text/aria. Placeholders are visible text to a user but
  are not `innerText`, so a text-only extractor reports "element missing" for every input
  hint. Adding this immediately surfaced a real colour mismatch that had been invisible.
- Viewport changes go through CDP `Emulation.setDeviceMetricsOverride`; `set_viewport_size`
  is a no-op on a CDP-attached real browser.
- Python is the repo-root `.venv` (3.12), managed by **uv**. See the tooling section below.

## Safety contract (binding)

Read-only on the live GHL account: navigate, scroll, hover, focus, open dropdowns/menus/
modals/tabs, resize. **Never** Save/Create/Add/Update/Delete/Archive/Merge/Assign/Import/
Export/Duplicate, never send SMS/email/test-send, never place a call, never run or test a
workflow, never touch anything billed (AI features, number purchase, A2P, marketplace,
wallet), never change users/permissions/custom fields/pipelines/calendars, never sign out.
Unsure whether a control mutates → don't click, record the label.

Note: the contract forbids **Export**, which is also how contacts leave GHL. Data migration
will need the user to run that export themselves, or an explicit one-time lift.

## Measured findings about GHL (verified this session)

- **GHL v2 is Vue + Module Federation micro-frontends** (`launchpadApp`, `contentAIApp`,
  `copilotApp`, power-dialer). Not React. No components can be lifted — everything is
  rebuilt from measured values.
- **Contacts grid is Tabulator** (`.tabulator-tableholder`). Virtualized; node count stays
  constant across scroll. Columns: Contact name, Phone, Email, Business name, Created (EDT), …
  Pagination is Prev/Next with a page-size select (20), "Page 1 of 14".
- **The document does not scroll.** `docHeight == viewport height`; an inner pane scrolls.
  `window.scrollTo()` is a no-op — must scroll the detected pane.
- Table header is **sticky** (stays at y=260.2 at scrollTop 328.8).
- **451 CSS custom properties** on `:root` (Untitled-UI-style palette, `--primary-500 #2970ff`).
  Captured to `theme` in every capture so structure can be diffed separately from theme.
- **GHL rate-limits (429)** under rapid sequential navigation, and the content region then
  hangs on a spinner forever. Capture one view per invocation with gaps. Never loop all views.
- **Opportunities is a virtualised kanban that keeps permanent skeleton placeholders**
  (`crm-opportunities-card-skeleton`) for off-screen cards, and every loaded GHL component
  carries a 14–16px `hr-base-loading` placeholder. Neither means "still loading".
  Both produced false capture aborts before the rule was corrected to:
  *blocked only if (loading text) or (nodes < 500) or (large spinner AND textlen < 600)*.
- A text-only "is it loading" check is **not sufficient** — the content spinner has no text.
  `capture/settle.py` checks loading text + visible spinner + DOM stability, and the driver
  **aborts** rather than record a loading state.

## Known measurement gaps

- ~~Opportunities capture incomplete~~ **FIXED.** The kanban measured `scrollWidth` 2160 vs
  `clientWidth` 1168 — nearly half the board was off-screen. `capture/recapture_opps.py` now
  sweeps horizontally. **10 stages, not 5**, and the counts reconcile to the header (26 = 26):

  | # | Stage | Count | Value |
  |---|---|---|---|
  | 0 | New Lead | 7 | $2,575.00 |
  | 1 | Inspection | 1 | $0.00 |
  | 2 | Request the Approval (AHS) | 16 | $23,211.98 |
  | 3 | Approved- Repair Schedule | 0 | $0.00 |
  | 4 | Repair in Process | 0 | $0.00 |
  | 5 | Submit The Invoice | 0 | $0.00 |
  | 6 | Call Back | 0 | $0.00 |
  | 7 | Call Back | 2 | $2,100.00 |
  | 8 | AHS Upgrades | 0 | $0.00 |
  | 9 | Submit Invoices | 0 | $0.00 |

  Two distinct stages are both named **"Call Back"** — preserved as measured, not deduplicated.

  > **Superseded as a description of OUR schema (2026-09-10).** Stages are now
  > user-editable, so this table is a historical measurement of GHL and not a
  > guarantee about our own stage list. See "Pipelines and stages are now
  > USER-EDITABLE" at the end of this file.

  Two traps hit while fixing this, both now handled in the tooling:
  1. The collector matched a stage's header container *and* its inner line, double-counting
     to 78. Only `"<name> | <n> opportunities | $<value>"` shaped entries are real headers.
  2. Column order was first derived from viewport `x` recorded at *different* scroll offsets,
     which is not comparable. Absolute position = `x + scrollLeft`. Correcting it genuinely
     changed the order, so the first ordering was wrong.

- **The account is live and moving.** Between two captures ~40 minutes apart the pipeline went
  25 -> 26 opportunities and New Lead 6/$2,475.00 -> 7/$2,575.00. Any capture is a snapshot;
  two captures of the same view are not expected to be identical.

- **Conversations menus: CAPTURED** (`captures/conversations/menus.json`). Still uncaptured
  everywhere else: Contacts / Opportunities / Calendars dropdowns, all ⋯ menus, modals,
  drawers, and every hover/focus state. Nothing should be claimed about those.

### Menu capture safety

GHL's thread header packs these at a **40px pitch**:
`Filter messages | Call: +1786… | Add to Favorites | Mark as read | Delete Conversation`.
A coordinate-based click that drifts one slot **places a real phone call or deletes a
conversation**. So `capture/open_menus.py`:
- resolves elements by exact label, never by coordinate
- re-checks the resolved element's label against a denylist at click time
- closes with Escape only
- records unreachable menus as `NOT_FOUND`, never as empty

Measured menu contents:
- **Sort conversations:** Latest/Oldest - All Messages, Latest/Oldest - Manual Messages,
  Longest SLA Overdue, Next SLA Target. (This account has no SLA configured — "SLA has not
  been set. Go to Settings to configure." — so SLA sorts have nothing measured behind them
  and currently fall back to recency.)
- **Filter messages:** All, Conversations, Activities, then SMS, Call, WhatsApp,
  Internal Comment, Contacts, Appointments, Opportunities, Payments, Invoice,
  AI Action Logs, SLA, WhatsApp Permission.
- **Filter conversations:** a filter builder — Filter Type / Is / Value, AND/OR, Cancel/Apply.

**Schema consequence:** that filter menu proves the thread is a **mixed activity timeline**,
not a message log — appointments, opportunity moves and invoices render inline beside SMS and
calls. `EventType` was extended accordingly, with `CONVERSATION_TYPES` / `ACTIVITY_TYPES`
mirroring GHL's own split. Types not implemented in v1 (WhatsApp, AI Action Logs, SLA) appear
in the filter UI **disabled rather than omitted**, so the gap stays visible.

## Current state

Running locally:
- backend `uvicorn app.main:app --port 8000` (SQLite dev DB, seeded)
- frontend `npm run dev` on `:5173`

**All four in-scope views built.**

| View | Built | Verified |
|---|---|---|
| Contacts | list, search, pagination, sticky header, inner-pane scroll | **37/37 landmark properties match GHL, 0 differ** (`capture/diff.py`) |
| Conversations | 3-pane, Unread/All/Recent/Starred, Sort + Filter menus, mixed timeline, call playback, stubbed composer | filters verified against API incl. 400 on unknown filter |
| Opportunities | 10-stage kanban, drag between stages, Board/List toggle, status filter, search, Manage fields, ⋯ menu | drag persists (New Lead 7→6, Inspection 1→2); cross-pipeline move rejected |
| Calendars | Day/Week/Month, Today/prev/next, week grid + current-time line, Manage view panel, Appointment list view | user filter returns only that user's appointments |
| Contact Details panel | inline-edit fields (save on blur), Owner select, add/remove tags, All fields/DND/Actions tabs, field search | **35/35 landmark properties match GHL, 0 differ** (`capture/diff_panel.py`) |

| Opportunity detail | two-column form, all measured fields, stage/status/owner/value edit, custom fields, Cancel/Update | empty name, negative value and unknown status all rejected; `exclude_unset` leaves other fields alone |

**Deliberate deviation:** in GHL, clicking a card **navigates** to `/opportunities/<id>` —
it is a route, not an overlay, and **Escape does not dismiss it** (verified: only browser-back
or Cancel closes it). We render a dialog, because v1 has no router. Revisit when TanStack
Router lands.

The contact panel is **one component used in two places** — Conversations' third pane and a
Contacts row click — rather than two implementations that would drift.
Measured details reproduced deliberately: an empty field renders **`--`**, not blank,
and the tab row uses a **36px** gap.

Restarting the backend must free the port via PowerShell — `pkill` silently does nothing on
Windows and leaves **stale code serving**, which nearly produced a false verification.

### Automations — BUILT

Four hard-coded rules (`app/automations.py`), a Postgres-backed queue (`app/queue.py`,
`SELECT ... FOR UPDATE SKIP LOCKED`), and a single-replica worker (`app/worker.py`).
**158 tests pass**; verified end-to-end against the live Postgres API.

| Rule | Trigger | Verified |
|---|---|---|
| 1. Missed call → auto text back | `POST /api/events` inbound CALL ≤15s | fires; a 120s call returns "call was answered" |
| 2. New lead → notify team | `POST /api/contacts` | fires |
| 3. Appointment booked → T-24h and T-1h reminders | `POST /api/appointments` | both scheduled at the right future times |
| 4. Stage change → text customer | drag OR detail form | fires; same-stage returns "stage unchanged" |

Design rules that are enforced and tested, not just intended:

- **Every outbound send goes through `MessageTransport`.** Drained jobs record
  `LOGGED_ONLY` — the rule fires, the intent is recorded, nothing is transmitted.
- **DND and missing-phone suppress all customer-facing sends.** Internal team
  notifications are exempt: DND is a promise to the customer, not a mute on our own staff.
- **Idempotency via `jobs.dedupe_key`** (unique). Saving an appointment twice cannot
  produce two reminder texts.
- **Reminders already in the past are never scheduled** — back-dating would fire
  immediately, which is worse than not sending.
- **Cancelled appointments are re-checked at run time**, not only at enqueue time.
- **Permanent errors fail fast.** An unknown job type cannot succeed on retry, so it
  fails immediately instead of burning 5 attempts and delaying discovery. Transient
  errors retry with exponential backoff (1m, 2m, 4m…).

`POST /api/events` is the ingest endpoint for the telephony project. Inbound calls and
messages are plain data we own — they do **not** go through the transport seam, which
governs outbound side effects only.

Not built: contact full-page view, opportunity tags, Followers.

## Auth — BUILT

Every `/api` route requires a credential. Exempt: `/api/health`, `/api/auth/login`,
`/api/auth/refresh`, `/api/auth/logout`.

| Piece | Choice |
|---|---|
| Browser session | JWT in httpOnly cookie, **15m access + 7d refresh** — as locked |
| CSRF | double-submit token, on cookie-authenticated writes only |
| CLI / machines | `Authorization: Bearer ghl_pat_…`, stored sha256, revocable |
| Enforcement | one app-level dependency, fail-closed |
| Roles | the locked `ADMIN` / `DISPATCHER` / `TECH` enum, plus token scopes |

**Deviations from the locked stack, and why:**

- **Password hashing is stdlib `hashlib.scrypt`, not bcrypt/argon2.** Both of those are
  C extensions, and "The SSL interception" below records that native wheels have broken
  on this host. scrypt is memory-hard and already in the standard library. `maxmem` must
  be passed explicitly — the default caps at 32 MB and n=2¹⁵ raises "memory limit
  exceeded". Measured cost ≈700 ms per hash on this machine.
- **Token scopes were added alongside the 3-role enum**, rather than a fourth role. The
  telephony feed needs a machine identity that can only `POST /api/events`; that is a
  narrower statement than any role, and the enum stays locked. A scoped token can never
  exceed its owner's role.
- **`GET /api/users` stays readable by all signed-in users, with email ADMIN-only.**
  Locking the whole endpoint to ADMIN would break the owner dropdowns on the contact
  panel and opportunity form for a dispatcher. The exposure being closed was
  *unauthenticated* access to the staff roster.
- **There is deliberately no `AUTH_DISABLED` escape hatch.** Tests and the capture
  harness authenticate for real, so the app can never be served wide open by a stray
  environment variable.

**Verified, and load-bearing for the design:** app-level `dependencies=[...]` do **not**
run for `/docs`, `/redoc` or `/openapi.json`. FastAPI registers those through Starlette's
`add_route`, producing plain `Route` objects with no dependant. They are therefore open
by design; pass `docs_url=None` if that ever becomes unacceptable. Pinned by
`test_docs_are_open_because_app_dependencies_do_not_cover_them`.

**Bootstrap.** Users seeded before auth existed have `password_hash = ""`, which never
verifies. `app/bootstrap.py` sets the first password, creates machine accounts
(`--machine`, no password, token-only), and mints scoped tokens.

## The `ghl` CLI — BUILT

`cli/ghl_cli/`, Typer, installed via `[project.scripts]`. Built so Claude Code can drive
the whole application over Bash: `--json` anywhere, name→id resolution, an ambiguity
error that lists candidates rather than guessing, distinct exit codes, and a `--yes`
guard on anything customer-facing or destructive. See `CLAUDE.md`.

Note the root `pyproject.toml` now has a `[build-system]`, so `uv run` editable-installs
the project. Only `cli/ghl_cli` is packaged — `backend/app` still resolves through
pytest's `pythonpath`, unchanged.

## Alembic — DONE (was the biggest gap in the data layer)

Baselined at `bd30c8bb208a`. Autogenerating against the live database produced an *empty*
migration, which proved `models.py` and `dtr_ghl_clone` had zero drift; the CREATE TABLEs
were then generated against a throwaway database and the live one was `alembic stamp`ed,
so no DDL touched the seeded data.

`Base.metadata.create_all()` no longer owns the Postgres schema — it is guarded by
`AUTO_CREATE_ALL`, defaulting on only for SQLite (tests, bootstrap). Postgres now has one
source of truth. Run `uv run alembic upgrade head`.

## Postgres — DONE

**PostgreSQL 18**, native on this host (service `postgresql-x64-18`, port 5432). Docker is
installed but its daemon is not running and is not needed.

- Database: **`dtr_ghl_clone`** — uniquely named; this server already hosts 20+ unrelated
  databases, so a generic name would have been a collision risk.
- Driver: `psycopg` 3.3.4
- Default URL (override with `DATABASE_URL`):
  `postgresql+psycopg://postgres:postgres@127.0.0.1:5432/dtr_ghl_clone`
- `custom_fields` verified as **`jsonb`**, via
  `JSON().with_variant(JSONB, "postgresql")` so the SQLite bootstrap still works.
- 10 tables created, seeded, and serving. Contacts re-verified after the switch:
  **37/37 landmark properties still match GHL.** The old SQLite file is deleted.

## Python tooling — uv + .venv (LOCKED)

**Use `uv`. Do not use `pip`. Do not install into the global interpreter.**

- `pyproject.toml` at the repo root is the single dependency manifest, with
  `uv.lock` committed for reproducibility.
- `.venv/` at the repo root (gitignored), Python **3.12**, covers backend *and* the
  capture harness — one environment, no split.
- Dependency groups: `capture` (playwright), `dev` (pytest, httpx, ruff).

```bash
uv sync --all-groups                       # create/refresh .venv
uv run python -m app.seed                  # from backend/
uv run uvicorn app.main:app --port 8000    # from backend/
uv run python capture/diff.py              # from repo root
```

### The SSL interception

This machine MITMs TLS (self-signed cert in chain), which broke `pip install psycopg` with
`CERTIFICATE_VERIFY_FAILED`. **`native-tls = true` under `[tool.uv]` solves it properly** —
uv then uses the Windows certificate store, which already trusts the intercepting root, so
verification stays ON.

Never "fix" this with `--trusted-host` or `--insecure`; that disables verification instead of
satisfying it. An earlier `pip --cert <exported-bundle>` workaround was removed as obsolete.

## Open questions

- Remaining stack details (DB, migrations, state management, styling, auth, deploy).
- Data migration path off GHL (blocked by the Export clause above).


## Calendars filter groups — LOCKED to GHL

Measured by expanding GHL's own filter panel: exactly two groups, **Users** and
**Calendars**. The calendars are per-user / per-resource
(`Luis Candialies's Personal Calendar`, `Workiz Jobs (imported)`) — GHL has **no
pipeline filter** on Calendars.

A Pipelines filter group was briefly added on request, then **removed**: parity with
GHL wins over extra features. `Calendar.pipeline_id` remains in the schema (nullable,
unused) and the `pipeline_ids` query param still works, but nothing surfaces it in the
UI. Re-adding it is a deliberate decision, not a default.

## Responsive rules — measured across widths

`capture/capture_sizes.py` at 1440 / 1920 / 2560:

| Pane | 1440 | 1920 | 2560 | Rule |
|---|---|---|---|---|
| Sidebar | 224 | 224 | 224 | fixed |
| Icon rail | 52 | 52 | 52 | fixed |
| Inbox list | 319 | 379 | 379 | grows then caps -> `clamp(319px, 22vw, 379px)` |
| Thread | 480 | 826 | 1299 | fluid remainder |
| Contact panel | 299 | 373 | 540 | `clamp(299px, 21vw, 540px)` |

The first build used fixed 276/299px panes, which is wrong on anything wider than
1440. Never hard-code a pane width again — measure it at three widths first.

## Reference capture

`capture/deep.py` writes, for BOTH sides and every width:
`references/<side>/<view>/<bp>.{html,png,json}` plus `styles.css`, and a flip viewer at
`references/compare/<view>__<bp>.html`. Screenshots alone invite approximation;
computed styles alone cannot see icons. Both are needed.


## Reporting — SCOPE CHANGE (was "skip in v1")

Reporting was listed as out of scope. Added on request. Measured tab row:
`Custom reports · Google Ads · Meta Ads (Facebook Ads) report · Attribution report ·
Call report · Appointment report · Local Marketing Audit`

**Implemented** (first-party data only):

- **Call report** — Start/End date, "All numbers", Filters, Incoming/Outgoing toggle
  (14px/600), "Call by status" + "First-time calls by status" (18px/500),
  "Avg. call duration:" / "Total call duration:" (16px/400 label, 16px/500 value),
  "Top call sources" table: Source | Total calls | Won deals | Avg duration.
  Needs `ConversationEvent.call_status` (completed / no-answer / busy / voicemail /
  failed) — added to the model; the telephony project populates it for real.
- **Appointment report** — "Appointment reporting" 30px/500, Start/End date,
  "All calendars", status tiles (16px/500 label, **48px/500** count) for
  Booked · Confirmed · Cancelled · New · Showed · No-show · Invalid · Rescheduled,
  plus a Source breakdown. Appointment statuses widened to the measured set.

**Deliberately NOT implemented**, rendered as disabled tabs with the reason on hover:

| Tab | Why |
|---|---|
| Google Ads | third-party integration — excluded by request |
| Meta Ads (Facebook Ads) report | third-party integration — excluded by request |
| Local Marketing Audit | third-party integration — excluded by request |
| Attribution report | rendered **empty** on the live account; nothing measured to copy |
| Custom reports | GHL shows an empty state; the report builder is a separate product |

The three ad tabs were never opened on the live account — avoiding third-party
integrations also avoids provisioning/consent prompts and anything billable.

Reporting is now a real sidebar item, no longer dimmed.

> **AMENDED 2026-09-09:** the sidebar was trimmed — see "Sidebar trimmed to the
> product" at the end of this file. Reporting is unaffected and still a real item.

### AMENDED 2026-09-09 — the four dead tabs are gone from the UI

The table above stands as the record of *why* each report was not built. What
changed is only how that shows on screen. At the owner's request the four tabs
that could never be enabled were **removed from the DOM entirely** — not hidden,
not disabled:

- Google Ads
- Meta Ads (Facebook Ads) report
- Local Marketing Audit
- Attribution report

The first three are third-party marketing integrations, and CLAUDE.md puts
everything marketing-related out of scope, so no future version of this app
enables them; keeping a greyed tab implied a setting somewhere would switch it
on. Attribution rendered empty on the live account, so there is nothing to
build towards there either. The measured GHL tab row is therefore *deliberately*
no longer matched on this screen — `capture/diff.py` landmarks do not cover it,
but do not "fix" this back to parity.

**Custom reports remains**, still a visible but disabled tab with its hover
reason, because the owner asked for it to stay. It is a stub only: the report
builder is a separate product and is not being written.

The tab row is now: `Custom reports · Call report · Appointment report`.

Pinned by `test_reporting_shows_only_the_three_tabs_that_can_work` in
`backend/tests/test_frontend_layout.py`. `capture/ui_check.py` used to assert
those tabs existed *and were disabled*; that check now asserts they are absent.
`capture/capture_reporting.py`'s FORBIDDEN list is untouched — it governs what
may be clicked on the live GHL account, which is a different question.

### Call report — AMENDED 2026-09-09 (what each number is allowed to claim)

The owner read the Call report on production as placeholder data. Audited: the
volume, duration and per-source figures were computed from real rows all along —
production simply holds 6 calls and 10 opportunities that are all still `open`, so
"Won deals 0" was the truth. Three things were not the truth, and are now fixed:

- **A blank `call_status` is `unknown`, not `completed`.** The column is nullable,
  and `POST /api/events` — the telephony project's own feed — had **no field for it
  at all**, so every ingested call arrived blank and the donut labelled all of them
  completed. `EventIngest` now carries `call_status`, validated against the measured
  five; anything else is a 400 rather than a new slice of the donut. The sixth
  bucket, `unknown`, is OUR addition to the measured set: it exists so an unmeasured
  outcome cannot be reported as a good one.
- **"First-time" means first ever, not first in the window.** It was the caller's
  earliest call *inside the selected range*, which makes a caller of ten years'
  standing new every month. It is now their earliest call of all time, in the
  direction the Incoming/Outgoing tab selects.
- **The first-time card gets its own duration strip.** Both cards were handed the
  whole window's average and total, so the second card was the first card's figures
  under a different label. New payload keys:
  `first_time_avg_duration_seconds`, `first_time_total_duration_seconds`.

**"Won deals" attribution — a product decision, overrulable.** A won opportunity is
credited to a call source when the opportunity's contact **called inside the report's
window and direction**; it is counted once per opportunity however often that contact
rang. Previously any won opportunity whose contact merely shared a source was
counted, so a source could show wins it never produced. What the schema does NOT
support, and what was therefore not invented:

- no per-call attribution — `Opportunity.custom_fields.owen_call_id` is a documented
  join key to the telephony project, but no `ConversationEvent` column holds the
  matching id, so "this deal came from this call" cannot be answered today;
- no `won_at` — nothing records the date a deal was won, so the window selects the
  *callers*, not the wins. A deal won last year still counts for a caller who rang
  this week.

Layout, typography and column order are untouched; only the numbers changed.

## Safety contract — AMENDED 2026-08-13 (narrow, on the record)

The "Safety contract (binding)" section above is **read-only on the live GHL account**.
It was lifted **once, for one scope**, by the account owner after the conflict was
raised explicitly:

> Permitted: creating opportunity **custom fields** and **custom-field folders** in
> the measured location (`$GHL_LOCATION_ID`), for the CompanyCam checklist migration.

**Everything else in the contract still stands**, including the clauses that section
names by name: no delete/archive/merge/export/import, no send, no call, no pipeline or
calendar or user changes, nothing billable, no workflow run or test.

Enforcement is `capture/build/guard.py`, not discipline: a closed ALLOW list of six
verbs, a DENY list covering destructive *and metered* controls (AI, credits, A2P,
upgrade, checkout — the Workflows empty state advertises an AI builder directly beside
"Create workflow"), label re-checked at click time, disabled controls refused, and an
append-only audit trail at `captures/checklists/audit.jsonl`. 46 tests in
`capture/build/test_guard.py` (run explicitly — `testpaths` does not cover `capture/`).

### What was built

3 folders + 25 opportunity custom fields. Fields **61 → 86**, Opportunity **30 → 55**,
0 pre-existing fields lost, 0 pipelines touched. Verified by `capture/build/verify.py`
against the API.

**The spec came from Owen's real CompanyCam and Workiz records, not the ChatGPT draft
that opened the task.** The draft proposed 23 "Customer/Technician Journey" milestone
tickboxes; what the team actually fills in is an inspection *questionnaire* (answers,
not tickboxes) plus a 14-shot photo checklist. Building the draft would have produced
25 fields nobody touches while missing every field used daily. `capture/build/spec.py`
records the mapping, including the three Workiz "Extra Info" fields that were merged
rather than duplicated.

GHL's `CHECKBOX` is a multi-select group — treated as a limitation at first, it is
exactly right for a photo checklist: 14 shots are **one field with 14 options**.

### Measurement corrections (the old notes were wrong)

- **Custom fields route is `/settings/fields`**, not `/settings/custom_fields` (blank
  126-node shell).
- **7 `owen_*` fields, not 4.** `owen_call_signal`, `owen_lead_trigger`,
  `owen_signal_at` were undocumented. Plus **12 `workiz_*`** fields — a second live
  integration this file never mentioned. Workiz is being retired (Owen, 2026-08-13);
  the fields are left in place and nothing new depends on them.
- **The pipeline table above is stale.** It records one pipeline, 10 stages, 26
  opportunities. Measured 2026-08-13: **four pipelines**, AHS at **22 stages / 40
  opportunities**. Not caused by this work — no pipeline was opened. `verify.py`
  therefore asserts pipelines *exist* rather than pinning a stage count that will keep
  drifting.
- **The custom-fields grid is virtualised AND paginated.** A DOM scrape returned 47 of
  61 — *and a different 47 each run*. Only two disagreeing runs exposed it. Read the
  API response instead; the DOM is not a source of truth here.
- **GHL fires `customFields/search` twice per load.** Counting responses without
  deduping by id double-counts everything: `verify.py` initially reported 172 fields
  and flagged all 25 new ones as duplicates — a false alarm that reads exactly like
  real corruption and would have sent someone deleting live fields.

### UI mechanics worth keeping (`capture/build/panel.py`)

GHL's own `hr-` widgets. Every failure mode below is **silent** — the screenshot still
looks right:

- Comboboxes are type-to-filter `input.hr-base-selection-input`. Clicking the displayed
  text is a no-op; pressing Enter leaves the filter text uncommitted; the committed
  value lands in a sibling `.hr-base-selection-label`, so reading `input.value` reports
  failure on success.
- **Scope every lookup to `div.hr-drawer[role=dialog]`.** The grid behind the drawer has
  its own control also labelled "Field type".
- Dropdown options render in a **portal on `<body>`**, outside the drawer — matched by
  geometry (below, within 420px, horizontally overlapping) instead.
- The options editor is a **click-to-edit table**, not inputs: before you click, "Enter
  option" is a `<p>` in a `<td>` and no input exists. Row cells are
  `[drag][icon][label][value][action]` — `querySelector('td')` returns the drag handle.
- **"+ Add option" is a no-op while the last row is empty.** Fill then add, one at a
  time; pre-creating rows silently yields a single-option field.
- Submit buttons wrap their label several nodes deep, and are **disabled until valid** —
  a leaf-only resolver never finds them, and clicking a disabled one saves nothing while
  looking like success.
- Use **JS clicks, not `ElementHandle.click()`** (as `open_menus.py` already does): a
  leftover overlay turns Playwright's actionability wait into a 30s mid-form timeout.
- **A failed screenshot must never abort a run.** `Page.screenshot` timed out on "waiting
  for fonts to load" and killed a pass mid-way through creating fields.

### Not done, deliberately

Workflows (the account has **zero**, so nothing to conflict with), any pipeline change,
and the CompanyCam↔GHL integration — the checklist *content* moved, the systems still do
not talk. Owen wants the integration done together, last.

## Workflow builder — measured, and DELIBERATELY NOT AUTOMATED (2026-08-14)

Owen authorised building the workflows. They were attempted and **stopped on evidence**,
which was a checkpoint written into the plan beforehand, not a rationalisation after.

The builder is a **cross-origin micro-frontend in an iframe**
(`client-app-automation-workflows.leadconnectorhq.com`). Nothing from the Custom-Fields
work transfers — different DOM, different components, and `page.evaluate` on the top
frame cannot see into it. `capture/build/wf.py` holds what was mapped.

**Why automation was abandoned:**

- **Canvas readiness is nondeterministic.** "Add new trigger" appeared at 20s, was absent
  at 16s and at 18s, and on one run never appeared at all. `settle()` is useless here: it
  watches the top frame, which holds ~176 nodes because all content is in the iframe.
- **Direct navigation to `/automation/workflow/<id>` redirects to the list**, so the
  editor is reachable only by clicking through.
- **Click semantics are inconsistent across the frame boundary.** Synthetic
  `element.click()` opens the "Create workflow" dropdown but silently no-ops on
  `.hr-dropdown-option`; Playwright's real click works there but its actionability check
  then times out on `Add new trigger` while a raw DOM read finds that same button
  visible with a non-zero rect. Three attempts, three different outcomes.
- **The blast radius is wrong for a flaky target.** The canvas carries `Test workflow`,
  `Publish`, and a metered AI builder ("Build workflows for free by chatting with AI",
  BETA). Retrying clicks on an intermittently-responsive canvas beside those controls is
  not worth ~30 minutes of manual work.

**GHL's API is not an escape hatch** — the v2 workflows endpoint is read-only (`GET`
only), so workflows cannot be created programmatically either.

**State left behind:** exactly one workflow, `New Workflow : 1786720951713`, **Draft**,
0 enrolled, no trigger, no actions — the builder auto-creates and auto-saves a record the
moment "Start from Scratch" is chosen. It cannot run. Deleting it was outside the
permitted verb set by design, so it is disclosed rather than removed.

**Also measured:** `Create workflow` is a dropdown, not a button — options are
`div.hr-dropdown-option` (*Start from Scratch* / *Select from Template* / *Company based
workflow*), and clicking the inner `.hr-dropdown-option-body__label` registers as a click
and does nothing.

The eight workflows are specified step-by-step in `CHECKLIST-MIGRATION.md` for manual
build. The rule that matters: **no Send action, re-entry off, leave in Draft.**

## Deployment — DONE (2026-09-04)

Live on the **`owen-main`** VPS (Ubuntu 24.04) at
**`crm.dreamteamroofingfl.com`**, behind the Traefik v3.7 that already fronts the other
projects on that box. `app.dreamteamroofingfl.com` was already taken by ops_tracker.

**Caddy was the locked choice and is not what we used.** The server was already running
Traefik with a Let's Encrypt resolver, a `traefik-public` network and a house pattern of
Docker labels. Standing up a second reverse proxy beside it to honour a decision made
before that server existed would have been the wrong kind of consistency.

| Piece | Where |
|---|---|
| Checkout | `/opt/santiagoproperties/ghl-clone/` (the house convention) |
| Origin | `github.com/santiago1397/ghl-clone` (private); server pulls via a **read-only** deploy key |
| Containers | `ghl_clone_api`, `ghl_clone_worker`, `ghl_clone_web` |
| Config | `.env.prod`, 0600, never committed |
| Deploy | `./deploy.sh [--with-migrations] [--rebuild]` |

Decisions worth keeping:

- **The API and worker share one image.** The worker can never drift from the code that
  enqueued the job it is draining. Its inherited HEALTHCHECK is explicitly disabled —
  it curls `/api/health`, which the worker does not serve, so it would have sat
  permanently "unhealthy" while working fine.
- **`alembic upgrade head` is NOT in the container command.** It runs as a one-shot from
  `deploy.sh --with-migrations`, so a schema change is a decision rather than a side
  effect of a restart. `python -m app.seed` appears in no container command at all: it
  calls `drop_all()`.
- **`JWT_SECRET` is set explicitly.** Left unset, the app writes a generated secret into
  each container's own writable layer, so every restart invalidates every browser
  session while API tokens keep working — every human locked out, no obvious cause.
  Verified by restarting `ghl_clone_api` and confirming an existing session survived.
- **`COOKIE_SECURE=1`.** The code default is `0`.
- **`/docs`, `/redoc` and `/openapi.json` are NOT publicly reachable in production**,
  despite being open in the app. Traefik routes only `/api` to the API container, so
  they fall through to the SPA and return `index.html`. The note above about them being
  deliberately open still describes the app; it no longer describes the deployment.
- **Production starts empty.** No seed data — the 268 contacts in development are
  synthetic (`@example.test`). Real data waits on a GoHighLevel export, which the safety
  contract says the user must run themselves.

Verified against the deployed stack: 43 routes swept unauthenticated with **0 leaks**;
`/api/contacts` returns JSON 401 rather than the SPA (the Traefik priority rule works);
deep links serve `index.html`; session cookies are `Secure`+`HttpOnly`; the
`events:write` token is accepted on `/api/events` and **403s on everything else**;
`ghl health` reports `LoggingTransport`, so nothing can transmit.

## Triage decisions from the first production test run (2026-09-09)

An orchestrated test run against the live deployment (`orchestrate/PLAN.md`) produced 23
findings. Two were closed as intended behaviour rather than fixed, and one standing claim
in this file turned out to be false. Recorded so the next test run does not re-file them.

- **Per-stage and per-deal money is TECH-visible, by design.** The Dashboard Funnel shows
  `value_cents` per stage to a TECH whose `GET /api/dashboard` returns 403, because the
  card is fed by `GET /api/pipelines` (ANY_USER). A TECH can also read
  `GET /api/opportunities?pipeline_id=1` and see per-deal values directly. The STAFF gate
  on `/api/dashboard` therefore withholds only an aggregate of numbers that user can
  already read. This is not a leak: a TECH sees the deals they are dispatched to. The gate
  is inconsistent, and that inconsistency is now deliberate rather than accidental — do
  not "fix" it by stripping `value_cents` from `/api/pipelines`.

- **Desktop-only is the intended scope.** There is no responsive breakpoint: the 224px
  sidebar never collapses, so at 390px width the content pane is 166px. Content reflows
  rather than clips and nothing is unreachable, but reports are not legibly usable below
  roughly tablet width. This is a desktop GoHighLevel clone and the sidebar width is
  locked to GHL's (see "Responsive rules", which measures 1440/1920/2560 only — all
  desktop). A responsive pass is a project of its own, not a bug fix.

- **CORRECTION: production does NOT start empty any more.** The "Deployment" section above
  and `CLAUDE.md` both say the production database is empty until a GoHighLevel export
  lands. As of this run it holds 12 contacts, 10 opportunities, 6 calls, 8 appointments
  and 1 pipeline. Anything that tests against production must assume real records exist
  and must never touch a record it did not create itself.

- **A QA run destroyed a live credential.** The `callmon` machine token (id 1,
  `events:write`, owner `owen@telephony.local`) was permanently revoked by a test probe
  that assumed the call would be refused; as ADMIN it was accepted. Revocation is
  irreversible — only a sha256 is stored. A replacement was minted the same day. No ingest
  was interrupted (`/api/events` has never been called in the retained logs, and the
  telephony project holds no `ghl_pat_` value in its configuration), but the credential
  itself was unrecoverable. An unattended worker holding ADMIN will reach endpoints no UI
  route exposes: prefer the least role that can exercise a surface.

## Sidebar trimmed to the product, and Dashboard is the landing view (2026-09-09)

At the owner's request, **six items were removed from the sidebar outright** — gone from
the DOM, not hidden and not dimmed:

  Launchpad · Marketing · Sites · Memberships · Reputation · App Marketplace

The "Scope — v1" section above put all of these out of scope, and the shell rendered the
last five dimmed with an `Out of scope for v1` tooltip so the omission stayed visible
against GHL. That reasoning held while v1 fidelity was the bar. It no longer does: the
app is in daily use by four people who are not comparing it to GHL, and a permanently
dead row reads as a feature that is coming. These six are not deferred, they are out of
the product.

**Four out-of-scope items were deliberately KEPT**, unchanged, and are still dimmed:

  Payments · AI Agents · Automation · Media Storage

This is a decision, not an oversight — removing them is a separate call the owner has not
made.

> **AMENDED 2026-09-10:** the owner made that call for one of the four. **Payments is
> now removed as well**, and `/payments` redirects to Dashboard — see "Payments removed
> too" at the end of this section. AI Agents, Automation and Media Storage still stand
> as written above: kept, dimmed, untouched. `backend/tests/test_frontend_nav.py` pins both halves, because "we removed the dead
items" and "we removed every dimmed item" are indistinguishable in a diff.

This narrows the GHL-parity claim: the sidebar is now **deliberately not** a 1:1 copy of
GHL's shell. `capture/diff.py` (37/37) and `diff_panel.py` (35/35) measure the Contacts
view and the contact panel, not the nav, so both still pass. Any future whole-shell
comparison must treat these six rows as an intended deviation.

### Landing route

**The app lands on Dashboard**, and `/launchpad` resolves to Dashboard rather than
dead-ending, so old bookmarks keep working.

Two corrections to what was assumed when this was requested:

- **Launchpad was never the landing view.** `App.tsx` opened on `contacts`
  (`useState('contacts')`); Launchpad was only the first sidebar row. So the change of
  landing view is Contacts → Dashboard, a real product change, not the preservation of
  existing behaviour.
- **There is no router**, so `/launchpad` never 404'd. `@tanstack/react-router` is in
  `package.json` but nothing imports it, and nginx's `try_files` serves `index.html` for
  every path (the config comments say "once TanStack Router lands"). Every URL rendered
  the same default view and the path was ignored entirely.

`App.tsx` now reads `window.location.pathname` once at boot: `RETIRED_PATHS` maps
`/launchpad` to Dashboard and `history.replaceState` rewrites the dead path out of the
address bar — `replaceState` rather than `pushState`, so Back does not bounce off the
redirect. This is the app's only URL handling and it stays that way until the router
lands; `RETIRED_PATHS` is where the next retired link goes.

`frontend/src/pages/LaunchpadPage.tsx` was deleted (nothing else imported it), along with
the five icons the removal orphaned: `IconRocket`, `IconMegaphone`, `IconStore`,
`IconAward`, `IconDoc`.

### AMENDED 2026-09-10 — Payments removed too

At the owner's request, **Payments is gone from the sidebar as well** — same treatment,
same reasons, one day later. The owner will not handle payments in this system at all,
which is what `CLAUDE.md` has said since the beginning ("Everything marketing-related,
Payments, and the whole agency layer are deliberately out"); the nav row was the last
place the product still claimed otherwise.

Payments was the odd one among the four kept items: not a dimmed label but a **real,
clickable row** that opened a card reading *"Payments — not built yet"*. That is a
stronger promise than a dimmed row, not a weaker one — "not built **yet**" dates the
feature rather than denying it.

**`/payments` redirects to Dashboard**, exactly as `/launchpad` does and through the same
`RETIRED_PATHS` table in `App.tsx`. Payments was a sidebar row for the app's whole life,
so the path is in somebody's bookmarks and must land somewhere sensible.

**The three remaining dimmed items — AI Agents, Automation, Media Storage — were left
exactly as they are.** That is deliberate and stays a separate decision.

What the removal orphaned, and went with it:

- `IconCard`, which had no other caller.
- The `PLACEHOLDER` map in `App.tsx` and the branch that rendered it. **There was no
  `PaymentsPage.tsx`** — Payments never had a component of its own; it fell through the
  ternary chain to `PLACEHOLDER[active]`, and it was the last key that did. Every
  remaining sidebar key renders a real page, so the fallback branch now renders
  `DashboardPage`: an unrecognised view lands where a retired path lands, not on an
  empty pane.

**No backend code was touched, because none is Payments-specific.** There is no payments
endpoint, model, table, migration or CLI command. The only payment-shaped things in the
backend are `EventType.PAYMENT` and `EventType.INVOICE` — two members of the timeline
event enum, listed in `ACTIVITY_TYPES` and surfaced by the Conversations activity-type
filter, which is measured GHL parity for a *conversation* feed and has nothing to do
with the removed view. They are also baked into the `eventtype` Postgres enum by the
baseline migration, so removing them would mean a migration against a live database for
no product gain. Left alone, deliberately.

`backend/tests/test_frontend_nav.py` pins all of it and gained one assertion the
2026-09-09 trim did not have: it now rejects a nav row that was **commented out** rather
than deleted. The file's comment-stripping — which exists so a prose record like this one
does not read as a live entry — made such a row invisible to every other assertion in it.

The GHL-parity note above extends to this row: the sidebar is one item further from GHL's
shell on purpose. `capture/diff.py` (37/37) and `diff_panel.py` (35/35) measure the
Contacts view and the contact panel, not the nav, so both are unaffected.

## Contact Details "Actions" tab — OUR design, not measured (2026-09-09)

The Actions tab shipped as the sentence "Actions are not implemented in v1." It now
contains exactly one action: **Delete contact**, driving the existing
`DELETE /api/contacts/{id}`.

**Its layout is ours, and nothing about it is parity.** GHL's own Actions tab was
never opened on the live account — the only "Actions" anywhere in `captures/` is
**Bulk Actions** from the Contacts list toolbar, which is a different control in a
different place. The "Design system — LOCKED" and "Responsive behaviour — LOCKED"
sections tie structural design to measured captures; this is a deliberate, disclosed
exception, not an oversight. **Re-measure it if someone opens a live GHL session
later**, and treat the current arrangement as provisional until then.

Scope is one action on purpose. Inventing a full Actions menu (merge, export, add to
workflow) would be guesswork dressed as measurement; one action backed by a real
endpoint is not. Bulk delete is **deliberately deferred** — the list checkboxes stay
wired to nothing, because this CRM is in real daily use and a bulk delete is the
easiest way to lose real customer records.

What the tab does, and why it is shaped that way:

- **ADMIN only.** The route is `auth.ADMIN`, so the control renders disabled with an
  explanatory title for DISPATCHER and TECH rather than letting them click through to
  a 403 — the precedent set the same day by `d1f7c50` (Add Contact) and `b943f4b`.
- **Two confirmations, not one.** The first confirm sends `force=false` on purpose.
  A contact that still has opportunities is then refused with 409, and **that refusal
  is rendered as the second confirmation**, naming the opportunities and saying they
  will be kept and detached. Confirming again retries with `force=true`. Forcing on
  the first click would detach opportunities without the user ever learning there
  were any, which is exactly what the 409 exists to prevent.
- **`GET /api/contacts/{id}` now also returns `opportunities: [{id, title}]`.** The
  confirmation has to name them, and the alternative was scraping ids out of the
  409's prose — which would tie the panel to the wording of an error message. The
  409 remains the authority on whether the delete is allowed; the list is only how
  the sentence is written.
- The endpoint's semantics are unchanged: opportunities are **detached, never
  deleted** (`custom_fields.owen_call_id` is the telephony join key), conversations
  **are** deleted explicitly, and the response is
  `{"deleted": id, "detached_opportunities": [...]}`.

## AMENDMENT (2026-09-09): the Calendars Month view and the appointment dialog are OURS

This section **overrides**, for these two surfaces only, the rule that structural design
is locked to a measured capture ("Design system — LOCKED", "Responsive behaviour —
LOCKED"). It is recorded so nobody later mistakes either for measured parity.

**GHL's Month view was never measured.** `captures/calendars/` holds the **Week view
only** — one 1440×900 capture of the 24-hour vertical grid. Nobody ever opened GHL's
Month view on the live account, so there is no HTML, no screenshot and no computed style
to compare against. The same is true of GHL's **appointment / booking modal**: it is on
the "Still uncaptured everywhere else … modals, drawers" list under "Known measurement
gaps", and it was never opened.

**What was actually shipped, and why.** Month view was not merely unmeasured, it was
broken. `days = 35` drove the range label, the API query window and the prev/next arrows,
while *both* grid renders did `dayList.slice(0, view === 'Month view' ? 7 : days)`. So:

- Month view was pixel-identical to Week view.
- It fetched five times the data it drew; appointments between day 8 and day 35 arrived
  and were discarded.
- The arrows paged 35 days at a time, so ~80% of every range was unreachable.

The `slice` was a deliberate cap, not a typo — 35 columns of a 24-hour vertical grid is
not a layout. A month needs a different shape, so on the owner's instruction a
**conventional weeks-by-days grid of day cells** was built: whole Sunday-to-Saturday weeks
(28/35/42 cells) covering every day of the anchor month, neighbouring-month cells drawn
dimmed rather than blank, appointments as compact chips with a `+N more` affordance past
three. The arrows now step one calendar month and the label names the month drawn.

Alongside it, the previously inert `+ New` button and a double-click on an empty slot both
open **one** create-appointment dialog. Its layout is likewise ours.

**Consequences, binding:**

- Day view and Week view are unchanged and remain the measured surfaces. Anything that
  touches them still has to answer to `captures/calendars/`.
- **If a live GHL session is ever opened again, capture the Month view and the booking
  modal and re-measure both.** Where GHL differs, GHL wins for structure, exactly as
  everywhere else. Until then neither may be cited as evidence of parity.
- The week still starts **Sunday**, which *is* measured, and the design tokens are the
  measured ones. Only the arrangement is ours.
- This closes the standing blocked finding about Month view. (Referred to elsewhere as
  **F2-2**; no such record exists in this repository — `orchestrate/` carries no finding
  by that id — so this amendment is the record.)

**Creating only. Editing and rescheduling are deliberately out**, and this is not an
oversight to be tidied up later: reminder jobs dedupe on a `dedupe_key` that includes the
appointment's start time, so a move must cancel the old pending jobs or the customer
silently receives **no** reminder. `PATCH /api/appointments/{id}` already does that
correctly and is covered by `test_rescheduling_an_appointment_schedules_new_reminders`;
wiring a UI to it needs its own task and its own tests. The dialog creates, and nothing
else.

**One deviation from the task's own field list, on the record.** The task named `status`
among the dialog's fields. `AppointmentCreate` does not accept it — a booking always takes
the model default `confirmed` — and inventing a control the server ignores is worse than
having none. The dialog therefore shows Status **disabled**, with a title saying why, the
same choice already recorded above for the Conversations filter types that v1 does not
implement: *disabled rather than omitted, so the gap stays visible.* Adding `status` to
the POST model would be a contract change and is a separate decision.

**One backend change came with it.** `title: str` accepted `""` and `"   "`, so an
untitled appointment was created happily and drawn as an empty chip — indistinguishable
from a rendering bug. `POST /api/appointments` now rejects a blank title with
400 `an appointment needs a title`, and stores the stripped value. Enforced at the API,
not only in the browser, so the CLI and the telephony feed get the same answer.

**Testing note.** `frontend/src/lib/calendarGrid.ts` holds all of the range arithmetic and
imports nothing, so `backend/tests/test_calendar_grid.py` runs it under **node** and
asserts real behaviour — cell counts per month, an appointment on the 20th reaching a
drawn cell, the arrows stepping 31 Jan → February rather than 3 March, DST-safe day
stepping. That is a deliberate step up from this project's usual "assert against source"
frontend idiom, which would have passed against the broken version. CI's backend job now
installs node for it; if node is ever absent the file skips loudly rather than silently
passing.

## AMENDMENT (2026-09-10): the appointment detail panel is OURS, and editing is now IN

This **supersedes the closing paragraph of the 2026-09-09 amendment above**, which
said: *"Creating only. Editing and rescheduling are deliberately out, and this is not
an oversight to be tidied up later."* That deferral was correct at the time and it
named its own condition — *"wiring a UI to it needs its own task and its own tests"*.
That task has now been done, so the deferral is lifted rather than contradicted.

Clicking an appointment opens a detail panel. From it the booking can be read,
edited, rescheduled and cancelled.

### It is our design, not measured GHL

**GHL's appointment detail modal was never captured.** `captures/calendars/` holds one
1440×900 capture of the Week view and nothing else; the modal is on the "Still
uncaptured everywhere else … modals, drawers" list under "Known measurement gaps",
and it was never opened on the live account. The "Design system — LOCKED" and
"Responsive behaviour — LOCKED" sections tie structural design to measured captures,
so this is a **deliberate, disclosed exception** — the third one, after the Month
view / booking dialog (2026-09-09) and the Contact Details Actions tab (2026-09-09).

`AppointmentDetailDialog.tsx` is built from the same primitives as
`NewAppointmentDialog` and `OpportunityDetail`, which is the in-repo idiom. **Nothing
about it may be cited as parity.** If a live GHL session is ever opened again,
capture the appointment detail modal and re-measure; where GHL differs, GHL wins for
structure, exactly as everywhere else.

**The Week and Day grids are untouched.** They remain the measured surfaces and still
answer to `captures/calendars/`. What was added to them is an `onClick` on a booking
and a struck-through treatment for a cancelled one — an interaction and a state, not
a layout change. `capture/diff.py` (37/37) and `diff_panel.py` (35/35) measure
Contacts and the contact panel and are unaffected.

**Drag-to-move on the grid is out of scope, by the owner's choice.** Rescheduling is
done through the panel's form. This is not a gap to be filled later without asking:
the grid is the surface parity is judged on, and drag handlers on it are a change to
that surface. `test_dragging_a_booking_on_the_grid_was_not_built` pins the absence,
because "we chose not to" and "nobody got round to it" are indistinguishable in a
diff.

### The reminder trap, and the bug that was still in it

CLAUDE.md records the rule: reminder jobs dedupe on `dedupe_key`, so anything that
reschedules must cancel the old jobs and use a key that includes the new time. The
existing `PATCH` did that and was covered. **It was still wrong in one move.**

The key is `appt_reminder:<id>:<starts_at>:<offset>`, and `enqueue()` refuses a key
that already exists **whatever its status**. The superseded jobs were retired by
setting `status = "cancelled"` and left holding their keys. So moving a booking
Tuesday → Friday → **back to Tuesday** hit the retired Tuesday key, `enqueue()`
returned `None`, and the customer got **no reminder at all** — the same silent
failure putting the start time in the key was meant to prevent, one move later. And
"move it back" is the most ordinary correction a dispatcher makes.

**A retired reminder job now releases its dedupe key** (`dedupe_key = None`, the old
value kept in `payload.superseded_key` so the trail still says which reminder the row
was). The key is an idempotency guard on live work, not a permanent record; it is
handed back when the work stops being live. The row itself is still `cancelled`
rather than deleted, so the history survives.

Two more holes closed with it, both the same shape:

- **`DELETE /api/appointments/{id}` left the reminders `pending`.** The old docstring
  argued this was fine because `_h_appointment_reminder` re-checks the status at run
  time, so nothing could actually send. That is true and it is not enough: "could
  never send" and "is not queued" are different statements to whoever reads
  `ghl jobs list --status pending`, and only the second one is now true. The endpoint
  returns `reminders_cancelled` so the count is visible rather than assumed.
- **Reaching `cancelled` through the Status field did nothing to the queue, and
  reviving a cancelled booking scheduled nothing at all.** Both now go through one
  retire-then-maybe-requeue path, so the state of the queue follows from the state of
  the appointment however it got there.

`NO_REMINDER_STATUSES` lives in `automations.py` and is read by both the run-time
handler and the edit-time API, so they cannot drift on what "off" means.
`on_appointment_booked` refuses a cancelled booking outright, because it is now
re-entered on every reschedule.

Asserted by counting rows in `jobs`, never by inspection: exactly one pending
reminder per offset after a reschedule and at the new time, the same after two
reschedules, a reminder still queued after a move back to the original time, zero
after a cancel by either route, reminders again after a revive, an already-sent
reminder left untouched, and a title edit that does not churn the queue.

### Product decisions in the panel, overrulable

- **`blocked` is not offered in the Status dropdown.** It is what the Manage view's
  "Blocked slots" filter selects — a slot that is not an appointment — so offering it
  beside the measured report statuses would let a customer's booking be moved out of
  the Appointments view from a control that looks like a report tile. The other eight
  measured statuses are all offered. A booking that already carries an unlisted
  status keeps it as an option, so opening one cannot silently rewrite it.
- **A cancelled booking is still drawn, dimmed and struck through.** Cancel is a
  status change, not a delete (unchanged, and pinned by an existing test), and GHL's
  own Appointment report has a Cancelled tile — so the row is a record. But a slot
  that reads as still taken is worse than one that reads as cancelled. The API is
  unchanged: `kind=appointments` still means "not a blocked slot", which is what GHL's
  View-by-type measures, and no cancelled-exclusion was invented for it.
- **Opening the panel is not gated on the role.** `GET` is `ANY_USER`; `PATCH` and
  `DELETE` are `auth.STAFF` and were **not widened**. So a TECH can open a booking and
  read the notes and the customer's name on the job they are driving to, and every
  control is `disabled` with a title saying why — the precedent `d1f7c50` and
  `b943f4b` set.
- **The panel says what happened to the reminders.** `PATCH` returns `automation`, and
  `frontend/src/lib/reminders.ts` turns it into a sentence. That file is import-free
  like `calendarGrid.ts` so `backend/tests/test_reminder_sentence.py` runs it under
  node — the usual "assert against source" idiom is too weak here, because the whole
  point of the text is that a reminder which silently did **not** move must not be
  reported as one that did. "Too soon for a reminder" and "suppressed: contact is on
  DND" are said out loud, and an outcome the function has not been taught is shown
  rather than collapsed into a bare "Saved."

### A bug found by driving it in a browser, not by the tests

The summary line and the cancel confirmation are written from the loaded record. A
save re-seeded the form and left that record stale until the 30-second refetch, so
**editing a booking and then cancelling it showed a confirmation naming the old title
at the old time** — a destructive prompt describing a slot that no longer existed,
which reads as being about a different appointment. The `PATCH` response is written
into the query cache instead. None of the source-level assertions would have caught
it; a Playwright pass over the real panel did. Pinned by
`test_a_saved_edit_reaches_the_confirmation_that_names_the_appointment`.

### Audit of the rest of the Calendars screen (asked for, deliberately not fixed)

| Control | What it does today |
|---|---|
| "Meetings" event-type select | **Decorative.** A `<select>` with one `<option>`, no `value`, no `onChange`, wired to nothing. It is the measured GHL "Meetings ▾" control and there are no event types in the model to populate it. |
| "Show buffer time" | **Inert, and honestly labelled** — `disabled` with `title="Buffer time is not modelled in v1"`. `Calendar` has columns `id, name, user_id, pipeline_id, color` and nothing else, so there is no buffer to show. |
| "Blocked slots" (View by type) | **The filter is real** — it sets `kind`, and the backend filters `status == "blocked"`. But **nothing in the UI can create a blocked slot**: `AppointmentCreate` has no `status`, and the detail panel deliberately omits `blocked`. So the view works and is always empty unless a slot is set through `ghl appts update --status blocked`. Real plumbing, no way in. |
| Appointment list view tab | **Real**, and now a way into the panel. It is the same range-and-filter query as the grid, rendered as a table; it has no sorting or pagination of its own. |
| "Calendar settings" (header) | **Decorative.** A `<div>`, not a button — no handler, no target. Deliberately left alone: it is a separate queued task with its own migration. |

Nothing in that table was changed beyond making a list row clickable.

### Known gap, not caused by this work

Under **SQLite** the API emits appointment timestamps with no UTC offset, because
SQLite hands back naive datetimes even from a `timezone=True` column — the reason
`_aware()` exists in `main.py`. JavaScript reads a bare ISO datetime as *local*, so a
browser served by a SQLite backend draws every appointment shifted by the host's
offset (observed: 2h on a Europe/Berlin host, during the smoke run). SQLite is only
ever used for tests and bootstrap, and both dev and production run Postgres, where
the column returns aware values — so this should have **no product impact**. It is
recorded rather than fixed because it could not be verified here: the local Postgres
would not accept the documented credentials, so the Postgres half of that claim is
reasoned from the dialect behaviour this file already documents, not measured.
**Worth one check by someone with those credentials.**

## AMENDMENT (2026-09-10): the Dashboard works, and most of it is OURS

The owner read the Dashboard as decorative. **Most of it was not** — the three
measured cards had been computed from real `Opportunity` rows all along, and on
production `Conversion rate 0.00%` / `Won revenue $0.00` were the literal truth,
because all 10 opportunities are still `status = open`. Nothing has been won, so
nothing can be reported as won. That is recorded here because it is the second
time a correct zero has been mistaken for a placeholder on this project (the Call
report amendment of 2026-09-09 is the first), and it will happen again.

Four things genuinely were wrong and are now fixed. Everything else on the screen
was swept; the full control-by-control inventory is in
`.qa/state/dashboard-done`.

### 1. The date range now filters, on CREATION date, defaulting to ALL TIME

`GET /api/dashboard` accepted only `pipeline_id`. The control offered
"Last 7/30/90 days" and changed nothing, so every card showed all time under
whichever label had last been clicked. It takes `start`/`end` now.

Two decisions of the owner's, both binding:

- **The window filters on `Opportunity.created_at`.** Not the stage-change date:
  `updated_at` moves whenever anyone touches a record. Not the won date: there is
  no `won_at` column at all — the same gap the Call report amendment ran into,
  and the reason "Won deals" there selects callers rather than wins.
- **No range means all time**, and "All time" is the first option in the control.
  Production's opportunities were created between 8 and 16 August; a 30-day
  default would have emptied this screen the moment the filter started working,
  and a fix that reads as a regression is worse than the bug it fixes.

Nothing on the screen is exempt from the range. The windows are rolling (the last
7×24 hours, not the last seven calendar days) and open at the top end.

### 2 and 3. Both funnel percentage columns were comparing the wrong things

One cause, two symptoms. Both columns compared stage **occupancies** — how many
deals are sitting in a stage right now — when a funnel is about **populations**:
how many got that far.

- "Next step conversion" was `count[i+1] / count[i]`, which reads **400%**
  wherever a later stage holds more deals than an earlier one. Production's
  pipeline does exactly that. A conversion rate above 100% is not a rounding
  problem, it is the wrong quantity.
- "Cumulative" was `count / total` — a distribution, printed under a heading that
  promises a cumulative, so it rose and fell down the column (30% / 10% / 40%).

**The definition now in force, and open to being overruled:**

- `reached[i]` = the deals in stage *i* **or any later stage**.
- `cumulative_pct[i]` = `reached[i] / reached[0]` — the share of the pipeline's
  deals that got this far or further. It starts at 100% and can only fall.
- `next_step_pct[i]` = `reached[i+1] / reached[i]` — of the deals that reached a
  stage, the share that went on to the next one. It cannot exceed 100%.
- `null`, rendered `--`, where a figure has no denominator: the last stage has no
  next step, and a stage nothing reached has no population.

**`reached` is INFERRED, and that is the load-bearing caveat.** The schema keeps
no stage history. A deal in stage 4 is assumed to have passed through stages 0-3,
and a deal lost at stage 2 still counts as having reached stage 2. This is the
only reading the data supports; it is not a record of movement. A real answer
needs a stage-transition table, which is a schema change and a separate decision.

Lost and abandoned deals stay in the funnel, so its counts reconcile against the
kanban columns — which is what anyone cross-checking will do first.

### `GET /api/dashboard/funnel` is new, and is deliberately ANY_USER

The Funnel and Stage distribution cards were fed by `GET /api/pipelines`, which
counts every opportunity ever created and therefore could not obey a date range.
They are fed by a new route instead.

That route keeps `/api/pipelines`' **ANY_USER** gate on purpose. The triage note
of 2026-09-09 above records per-stage counts and money as deliberately
TECH-visible — a TECH can already read them from `/api/pipelines` and
`/api/opportunities` — so folding the funnel into the STAFF-only
`/api/dashboard` would have quietly removed a card from a dispatched tech's
screen while withholding nothing they cannot already read. `/api/dashboard`
itself is unchanged: still STAFF, still 403 for a TECH, and the cards it feeds
still render the refusal rather than a dashboard of zeros.

### 4. Controls that looked clickable and did nothing

Per CLAUDE.md's standing rule, and the precedent set by the Reporting tabs and
the Contact Details Actions tab: a control either works or is disabled with the
reason on hover.

- **Per-card gears** were `disabled` with "not implemented in v1". They open a
  settings popover now, carrying only options that genuinely change that card —
  follow the range or pin the card to all time; the conversion denominator
  (won/decided or won/all); whether the funnel draws stages holding nothing; the
  distribution's sort order. Persisted in `localStorage`.
- **The "Dashboard" selector** was a `<button>` with no handler. It opens a menu
  of the dashboards that exist. There is one, so it lists one.
- **`⋮`** was a `<span>` — not focusable, not a button. It is a menu: Refresh
  figures, Download as CSV.
- **"+ New" stays DISABLED**, with a fuller reason on hover. See below.

### Custom dashboards are NOT built, and this is what they would take

"+ New" and the dashboard selector both imply user-defined dashboards. That is a
build of its own — a widget registry, a persisted per-dashboard layout, a layout
editor, and a card-configuration model that every card must then honour — and
this CRM is in daily use by four people. A half-working dashboard builder on it
is worse than an honest "not built". Rough shape if it is ever wanted:

- a `dashboards` table plus a `dashboard_cards` table (dashboard, card type,
  position, size, per-card settings as JSONB), and CRUD behind the STAFF gate;
- a card registry keyed by type, so a saved card can be resolved to a component;
- drag-and-resize editing (`dnd-kit` is already a dependency for the kanban);
- the five existing cards refactored to take their settings as data rather than
  as page state.

Two to three days, most of it in the editor rather than the data model.

### What on this screen is measured, and what is ours

The **three top cards keep their measured structure and typography** — titles at
16px/600, the donut with its centred total, the horizontal value bars with the
"Total revenue" footer, the conversion ring. Only their behaviour changed. The
Funnel and Stage distribution cards likewise keep their measured shape.

**Everything the captures never covered is our own design and may not be cited as
parity**, exactly as with the Calendars Month view and the Contact Details
Actions tab:

- the contents of the date-range menu (GHL's own menu was never opened);
- the per-card settings popovers, in full;
- the "Dashboard" selector menu and the `⋮` menu;
- the `--` shown where a percentage has no denominator (it reuses the measured
  empty-field dash, but this use of it was not measured);
- the caption naming a card's window when it differs from the header control's.

Re-measure all of these if a live GHL session is ever opened again; where GHL
differs, GHL wins for structure, as everywhere else.

### One test-infrastructure hazard, found and NOT fixed

`backend/tests/conftest.py` puts the throwaway SQLite database at a fixed path,
`<tempdir>/ghl_clone_test.db`. Every worktree on this machine therefore shares one
file, and two agents running `pytest` at the same time drop and recreate it under
each other — 14 failures and 18 errors across every module, in tests neither
agent touched. Verified: the same commit passes 285/285 with a private `TMPDIR`.

Left alone deliberately. The fix is a line in shared test infrastructure that
several branches would each have to edit, and merging four different versions of
it is worse than the hazard. Until someone owns it: run
`TMPDIR=<private dir> uv run pytest`, and treat a broad cross-module failure as a
collision to be re-run before it is investigated.

## Global search — the endpoint is measured-free, and the overlay is OURS (2026-09-10)

The sidebar search row has rendered a magnifier, the word "Search" and a `ctrlK`
badge since the shell was built, inside a plain `<div>` with no handler. Nothing
happened on click and ctrl+K did nothing. It is now a button and the shortcut is
bound. This section records what is measured about the result and what is not.

**The ROW is measured; the OVERLAY it opens is not.** `captures/contacts/` carries
the row's geometry and the badge (the `measured:` comment in `Sidebar.tsx` cites
it), and turning the `<div>` into a `<button>` changed none of those values —
`backend/tests/test_search_ui.py` pins them so a later restyle has to argue with a
test. **GHL's open search overlay was never captured on the live account.** It is
not in `captures/`, it is on the "Still uncaptured … modals, drawers" list under
"Known measurement gaps", and nobody opened it. So the palette's layout, its
wording, its grouping and its keyboard model are **ours**, exactly like the
Calendars Month view and the Contact Details Actions tab before it. This
**overrides**, for this surface only, the rule that structural design is locked to
a measured capture. **If a live GHL session is ever opened again, capture the
search overlay and re-measure.** Until then it may not be cited as parity.

Neither `capture/diff.py` (37 properties) nor `diff_panel.py` (35) looks at the
search row — their landmarks are `body`, the `aside` box, the "Contacts" nav row,
the location name and city, the page title and three column headers. Both diffs
are therefore unaffected by this work; see the honest note about them in
`.qa/state/search-done`.

### Scope: three sources, and NOT call transcripts

`GET /api/search` covers **contacts** (name, email, phone), **opportunities**
(title, every status) and **conversation messages** (bodies). Call transcripts are
**deliberately excluded**, on the owner's instruction, even though `GET /api/calls?q=`
already searches them and will keep doing so. A recorded call is minutes of speech,
so a short query hits nearly every one and buries the contact the user was actually
reaching for. This is a scope decision, not an oversight — do not "finish" it by
adding transcripts without asking.

Opportunities are searched across **every status**, not the `open` default that
`GET /api/opportunities` carries. A palette is how you go back to a deal you
already won or lost; the row prints the status so the difference is visible.

### One endpoint, not a fan-out

The palette could have called `/api/contacts`, `/api/opportunities` and
`/api/messages` itself. It does not, and the reason is not only request count:
ranking, the per-group cap and the role rule would then live in three call sites
with three chances to disagree, and `/api/opportunities` requires a `pipeline_id`
the palette has no business choosing. One endpoint, one place to test.

Every group appears in every response, empty ones included, so "no results" is a
property of the groups rather than of a missing key; `total` is the true match
count and `truncated` says the list was cut. **The cap is reported, never silent** —
a palette that shows five of forty without saying so teaches people the record
they want is not in the system.

### AMENDMENT: internal notes (NOTE, INTERNAL_COMMENT) are STAFF-only (2026-09-10)

**This removes access a TECH had.** It is a deliberate product change, made by the
owner while deciding how search should treat them, and it is recorded here because
nothing in this file previously said a TECH could not read them — they could,
everywhere.

The finding that prompted it: **there was no per-record role filtering anywhere in
this application.** `/api/contacts`, `/api/opportunities`,
`/api/conversations/{id}/events` and `/api/messages` are all `auth.ANY_USER`, so a
TECH could already read every contact with email and phone, every opportunity with
`value_cents` (locked as intended by the 2026-09-09 triage above) and every message
body including the crew's internal commentary. "A TECH must not see anything they
could not already reach" was therefore satisfied by *any* implementation, and no
TECH/ADMIN difference could be demonstrated without introducing a new rule.

The rule chosen: NOTE and INTERNAL_COMMENT are the team talking to itself —
recorded for the crew, never transmitted, and already exempt from DND suppression
for exactly that reason (`automations.INTERNAL_TYPES`). They are now STAFF-only.

**Enforced at every door, in one predicate** (`_sees_internal` in `app/main.py`):
the thread view, `GET /api/messages`, `GET /api/search`, and the composer. Search
alone was considered and rejected — a TECH would fail to find a word in the
palette and then read that same note two clicks later in Conversations, which is
the inconsistent-gate failure the 2026-09-09 triage complains about once already.

- An **explicit** ask (`?type=NOTE`, `?filter=internal_comment`) is refused **403**
  rather than silently emptied, so `ghl msg list --type NOTE` says why it is empty.
- An **unfiltered** read is **narrowed**, not refused: an inbox that 403s because
  one hidden row exists would be unusable.
- **Writes are gated by the same predicate.** A note whose author cannot then read
  it is a worse bug than a refused write. A TECH's customer-facing SMS is
  untouched.
- The Conversations "Internal Comment" filter renders **disabled with a reason**
  for a TECH rather than vanishing — the precedent set by Add Contact, Add
  Opportunity and the Actions tab.

Consequences to be aware of: the `ghl` CLI has no `--role` concept, so a TECH's
token now gets 403 from `ghl msg list --type NOTE`; and `capture/`'s scripts sign
in with whatever account `GHL_APP_EMAIL` names, which must stay staff for the
thread captures to contain internal comments. Revert commit `89ff7c9` alone to put
the old behaviour back.

### Product judgement calls, all overrulable

Made because the work needed an answer, not because they were specified:

- **Per-group cap of 5** (`limit`, 1–25). Three groups at five rows fits one
  screen without scrolling.
- **Ranking**: contacts alphabetically by name; opportunities and messages
  most-recently-touched first. Deterministic and explainable. Relevance ranking
  (prefix beats infix, name beats email) was not attempted — it needs a corpus to
  tune against and this database has 268 synthetic contacts.
- **Message bodies only.** Email **subject lines are not matched**, because the
  owner said bodies. One `or_` clause to add if that is wrong.
- **Enter on a message opens its CONVERSATION**, not the message: there is no
  per-message screen, and the thread is what the searcher wants.
- **Arrow keys wrap** at both ends rather than clamping.
- **Wording**: "Showing 5 of 12 — keep typing to narrow" under a capped group, and
  the no-results state names the transcript exclusion so the gap stays visible.

## AMENDMENT (2026-09-10): the Opportunities tabs are OURS, not measured

`TABS = ['Opportunities', 'Forecast', 'Pipelines', 'Bulk Actions']` and the `⋯`
menu's `Export | Restore opportunities | Manage smart lists | Dashboard insights`
were **measured as labels and nothing else**. `captures/opportunities/` holds the
**board** — the kanban, its columns, its cards and the toolbar around it. Nobody
ever opened any of those tabs or menu items on the live account, and the safety
contract forbids clicking Export there, so there is no HTML, no screenshot and no
computed style behind any of them.

This section **overrides**, for these screens only, the rule that structural
design is locked to a measured capture ("Design system — LOCKED", "Responsive
behaviour — LOCKED"). Everything built behind those labels is **our design**. It
may not be cited as evidence of GHL parity, and if a live session is ever opened
again these screens should be captured and re-measured, with GHL winning on
structure exactly as everywhere else.

**The board itself is unchanged** and remains a measured surface: same 240px
column pitch, same 230px cards, same drag handling. The tabs switch what is drawn
*below* the pipeline row; they do not restyle the board.

### Forecast — projected revenue by stage (OUR design)

`GET /api/forecast?pipeline_id=` and `frontend/src/components/ForecastPanel.tsx`.
Read-only, no new tables.

The real risk here was never the layout, it was arithmetic: a forecast is the
easiest possible way to put a **second, contradictory set of numbers** on data the
Dashboard already publishes. So:

- `_status_rollup()` in `backend/app/main.py` is now the single implementation of
  the Dashboard's by-status arithmetic. `GET /api/dashboard` returns it verbatim
  and the forecast quotes it, so the two cannot drift apart in a later edit.
- The forecast's per-stage `count` and `value_cents` are the same figures
  `GET /api/pipelines` feeds the Dashboard **funnel** with — every status, not
  only the open ones. A forecast that counted only open deals would draw a
  different bar for the same stage on two screens.
- The panel formats numbers and computes none. `test_the_forecast_quotes_the_
  server_rather_than_recomputing` pins that.

**The projection rule, stated because it is a judgement call the owner can
overrule:**

    weighted  = the stage's OPEN value x the pipeline's conversion rate
    projected = money already WON in the stage + weighted

One rate for the whole pipeline, and it is the rate the Dashboard already shows
(`won / (won + lost)`, two decimals). **Per-stage win probabilities were rejected
deliberately**: nothing in this schema records stage history, so a won deal simply
sits in whatever stage it was won in, and "the win rate of Inspection" would in
fact be measuring *where deals get marked won*. That is a plausible-looking number
nobody can check. The screen states the rate and where it comes from, rather than
weighting invisibly.

Weighting is integer arithmetic in whole cents, half up (`_weighted()`), for the
same reason `centsFromDollars` avoids floats — `round(v * rate / 100)` both loses
halves to banker's rounding and depends on IEEE-754. Because the published rate
carries exactly two decimals, every figure in the response can be re-derived by
hand from the other figures in the same response.

**Role: STAFF, matching `/api/dashboard`.** The tab is dimmed for a TECH with an
explanatory title rather than opening onto a 403 (precedent: `d1f7c50`, `b943f4b`).
This deliberately repeats the inconsistency recorded under "Triage decisions" —
per-stage money is already TECH-visible through `/api/pipelines` — rather than
inventing a third rule for a third screen.

**Tabs with nothing behind them stay dimmed and say so**, in a `LIVE_TABS` set,
rather than switching to a blank pane. Same disabled-rather-than-omitted rule as
Import, the Conversations filter types and the Reporting tabs v1 does not
implement.

### Bulk Actions — three actions, and the fourth is refused (OUR design)

`POST /api/opportunities/bulk/stage`, `POST /api/opportunities/bulk/owner`, and a
client-side CSV of the selection. The Bulk Actions **tab is the selection mode**:
the board and its filters stay exactly as they are, the cards start picking
instead of opening, and a bar appears above them.

- **Bulk move stage — ANY_USER**, matching `PATCH /api/opportunities/{id}`. A TECH
  may move a deal between stages (CLAUDE.md); ticking five cards must not need a
  right that dragging one of them does not.
- **Bulk assign owner — STAFF**, matching `PATCH /api/opportunities/{id}/detail`.
  Changing an owner is editing the record. The control is disabled with a title
  for a TECH rather than 403-ing on Apply.
- **Bulk export — no request at all.** The rows are already on the client.

**NO BULK DELETE.** Deliberate, and the same call already made for the Contacts
list. `Opportunity.custom_fields.owen_call_id` is the telephony project's live
join key; opportunities are never cascade-deleted from a contact for exactly that
reason; and a checkbox column is the fastest way ever invented to lose real
customer records in one click. `DELETE /api/opportunities/{id}` still exists, one
at a time. The bar **says this out loud** rather than just omitting the button — a
missing control reads as an oversight and gets "fixed" later by someone who does
not know why. `test_there_is_no_bulk_delete` pins the route table so adding one
has to be a decision somebody takes on purpose.

**A bulk move is the single move, applied N times, and that is load-bearing.**
Rule 4 (stage change → text the customer) fires once per opportunity that
*actually changed stage*, and **not at all** for one already sitting in the
destination — "your job is now at Inspection" arriving because a card happened to
be inside a selection is a message the customer should never have received. The
response separates `moved` from `unchanged` and reports the automation outcome per
id, including suppressions (DND, no phone), and the bar repeats those numbers
instead of claiming it moved everything it was given.

**A partly valid selection is refused whole**, having written nothing: applying the
half that resolved leaves the user unable to tell which half landed, and re-running
it is then not safe. Duplicate ids in a selection collapse rather than applying
twice.

**Selection is narrowed to what the board is showing.** Changing the status filter,
the search or the pipeline drops those rows out of the selection, so a bulk action
can never touch a record the user cannot currently see — and it is what makes
"the export contains exactly the ticked rows" true.

**`frontend/src/lib/csv.ts` imports nothing**, like `calendarGrid.ts`, so
`backend/tests/test_csv_export.py` runs the real module under node and asserts the
file rather than the source that writes it. Money is written as a plain decimal
(`9500.01`, not `$9,500.01`, which Excel imports as text) using integer arithmetic,
for the same reason `centsFromDollars` avoids floats. Fields beginning `=`, `+`,
`@` or a tab are prefixed with an apostrophe — a contact named `=cmd|...` is a
name, not an instruction — while `-` is deliberately left alone, because a negative
number is a legitimate value and far more common here than a leading minus sign in
a title.

### The ⋯ menu and the saved-list row (OUR design)

All four ⋯ items were `<div>`s with `cursor: not-allowed` and one shared tooltip,
and the `+ List` / "Open opportunities" row was two static labels. Three of the
four now do something; the fourth deliberately still does not.

- **Export** — CSV of **the current filtered set**, which is what an Export sitting
  next to the filters has to mean. Client-side, from rows already on the page;
  shares `lib/csv.ts` with the Bulk Actions export so there is one CSV writer and
  one set of escaping rules.
- **Manage smart lists** — opens the saved-list manager.
- **Dashboard insights** — opens the Dashboard. `OpportunitiesPage` now takes an
  `onNavigate` prop from the shell. There is still no router (DECISIONS.md), so
  this is a view switch and not a URL.
- **Restore opportunities — LEFT DISABLED, with a title saying exactly why.**

**Why Restore stays dead.** It implies a trash, and **this codebase has no soft
delete**: `DELETE /api/contacts/{id}` and `DELETE /api/opportunities/{id}` remove
rows, and a delete is a delete. Building it is not a menu item, it is a data-model
change — a `deleted_at` on `opportunities`, every read path in the API, the CLI and
the board taught to exclude it (miss one and deleted deals reappear on a report),
`DELETE` rewritten to soft-delete with a separate hard delete behind it, a
retention rule so the table does not grow forever, and a decision about what
happens to the telephony join key `custom_fields.owen_call_id` while a deal is in
the bin. That is its own task with its own tests, and inventing it as a side quest
is how a schema grows a `deleted_at` that half the queries forget to filter on.
`test_restore_opportunities_stays_dead_and_says_exactly_why` also asserts no
soft-delete column was quietly added to `models.py`.

**Saved views (smart lists).** New table `saved_views` (migration
`79c22cf0590c`), holding exactly the filters the board has: a pipeline, the status
filter and the search term. Nothing speculative — a saved filter for a control
that does not exist would be a promise the board cannot keep.

- **Shared, not per-user.** Four people, one company; "the list Owen made" is the
  useful thing. `created_by_id` records who made it and is not an ownership check.
- **`pipeline_id` is nullable.** A view that names a pipeline switches the board to
  it; one that does not only re-filters, so "Won deals" is useful on whichever
  board is open.
- **"Open opportunities" is NOT a row.** It is the board's default state, drawn as
  a built-in chip. So there is nothing to delete, and no seeded row that can drift
  out of step with the code.
- **Duplicate names are allowed**, consistent with the two stages both called
  "Call Back": this codebase resolves by id everywhere, and a uniqueness rule here
  would be the only place that disagreed.
- **Roles: read ANY_USER, create/rename STAFF, delete ADMIN** — the standing rule
  in CLAUDE.md (everyone reads, staff write, admin deletes), applied to a shared
  object where deleting one takes another person's list away. Delete also asks
  twice. **This is a judgement call the owner can overrule**: if a dispatcher
  tidying their own lists should not need an admin, move the DELETE to STAFF.
- `frontend/src/lib/savedViews.ts` imports nothing, so `test_saved_views.py` runs
  the real `applyView`/`matchesView` under node. The failure that guards against is
  a view that resolves back to the filters already in effect — nothing on screen
  would look wrong.

### Pipelines and stages are now USER-EDITABLE (2026-09-10) — read this one

**This supersedes the implicit assumption everywhere above that the pipeline
structure is fixed.** Until today there were **no write endpoints for pipelines or
stages at all** — no POST, no PATCH, no DELETE. The structure came from the seed
and could only be changed by editing the database. On the owner's instruction it
is now editable from the Opportunities > Pipelines tab:

    POST   /api/pipelines                          create a pipeline
    PATCH  /api/pipelines/{id}                     rename it
    DELETE /api/pipelines/{id}                     only when EMPTY
    POST   /api/pipelines/{id}/stages              add a stage (appended)
    PATCH  /api/stages/{id}                        rename a stage
    DELETE /api/stages/{id}                        only when EMPTY
    POST   /api/pipelines/{id}/stages/reorder      reorder the columns

**Consequence, binding: the stage list is no longer guaranteed to match the
measured GHL capture.** The table under "Known measurement gaps" — 10 stages, the
`Approved- Repair Schedule` typo, the two "Call Back" stages — records what GHL
held on 2026-08-13. From today it is a **historical measurement, not a description
of our schema**: anyone may add, rename, reorder or remove a stage, and a
divergence between our board and that table is now expected rather than a defect.
Nothing should assert against those stage names. (`capture/build/verify.py`
already asserts pipelines *exist* rather than pinning a stage count — that choice
now covers this too.)

**The delete rule, which is the whole safety story:**

- **A stage can only be deleted while it holds ZERO opportunities.** A populated
  stage is refused **409**, and the message names the count so the admin knows
  what to move. **There is no `force`, at any role, by any query parameter.**
- **A pipeline can only be deleted while it has ZERO stages and ZERO
  opportunities.** `Pipeline.stages` cascades delete-orphan, so a pipeline delete
  that ran with stages present would take the columns and every deal in them.
  Empty means empty: the columns are somebody's configuration too.
- **Nothing here ever deletes a deal.** CLAUDE.md warns specifically never to
  delete opportunities as a side effect of tidying something else, because
  `custom_fields.owen_call_id` is the telephony project's live join key. A
  cascading delete on this screen would break attribution history in a separate
  production system.
- A saved view or a calendar pointing at a deleted (empty) pipeline is **detached**
  rather than blocking it — both columns are nullable and carry no data of their
  own — and the response names what it detached, so it is not a silent side effect.

**Reordering moves columns, never deals.** `POST .../stages/reorder` writes
`Stage.position` and nothing else; no opportunity's `stage_id` is touched. It
takes the **whole stage list as a permutation** — every stage of that pipeline
exactly once — so a board that changed underneath the user is refused outright
instead of half-applied, and a stage from another pipeline cannot be smuggled in.
`test_reordering_stages_moves_no_opportunity_between_stages` compares the entire
`opportunity -> stage` map before and after, not a sample.

**Renaming does NOT make names unique, and that is deliberate.** The measured
pipeline holds **two distinct stages both called "Call Back"**, `resolve.pick` in
the CLI exits **5 (ambiguous)** rather than guessing between them, and this
codebase resolves by id everywhere. Renaming one stage to match another is
allowed, and `test_renaming_does_not_make_stage_names_unique` asserts it stays
allowed — a uniqueness rule added here would silently retire the CLI's exit 5.
**Making names unique is a product decision for the owner, not a cleanup**; if it
is ever wanted, it changes the CLI's contract and needs its own note here.

**`PATCH /api/stages/{id}` renames and nothing else.** There is no `pipeline_id`
in the body on purpose: moving a stage to another pipeline would carry every deal
in it across, which `PATCH /api/opportunities/{id}` already refuses as a
cross-pipeline move.

**Role: ADMIN for all seven endpoints.** Renaming a stage changes a label four
people navigate by and that the `ghl` CLI resolves against; deleting one removes a
column of the board. The Pipelines **tab stays open to every role** and the panel
disables the controls a non-admin cannot use, each with a title saying why —
hiding the structure from the people who work it daily would be worse, and a form
that 403s on submit is the thing `d1f7c50` / `b943f4b` ruled out. A dispatcher can
still move a card; the gate is on the structure, not on the deals.

**Not done, on the record:** the `ghl` CLI has **no** pipeline-management commands.
Every endpoint above is reachable with a PAT, but there is no `ghl pipelines
create` / `stages rename` / `stages reorder`. If pipeline structure should be
scriptable, that is a separate task.

### Which of these screens are OURS, in one list

Measured (do not restyle): the **Opportunities board** — column pitch, cards,
headers, drag — and the toolbar around it.

OUR design, no capture behind any of them, none may be cited as parity:

| Screen | Where |
|---|---|
| Forecast tab | `components/ForecastPanel.tsx` |
| Bulk Actions bar and the selection mode | `components/BulkActionsBar.tsx` |
| Saved lists row and Manage smart lists | `components/SavedViews.tsx` |
| Pipelines tab | `components/PipelinesPanel.tsx` |

Re-measure all four if a live GHL session is ever opened again; where GHL differs,
GHL wins on structure, exactly as everywhere else.
## Phone numbers — store E.164, and never block a save (2026-09-10)

Two decisions, made a day apart, and the second one overrides part of the first. Both are
recorded here because the code carries only the end state.

**2026-09-09 — store E.164, display formatted.** `+18135550102` in the row,
`(813) 555-0102` on the screen. `backend/app/phones.py` does it, `phone_display` is
computed by the backend so formatting lives in one place rather than in every client.

- A bare 10-digit number is **US (+1)**. The business is in Bradenton FL.
- A number that already carries a `+` **keeps its own country code**. The owner's own
  mobile is Venezuelan (`+58…`); re-homing it to +1 would break it. `00` is accepted as
  a synonym for `+`.
- **No `phonenumbers` dependency.** ~5 MB of worldwide metadata for two rules in a
  single-tenant CRM for one US company. The stated cost: a `+<cc>` number is
  length-checked, not validated against its country's numbering plan.

**2026-09-10 — a phone number can never stop a contact being saved.** As first written,
`POST /api/contacts` and `PATCH /api/contacts/{id}` answered **422** for a number that
would not parse. That is the wrong trade for this business: staff enter numbers standing
on a roof with a customer talking at them, and a refusal at that moment does not produce
a better number — it loses the number entirely. An oddly-formatted number in the record
beats no number in the record.

So the write path is `store_phone`, which **cannot raise**:

- parses → stored E.164, rendered formatted;
- does not parse → **stored exactly as typed** (trimmed like every other field), the save
  succeeds, and `phone_warning` carries a sentence naming what we could not read. The
  panel shows it in amber *under* the saved value. It is a note beside the number, never
  a wall in front of it.

`normalize_phone` still raises `InvalidPhone` — it is the parser, not the write path, and
its messages are what `phone_warning` says. Do not wire it back into a validator.

**No backfill, still.** Production holds 13 real contacts with hand-typed numbers like
`(941) 555-0100`. Writes only; nothing rewrites an existing row, and an unrelated PATCH to
another field leaves the stored number byte-for-byte as it was (pinned by
`test_an_existing_row_is_left_exactly_as_it_was`). A backfill is a separate decision the
owner has not made.

**Consequence to be aware of when a real transport lands.** `LoggingTransport` transmits
nothing, so today an unparseable number costs nothing. The day a real SMS transport is
wired in, a number stored verbatim is one the transport may not be able to dial —
`phone_warning` is deliberately the same signal that would drive suppression, and the
existing "missing-phone suppresses customer-facing sends" rule is where that would hang.
## Finding a contact by phone — the last ten digits, both sides (2026-09-10)

Companion to the storage decision above, and deliberately a separate one: that section
says what a number **is stored as**, this one says when two numbers **are the same
number**. They meet in the fact that the database now holds both formats at once.

**The rule: compare the last ten digits.** `8135550102`, `(813) 555-0102`,
`813-555-0102` and `+18135550102` are one person. This is not a new convention — it is
the identity rule the telephony project on owen-main already uses to match an inbound
caller to a contact, and two systems that share a database must not hold two different
answers to "is this the same line".

It is applied in the **shared** `/api/contacts?q=` search (`backend/app/phone_match.py`),
not in a search of the picker's own, so the Contacts list got the same fix. Before it,
typing a number the way a phone displays it was the one shape that never worked.

Three limits, chosen rather than discovered:

- **Only a phone-shaped query is digit-matched** — digits and phone punctuation, at least
  four of them (`MIN_MATCH_DIGITS`). `Maria` must never be stripped to `""`, because an
  empty match key matches every contact in the database. Four digits is the shortest
  fragment people actually quote: "the customer ending 0102".
- **The digit clause is ADDED to the existing ILIKE terms, never substituted for them.**
  A number stored as a carrier email address (`8635550123@vtext.com`) is still findable,
  and a business really named `24/7 Roofing` still matches its own name.
- **Digits are extracted with nested `REPLACE`, not a regex**, because SQLite has no
  `regexp_replace` and the tests must get the same answer as Postgres. The cost: a stored
  number carrying letters is not digit-matched. `phones.InvalidPhone` refuses extensions
  at the write path anyway.

**This defeats the index on `contacts.phone`** — a function of the column cannot use a
plain b-tree — so a phone search is a sequential scan. Measured against the actual sizes
that exist: 13 rows in production, 268 in the local synthetic set. If contacts ever reach
a size where this matters, the fix is an expression index on the same expression, not a
different rule.

**The browser holds a second copy of the rule** (`frontend/src/lib/phoneMatch.ts`) and
that is deliberate, with a guard rail. It never re-runs the search — the server owns that
— but it answers two questions asked before anything is written: does a prefill go in the
phone field or the name field, and is this number already on file. The two
implementations are pinned to each other by `test_contact_picker.py`, which runs the same
cases through node and through Python and compares the answers.

## The contact picker is shared, and adopted in exactly one place (2026-09-10)

`frontend/src/components/ContactPicker.tsx` is the one picker: search, a `+` that opens
**the real `AddContactDialog`** (lifted out of `ContactsPage` for the purpose — one
definition of what a contact needs, pinned by a test asserting `createContact(` appears in
exactly one `.tsx`), auto-selection of what it created, and a named confirmation.

**Adopted only in the Add-opportunity dialog.** The appointment create dialog and the
global search have pickers of their own; they were left alone on purpose because those
files were being edited on other branches at the time. They are the next two adopters, in
that order, and neither needs a change to this component.

**The duplicate guard warns, it does not block.** If the number being entered is already
on file the dialog names the contact and offers to select it instead — and the Create
button stays live. Two people genuinely share one number: a couple, or a property manager
who is the contact for a dozen addresses. A guard that refuses would be wrong more often
than it would be right, and the dispatcher is the one who knows which case this is.

## AMENDMENT (2026-09-11): the outbound transport is no longer UI-only

**This overrides the "SMS and email are UI-only in v1" decision recorded against
the transport seam, and the CLAUDE.md line saying `LoggingTransport` is the only
`MessageTransport` implementation.** It is an amendment rather than a reversal
because the seam itself is unchanged and was built for exactly this: a second
class, no UI change. What changed is that the second class now exists.

There is a real phone number. `+19544829099` is a BulkVS DID bound to this CRM
through a new module on owen-main (`backend/app/integrations/crm/`), which exposes
`POST /api/crm-link/{calls,messages}` on the internal Docker network. The
Conversations composer, which had never been able to send — the send button was
literally `disabled` — now goes through it.

### Nothing leaves the building until somebody arms it

`get_transport()` returns `CrmLinkTransport` only when **both** `CRM_LINK_BASE_URL`
and `CRM_LINK_API_KEY` are set, and otherwise returns `LoggingTransport`. Neither
is set in any environment that exists today, including production, so merging this
changes no behaviour anywhere. Arming it is a deliberate act of configuration; the
two variables are documented in `.env.example`, and `GET /api/health` reports which
transport is live so the question has an answer from outside.

The resolution is per send, read from the environment, not frozen at import — so a
test (and an operator) can turn it on and off without restarting anything.

### SMS is DARK on the far side, and a refusal is the correct answer today

owen-main refuses every SMS three times over: its own `CRM_LINK_SMS_ENABLED` is
false, the DID's `sms_enabled` is false, and the 10DLC campaign is SUBMITTED rather
than approved. A send today therefore comes back 403 and is recorded `REFUSED` with
the reason in words. That is the system working. The day 10DLC is approved, nothing
in this repository changes.

### `delivery_status` is now a lifecycle, and LOGGED_ONLY stays visible

The owner's requirement was "i want to know if the text arrived or not", which a
boolean cannot answer. `DeliveryStatus` gains `QUEUED` and `REFUSED`:

    QUEUED -> SENT -> DELIVERED | FAILED        the live ladder
    REFUSED                                      it never left, and we know why
    LOGGED_ONLY                                  recorded, never transmitted

`REFUSED` is deliberately not folded into `FAILED`: nothing is broken, and a
refusal will not fix itself on a retry the way a failure might. `LOGGED_ONLY` stays
a distinct, visible state so a stubbed message and a real one can never look the
same in one thread. The ladder is **forward-only** (`models.advance_delivery`),
mirroring owen-main's `services/sms.OUTBOUND_STATUS_RANK` — two systems ranking the
same ladder differently would disagree about a message's final state.

A `REFUSED` send **writes a row**. That differs from a DND or no-phone suppression,
which still writes nothing: there our own rule stopped the send before anything was
attempted, so there is nothing to show. Here the operator typed a message and
pressed send, and the message plus the reason it did not go is exactly what they
need on the thread.

### `POST /api/events` accepts a phone number, and creates the contact

It used to require `contact_id` and 404 on anything else. owen-main's
`client.resolve_contact_id` searches our contacts and, when nothing matched,
**dropped the event** rather than post one it knew we would refuse — so a
first-time caller, the new roofing lead, reached nobody. `from_number` (alias
`caller_number`, owen-main's own field name) is now accepted instead: matched to a
contact on the last ten digits, and a contact is **created** when nothing matches,
named by the number.

The explicit-`contact_id` path is byte-for-byte unchanged, including its 404, so
the path owen-main uses today is untouched.

Creating a contact per inbound call is the "hundreds of junk contacts" failure
owen-main's own notes warn about. The judgement here went the other way, for this
business: a roofing lead that rings once and is never recorded is a lost job, and a
contact carrying a real number is recoverable while a dropped event is not. The
last-ten-digits rule means a repeat caller resolves to the existing row rather than
accumulating duplicates.

### Known gaps on the far side, not fixable from this repository

Both were verified by reading owen-main's source, not assumed:

- **Delivery receipts are not relayed.** owen-main's
  `/webhooks/bulkvs/message-status` updates its OWN `messages` row and returns 200.
  It posts nothing onward. `POST /api/events/delivery` is the CRM half and is
  built, tested and ready; the forwarding call is a change to owen-main.
- **Inbound SMS is not pushed, and neither are outbound call outcomes.**
  `push.report_call_phase` is called from exactly three places, all inside
  `integrations/crm/handler.py` — the bound-DID **inbound call** handler. No message
  path pushes to the CRM link at all, and a call placed via `/api/crm-link/calls`
  produces no follow-up event. This is why a click-to-call is recorded with
  `call_status` NULL: we know a call was placed and will never be told how it ended.

## The browser softphone is OURS — no GHL capture exists (2026-09-11)

**GHL's softphone was never captured on the live account.** Placing or answering a call
there is forbidden by the safety contract, the power-dialer is a separate micro-frontend
nobody opened, and nothing under `captures/` describes an incoming-call card, a dial pad
or a registration indicator. Everything in `frontend/src/components/Softphone.tsx` and
`frontend/src/lib/softphone*.ts` is therefore **our own design**, and must never be cited
as parity. `capture/diff.py` and `diff_panel.py` do not cover it and should not be
extended to; there is nothing on the other side to compare it against.

The requirement it serves is the owner's, in his words: *"i should be able to answer from
the crm or the other 2 phones, the one that picks up first, takes the call."*

### What was already true, and was not rebuilt

`+19544829099` is bound to the CRM in the telephony project, and OWEN's
`integrations/crm/ring.py` already rings both mobiles **and** every
`PJSIP/operator-<slug>` browser softphone in parallel, hangs up every losing leg *before*
it bridges the winner, and records the bridge. First-answer-wins is implemented, tested
(`test_first_to_answer_is_bridged_and_the_rest_are_torn_down`) and live.

So the CRM needed to become one of those `operator-<slug>` endpoints — nothing more.
**Answering in the browser does not cancel the mobiles from this end**, and no code here
tries to: that would be a second, competing answer to a question already settled in the
place that owns the call.

### The decisions taken, and what each rejects

- **A CRM user maps to an OWEN operator BY EMAIL**, through OWEN's own `operator_slug`.
  The owner's call. The alternative — a mapping table in this database — was rejected
  because the slug already names a `pjsip.conf` section and an ARI dial string, so a
  second source of truth could only ever disagree with the phone system.
- **The operator must be provisioned; we never invent one.** OWEN refuses an email that
  is not on `CRM_LINK_SOFTPHONE_OPERATORS` (empty grants nobody). Minting off the slug
  rule alone would hand a browser a well-formed credential for an endpoint that exists
  nowhere: it would register into a SIP 401 and could never be rung, while the UI said
  it was ready.
- **The CRM-link machine key never reaches the browser.** `POST
  /api/softphone/credentials` in this app is a proxy that holds the key; the browser
  authenticates with its ordinary session cookie. The key can also place calls and send
  texts on a bound DID.
- **The identity is the session's email and is never a parameter.** An operator endpoint
  is a ring destination, so "register as another user" would mean "answer their calls".
- **Registration state is only ever reported from a SIP registration callback.** Not from
  "we called `register()` and it did not throw". This browser is one leg of a ring group
  whose other legs are two mobiles: if it silently stops being registered, the call still
  rings the mobiles and nobody discovers the desk was dead. The dock is always on screen
  and says which of eight states it is in.
- **One tab at a time, elected between the tabs.** The operator AOR is `max_contacts = 1`,
  `remove_existing = yes`, so a second tab *silently evicts* the first — which would leave
  a tab showing "Ready for calls" that can never ring again. A BroadcastChannel lease makes
  the eviction visible and deliberate ("Open in another tab" + "Use this tab").
- **The microphone is asked for before registering, not when the phone is ringing.** A
  blocked microphone does not stop a registration, so without the probe the failure lands
  as a rejected call that reads like a dropped one.
- **Default ON, remembered per browser.** A phone somebody has to switch on every morning
  is a phone that is off when the call comes in. Switching it *off* is the deliberate act.
- **In-call controls are hang up and mute, and nothing else.** Hold, transfer and DTMF are
  all backend/ARI operations in the telephony project; the browser never talks to ARI, and
  a v1 that shipped buttons driving a seam this app does not own would be a promise we
  cannot keep.

### Not verified, and cannot be from here

**No real call has been placed.** Everything above is proven by unit tests, fakes and a
type-checked build. What remains unproven until a human dials `+19544829099` with
`CRM_LINK_ENABLED=true` and a provisioned operator: that the browser registers against the
live Asterisk, that the INVITE's caller-ID parses into the name the card shows, that media
flows through coturn, and that answering in the browser really does stop the two mobiles.
The full list is in `.qa/state/softphone-done`.

## The Conversations inbox: unread, and a thread can now be deleted (2026-09-11)

Three loose ends the owner reported, and the product decisions taken while
closing them. None of these overturns a locked decision; the third adds a
destructive capability that did not exist, which is why it is written down.

### "Unread" now means a CONVERSATION, wherever it is counted

The badge on the Unread **tab** was the sum of `unread_count` across every
conversation, while the tab it labels filters conversations. One thread holding
two unread texts therefore showed "2" above a list of one row. It is now a count
of rows — `unreadTabCount` in `frontend/src/lib/inbox.ts`, the same predicate
`GET /api/conversations?tab=unread` applies on the server (`unread_count > 0`),
so the badge and the list it labels cannot be computed from different rules.

**The per-row badge keeps the message total, deliberately.** There it labels one
conversation, and "2" means two unread texts — which is what the operator needs
before opening it. That is the one place the sum is the right number.

### Opening a thread marks it read, and so does a message ARRIVING on an open one

`PATCH /api/conversations/{id}` has accepted `{"read": true}` since the inbox was
built and `ghl convos read` has always used it. Nothing in the browser ever
called it, which is the whole of the reported bug.

The judgement call is the second half, and it is **overrulable**: when a new
inbound message lands on a thread that is already open, the badge does **not**
come back. "Unread" has to mean nobody has seen it, and somebody has — the
message is on screen beside them, within the ten-second poll. Letting the badge
reappear on the thread being read is the owner's original complaint arriving by a
different route.

The guard that makes that safe is **tab visibility**: a thread left open on a
background tab keeps its badge and clears it when the tab comes forward. Nobody
is reading a background tab. `shouldMarkRead(openId, unread, visible)` is the
whole rule, and it is one testable function rather than an inline `&&`.

**The mark-read is not optimistic.** The server is asked first and the badge is
cleared only if it agreed; a failed PATCH leaves the badge exactly where it was
and says why. An optimistic clear that silently rolls back is indistinguishable
from a badge that works, right up until somebody misses a customer's text.

**The auto-selected first row counts as opening.** Its thread is rendered in full
beside the list, so a badge on it would claim nobody had seen messages that are
on screen. There is no hover handler and no keyboard navigation in this list, so
"selected" and "opened" are the same event and nothing else can trigger it.

**The measured "New" divider is now drawn from a snapshot** taken when the thread
was opened, not from the live unread count — otherwise clearing the count would
have deleted a measured element from the screen by a side door.

### `DELETE /api/conversations/{id}` — ADMIN, and it deletes ONE thing

There was no supported way to remove a thread; the trash icon was labelled
`Delete Conversation (not implemented in v1)`, and demo threads had to be removed
straight from the production database. That is what this closes.

- **ADMIN only**, like the other two deletes. This is customer correspondence,
  and there is **no soft delete anywhere in this codebase** — see "Why Restore
  stays dead" — so it is gone when the call returns. A DISPATCHER and a TECH get
  the control **disabled with a title saying why**, never a form that 403s on
  submit (`d1f7c50` / `b943f4b`).
- **It deletes the conversation and its own events. Nothing else.** The contact
  stays, their opportunities stay attached — `custom_fields.owen_call_id` is the
  telephony project's live join key — and their appointments stay. `delete_contact`
  has to remove conversations explicitly because they are deliberately not
  cascaded from `Contact`; this is the same care in reverse. Only
  `ConversationEvent` cascades, and nothing else in the schema references a
  conversation.
- **The confirmation names the contact and the size of the thread**, because "are
  you sure" tells nobody anything about what they are about to lose. The row now
  carries `event_count` — the WHOLE timeline, not narrowed by the thread filter
  and not narrowed by the STAFF-only internal-note rule, because a delete removes
  every event and a count that hid the notes would under-report what is going.
- **There is no bulk delete and no `ghl convos delete`.** The first is the same
  call already made for contacts and opportunities ("a checkbox column is the
  fastest way ever invented to lose real customer records in one click"). The
  second is simply not built; the endpoint is reachable with a PAT if it is ever
  wanted.

### The Conversations icon rail — wired, and one row left dead (2026-09-11)

Six icons with labels and no handlers. The decisions taken, all overrulable.

**"Assigned to me" is `Contact.owner_id`, and `Conversation` gains no assignee.**
The owner's instruction, and it is the right shape anyway: a thread is
correspondence with a customer, and the customer already has an owner — the one
the contact panel shows. A column on `conversations` would be a second answer to
"whose is this" with nothing keeping the two in step, and it would need a
migration to say something the schema already says. A contact nobody owns is in
the team inbox and in nobody's own.

**Scope, tab and search intersect, and they narrow ONE query on the server.**
"My conversations, unread, matching Reyes" is a reasonable thing to ask for, and
each control answers a different question — whose, what state, which contact, in
what order. Doing any of it on the client would let the Unread badge and the list
it labels be computed over different sets again, which is the bug that opened
this work.

**The in-place search matches NAME and PHONE only** — exactly the two fields the
row on screen shows. Matching an email or a business name produces a row whose
reason for matching is invisible to the person reading it. The ctrl+K palette
already searches those and is the control for "find anything anywhere"; this one
narrows the inbox you are looking at. `phone_match` is reused, so a number typed
the way a caller ID reads it finds a row stored in E.164.

**"Unread only" and "Filters" drive existing state or nothing at all.** "Unread
only" writes the same `tab` the Unread tab writes and reads its highlight back
from it — a second `unreadOnly` flag would drift, and the copy nobody fixed would
win silently. **"Filters" is left DISABLED with its reason on hover**, because
the toolbar control it would have to share state with is *itself* unimplemented:
GHL's measured filter builder (Filter Type / Is / Value, AND/OR, Cancel/Apply)
does not exist here, so wiring the rail would mean inventing a second filter
model for it to own. Building the filter builder is its own task.

**The highlight is derived, never stored.** It was a `rail` index nothing else
read, so the first row sat permanently lit whatever the list was showing. Each
row now lights when the thing it names is actually applied, and more than one can
be lit because scope, search and the unread filter compose. `Conversations` is
always lit: it is the screen you are on, which is all the owner asked it to be.

**The measured list pane does not move.** The search box renders only while the
search is open, so the default screen is byte-for-byte the one under measurement;
the "Team inbox" title is left alone even when the scope is "assigned to me",
because it is measured text and the rail is the scope indicator.

## AMENDMENT (2026-09-11): a contact delete detaches appointments too, and refuses first

`DELETE /api/contacts/{id}` returned a raw **500** for any contact that had ever had an
appointment. Reproduced on production while clearing demo data:

    sqlalchemy.exc.IntegrityError: (psycopg.errors.ForeignKeyViolation)
    update or delete on table "contacts" violates foreign key constraint
    "appointments_contact_id_fkey" on table "appointments"

The endpoint reasoned about two of the three things that hang off a contact and was blind
to the third. It now reasons about all three, and the rule is one sentence: **a contact
delete destroys nothing that carries meaning of its own.**

| Hangs off a contact | What the delete does | Why |
|---|---|---|
| Opportunities | detached (`contact_id = None`) | `custom_fields.owen_call_id` is the telephony join key |
| Appointments | detached, pending reminders withdrawn | a booking is the record that a slot was taken |
| Conversations | deleted | nothing outside this record joins to them; `contact_id` is NOT NULL and nothing cascades them |

### Refuse first, detach on `force` — option (a), not a silent detach

The refusal is one 409 naming **both** kinds and every id, not one per kind: an admin who
clears the opportunities only to be refused again over the bookings has been made to play
whack-a-mole with a confirmation dialog.

`force=true` then detaches both. Rejected alternatives, on the record:

- **Detaching appointments silently, with no 409.** `appointments.contact_id` is nullable,
  so this was available and is what SQLAlchemy's default relationship behaviour would have
  done on its own. Rejected: an appointment is a commitment to a customer and somebody may
  still be driving out to it. Severing one is a deliberate act. It is also the existing
  convention on this endpoint — opportunities have always been refused, never quietly
  detached.
- **Deleting the appointments under `force`.** Rejected for the same reason
  `DELETE /api/appointments/{id}` cancels rather than deletes: GHL's Appointment report has
  a Cancelled tile, so a booking is a record. A detached appointment keeps its title, slot,
  calendar, assignee and notes, and the calendar still draws it — without a customer name.
- **Cancelling future appointments under `force`.** Not done. It is a product decision
  about what a deleted customer's diary should look like, not part of fixing a 500, and it
  is the owner's call. The status is left exactly as it was.

The pending reminders **are** withdrawn, through `_drop_pending_reminders()`, releasing the
dedupe key like every other retire path. `_h_appointment_reminder` already skips a booking
whose contact has gone, so nothing could have been sent — but "could never send" and "is
not queued" are different things to the dispatcher reading `ghl jobs list --status
pending`, which is the same distinction `cancel_appointment` already draws.

### `DELETE /api/appointments/{id}` still cancels, and now says so in the body

The behaviour is unchanged and still correct. What changed is that only the docstring knew:
the response now carries `deleted: false` beside `status: "cancelled"`. **No hard delete
was added.** If one is ever wanted it should be a separate, named endpoint, not this route
quietly changing meaning.

### The whole class of bug, audited (every `@app.delete` route)

Driven with foreign keys **enforced**; `PRAGMA foreign_key_check` empty afterwards in all
cases. Nothing else answers a delete with a raw `IntegrityError`:

- `/api/contacts/{id}` — fixed here.
- `/api/pipelines/{id}` — already this exact shape: refuses unless empty, detaches the
  nullable `saved_views.pipeline_id` and `calendars.pipeline_id`, names both in the body.
- `/api/stages/{id}` — refuses while opportunities hold it; nothing else points at a stage.
- `/api/opportunities/{id}`, `/api/saved-views/{id}`, `/api/contacts/{id}/tags/{tag_id}` —
  leaves. Nothing carries a foreign key to them.
- `/api/appointments/{id}` — a status change; deletes no row.
- `/api/auth/tokens/{id}` — revocation; sets `revoked_at`, deletes no row.

**The gap is in what does not exist.** There is no delete endpoint for **users**,
**calendars** or **tags**, and each would land in this class the day one is added:
`api_tokens.user_id` and `contact_tags.tag_id` are NOT NULL (so a user or tag delete has to
decide, not detach), while `appointments.calendar_id` is nullable (a calendar delete can
detach, the way pipelines do). Not built — adding a delete endpoint nobody asked for is a
bigger change than the bug being fixed.

### SQLite hides this class of bug from the test suite

`PRAGMA foreign_keys` defaults to **off**, so the production failure cannot reproduce
itself here by accident: against the pre-fix code, the same delete "succeeded" and silently
left an appointment pointing at a deleted contact. `test_delete_with_appointments.py` turns
the pragma on for the tests that need it — and asserts the pragma really is on first, so
the guard cannot rot into a test that proves nothing. It was **not** turned on globally in
`app/db.py`: that is shared infrastructure several branches would each have to edit, the
same reasoning already recorded for the shared-`TMPDIR` hazard above.

## The Workiz import — the real business data arrives (2026-09-11)

The owner is migrating off Workiz. Two exports (856 clients, 389 jobs) are on the
build machine at `~/workiz/`, chmod 600, holding REAL customers' names, phone
numbers, email addresses and home addresses. `backend/app/workiz_import.py` reads
them; `backend/tests/test_workiz_import.py` covers it against fixtures, never
against the export.

    uv run python -m app.workiz_import                # DRY RUN. Writes nothing.
    uv run python -m app.workiz_import --commit       # ...and this one writes.

### It is a module, not a `ghl` CLI command, and that is the whole safety story

The CLI talks to the API over HTTP, and **every write endpoint it would use fires
an automation**: `POST /api/appointments` schedules T-24h and T-1h reminder texts,
`POST /api/contacts` notifies the team, a stage change texts the customer. An
import through the front door would queue hundreds of messages about roofs that
were fixed in March. So it takes the `app.bootstrap` shape — argparse, `SessionLocal`,
whatever `DATABASE_URL` points at — and writes through the ORM.

**No reminders, ever. Three guarantees, deliberately not one:**

1. The module does **not import `app.automations` or `app.queue`**. There is no code
   path from an import to `enqueue()` for a future change to find. This is why
   `_thread_for` is four lines of its own rather than `automations.thread_for` —
   the duplication is the price of the guarantee and is a deliberate purchase.
2. The `jobs` table is **counted before and after, inside the transaction**. One new
   row rolls the entire import back and exits non-zero. That one holds even if
   something underneath this file starts enqueueing on its own, which an import
   check cannot.
3. Appointments are written through the ORM, never through the endpoint that books.
   There are no SQLAlchemy event listeners anywhere in `app/` for one to hide behind.

Asserted on the queue itself, including a test that makes the import queue a row on
purpose and proves nothing at all was written.

**Only FUTURE jobs become appointments.** 381 of the 389 `Scheduled` datetimes are in
the past. Back-dating a booking is the shape that would have produced the texts.

### AMENDS "Renaming does NOT make names unique" — for the data, not the rule

The section "Pipelines and stages are now USER-EDITABLE" says making stage names
unique "is a product decision for the owner, not a cleanup". **The owner has now made
it**, for the AHS board, while it was empty. The importer removes the duplicate
`Call Back` column and the empty near-duplicate `Submit Invoices`.

What has **not** changed, and must not: there is still **no uniqueness rule in the
API**. `PATCH /api/stages/{id}` will still happily rename one stage to match another,
`test_renaming_does_not_make_stage_names_unique` still passes, and the CLI's exit 5
(ambiguous) is intact for anyone who recreates the situation. This was a one-off fix
to *the data on one board*, not a constraint added to the schema.

The safety rules are unchanged and are re-checked at write time, not only when the
plan was built:

- A duplicate column holding deals is **renamed, never emptied**. Nothing in the
  importer moves or deletes an opportunity — `custom_fields.owen_call_id` is the
  telephony project's live join key (CLAUDE.md).
- `NEAR_DUPLICATE_STAGES` is the one judgement call, written down as a constant so
  removing an entry is a one-line veto. It only ever removes an EMPTY column.

### Routing: status -> MEANING -> that board's word for it

`Source` picks the board (AHS, or Retail for everything else), unconditionally, as
asked. A flat status->stage table then **cannot file six real rows**: four AHS-sourced
"Pending (Estimate Follow Up)" and two retail-sourced "In Progress (Request Approval  )"
— the trailing spaces are in the raw data — whose status has no column on the board
their source sends them to. So a status maps to a meaning and the meaning is spelled
in each board's own vocabulary (`STAGE_FOR`). Statuses match with all whitespace
stripped and case folded; one that is not in the table **skips its row and says so**
rather than being guessed at. 49 cancelled jobs become nothing at all.

Two judgement calls in that table, both overrulable in one line: AHS
"Pending (Estimate Follow Up)" files under **Call Back** (chasing a decision is a
callback; the alternative reading is "Request the Approval (AHS)"), and a **won** deal
lands in its board's invoice column because that is where the work ended.

### `workiz_*` is a reserved namespace alongside `owen_*`

`RESERVED_PREFIX` became `RESERVED_PREFIXES`, checked in the one `is_reserved()` that
already existed — the guard was extended, not duplicated. A user-defined field whose
**derived** key lands in either namespace is refused, so "Workiz ID" is refused as
well as `workiz_id`.

`workiz_*` is **stricter than `owen_call_id`**, which may still be set where there was
none: these are read-only in every direction — not set, not changed, not removed.
`workiz_id` is the whole of the import's idempotency (the `Client #` on a contact, the
`Job #` on an opportunity); edit one by hand and the next import stops recognising that
record and creates a second copy of the customer. An unchanged echo is still not a
write, so the detail form posting the whole blob back on every save keeps working.

`Contact.custom_fields` is not writable through the API at all, so the contact half of
the namespace needs no guard and deliberately did not get a decorative one.

### Contacts gained four address columns, and nothing backfills them

`contacts` had never had an address. `address_street` / `address_city` /
`address_state` / `address_postal_code`, all nullable, migration `e7a3d1c05f84` — four
`add_column` calls and nothing else. Four columns rather than one blob because the two
exports disagree about the shape: the jobs file already has City / State / Zip code
separately, the clients file has one combined string.

The parser's rule is **anything not confidently identified stays in `street`**, checked
as a property: over the real export, not one character of any address is dropped. The
clients file turns out to write `"<street>, Florida <ZIP>"` — the **state** where a city
would go, 754 times — so a US state list is what stops "Florida" landing in
`address_city` on three quarters of the database. The city comes from the jobs file's
own column, blank-fill only, never an override.

Read-only on `_contact_detail` and deliberately **not** on `ContactPatch`: the measured
Contact Details panel has no address control, and adding one is a change to the surface
parity is judged on. An address that is written and cannot be read would be worse than
either.

### Measured against the real export, and it reconciles

856 client rows -> **821 contacts** (27 phone numbers shared by 62 records; 35 rows
collapse), **331 Customer / 490 Lead**. 389 job rows -> **340 opportunities** (49
cancelled, **0 skipped, 0 unrouted**), **238 won**, **$301,219.62** to the cent.
5 future appointments, 335 past jobs booked nothing. **0 rows in `jobs`.**

Re-running `--commit` a third time leaves a SHA-256 of every row of every table
byte-identical. A dry run leaves the database file byte-identical.

### Merging is on the last ten digits, and nothing is discarded silently

The identity rule the picker, the contacts search and the telephony project already
share. Most-complete record wins, its blanks fill from the others, and every name,
email and address that **lost** is written into a STAFF-only `NOTE` on the surviving
contact naming the `Client #` it came from — rewritten in place on a re-run, never
duplicated. A phone with fewer than ten digits is **its own group**: an empty match key
is how four strangers become one person.

Where a job's phone and its client name point at different records, **the phone wins**
and the row is listed under "Conflicts a human should look at". One real row does this.

### Deliberately not done

- **`Tech` creates no users**, by the owner's decision. `Tags`, `Created by` and
  `Job origin` are not mapped either; `Lead Created Date` is empty in all 389 rows.
- **The importer has never been run against production.** It is built, tested locally,
  and stopped there. A human runs it after reviewing a dry run.
- It **never deletes** a contact, an opportunity or an appointment, and never deletes a
  stage that holds deals.

### It depends on `feature/opportunity-fields`, which was not on main

That branch (`e163cbb`) supplies `CustomFieldDef`, the `owen_` guard this one extends,
and `appointments.opportunity_id`. It was **not merged into origin/main** when this was
built, so it was merged into this branch instead — designing against it in the air would
have meant either a second custom-field system or untestable code. **It carries no
Alembic migration of its own**, so `alembic upgrade head` does not create
`custom_field_defs`, `custom_field_pipelines` or `appointments.opportunity_id`.
`workiz_import.preflight()` refuses to run and names what is missing rather than failing
half-way through writing 800 contacts. That migration is owed by that branch, and is the
first thing to check before running this for real.

## Custom fields are the owner's own job questions (2026-09-11)

The opportunity detail carries questions the owner creates, edits and archives
himself — "How many stories?", "Where is the leak located?", "How old is the
roof?", "What type of roof?". `Opportunity.custom_fields` had been a free-form
JSON blob since the beginning with **no definition table anywhere**: nothing said
a field existed, what type it was, or what its options were, and the four `owen_*`
keys were visible only because the telephony project writes them into that blob.

**The definitions describe; the blob still stores.** `custom_field_defs` says a
field exists; the answers stay exactly where they already were. There is no
answers table and no data migration, which is why nothing in this work can lose
an answer that already existed.

### The owner's decisions, implemented as given

- **Attached to OPPORTUNITIES, not contacts.** "How old is the roof" is a fact
  about this job; the same customer calling back next year gets their own
  answers. `CustomFieldDef.entity` exists so contact-level fields could be added
  later without a second table. **Nothing writes anything but `"opportunity"`**,
  and contact-level fields were deliberately not built.
- **Five types: text, number, dropdown (pick one), date, yes/no.** No
  multi-select — deferred by the owner, and not to be added without asking.
- **One definition attaches to SEVERAL pipelines** (`custom_field_pipelines`).
  Defining "Roof type" once is what keeps answers comparable across boards;
  "AHS claim number" stays on the warranty pipeline alone.
- **There is no `required` flag, and there is no column for one.** An inbound
  call at 2am must still become a deal; a required field would mean a missed
  lead.
- **Defining, editing and archiving is ADMIN**, matching the pipeline structure
  endpoints. The tab stays open to every role and the panel disables what a role
  cannot use, with a title saying why — never a form that 403s on submit
  (`d1f7c50`, `b943f4b`).
- **Deleting a field ARCHIVES it.** `DELETE /api/custom-fields/{id}` sets
  `archived_at` and returns `{"archived": id}`. **There is no endpoint anywhere
  in this app that destroys a recorded answer**, and none should be added: those
  values are things a customer told somebody on the phone.

### Judgement calls, all overrulable

- **An empty attachment set means NOWHERE, not everywhere.** The alternative
  reading is tempting — "Roof type is useful everywhere" — and was rejected:
  detaching the last pipeline would then silently turn a narrow question into a
  global one. The panel warns on screen when a field is attached to nothing.
- **`merge_answers` MERGES rather than replaces.** A key the client did not send
  keeps its value; clearing is explicit (send the key empty). This is what makes
  "a client posting a shorter object deletes nothing" structural rather than
  per-case. An undefined, non-reserved key is still stored verbatim — the column
  was free-form before this and stays that way — and `null` removes one, which is
  the only deletion path this module has.
- **An unchanged echo is never re-validated.** The form posts the whole object
  back on every save, so re-validating an echo would mean that retiring a
  dropdown option locked every deal already holding that option out of being
  saved at all.
- **The key is derived from the label ONCE and then frozen**, and the type cannot
  be changed at all. Both are what answers are filed under or against. The panel
  shows them as fixed text rather than as controls that do nothing.
- **Answers are on the detail form and the Add opportunity dialog, and NOT on the
  board card**, by the owner's instruction: the board is dense and has to stay
  scannable.

### The `owen_` namespace is now read-only in the form, not just guarded

`owen_call_id` keeps its 400 on a change — unchanged, and still the join key. The
other three (`owen_campaign`, `owen_tracking_number`, `owen_is_new_caller`) were
plain editable inputs in the detail form, and **any client posting a shorter
`custom_fields` object deleted them outright**, which would have broken call
attribution silently. Now:

- no definition may claim a key under a reserved prefix, and the check is on the
  DERIVED key, so labelling a field "Owen campaign" is refused too;
- **no write through `merge_answers` can drop a reserved key**, whatever it sends;
- all four render read-only in the form, in their own "From OWEN" block.

The guard is on the PREFIX, not on a list, so the three further `owen_*` fields
measured on the live account later — `owen_call_signal`, `owen_lead_trigger`,
`owen_signal_at`, recorded under "Measurement corrections" above — are covered
without being enumerated anywhere.

**They remain settable through the API** (`owen_call_id` excepted). That is
deliberate and is the one place this is looser than it could be: the telephony
project is the writer of those keys and must be able to correct attribution, and
`test_the_telephony_join_key_cannot_be_rewritten_through_the_detail_form` already
pins that the guard is not over-broad. **If the owner wants them frozen at the API
too, it is the same loop `workiz_*` already uses at the top of `merge_answers`,
pointed at `RESERVED_PREFIX` instead — and it is his decision**, not a cleanup.
(The Workiz import added `workiz_*` to the same tuple and took exactly that
stricter line: read-only in every direction.)

### Appointments are linked to opportunities, and a delete never cancels a visit

One nullable `appointments.opportunity_id`, readable from both ends: the deal
lists the visits booked for it, the visit names its deal.

- **Deleting an opportunity DETACHES its appointments.** Not a cascade, and not
  an accident of a nullable column — the endpoint nulls them deliberately and
  returns `detached_appointments`. A booked visit is a promise to a customer;
  tidying a deal record must not quietly cancel it. Same call `delete_contact`
  already makes about opportunities. It is also load-bearing: the column is a
  real foreign key, so on PostgreSQL the delete would raise rather than cascade.
- **The EXISTING create dialog is reused from both directions** rather than a
  second one being written. It already validates the times and schedules the
  reminders. From a deal the opportunity is locked and the contact and title
  arrive prefilled; from the calendar it is an optional picker of OPEN deals.
- **Reminder rescheduling was not touched.** Only `starts_at` and the status
  reach the queue; binding a deal is not a change to when the visit happens, and
  `test_linking_a_booking_does_not_touch_the_reminder_queue` compares the `jobs`
  rows before and after rather than reading the code.
- **Only open deals are OFFERED**, but the backend accepts any existing one: a
  booking already bound to a deal that is later won must keep its link, and the
  panel keeps the stored deal as an option so opening a booking cannot silently
  unbind it.

### The Custom fields tab is OURS

`components/CustomFieldsPanel.tsx` joins the Opportunities tab row beside
Pipelines, which is itself OUR design.

GHL's `/settings/fields` screen HAS been driven on the live account — the
CompanyCam checklist migration created 25 opportunity fields through
`capture/build/`, and that screen's own widget mechanics are recorded under "UI
mechanics worth keeping (`capture/build/panel.py`)" above. But **it was driven,
not captured**: there is no HTML, no screenshot and no computed style for it to
compare against, and this panel was not built from one. It is built from the
Pipelines tab's primitives, which is the in-repo idiom. So **nothing on this tab
may be cited as parity**, exactly like the four screens listed under "Which of
these screens are OURS", and if a live session is ever opened again that screen is
worth capturing properly. `components/CustomFieldAnswers.tsx` — used by BOTH the
detail form and the Add dialog, so the two cannot drift — is ours too.

### CORRECTION to "It depends on `feature/opportunity-fields`" above

That section says the branch **"carries no Alembic migration of its own"** and
that one is "owed by that branch". **It was delivered**: revision
`c2d2cc47e4ff`, "job questions on opportunities, and a booking's deal", which
creates `custom_field_defs` and `custom_field_pipelines` and adds
`appointments.opportunity_id`. It is chained after `e7a3d1c05f84` (the Workiz
address columns) and `alembic heads` prints exactly one head. So
`alembic upgrade head` now does create all three, and `workiz_import.preflight()`
no longer has anything to refuse on that account.

## OpenPhone is mirrored into the thread, READ-ONLY (2026-09-11)

The company runs a second phone system, OpenPhone, on a line separate from the
BulkVS DID `+19544829099` this CRM sends from. It is being migrated away from. The
owner's requirement is that its history and its new activity appear on the same
customer timelines this CRM already owns, so nobody has to keep two apps open — and
that it stay strictly one-way.

His words, and they govern everything below:

> *"i should not be able to text, call or answer from the crm using the quo phone
> number, just log everything live"*
> *"if i want to answer or text someone calling to openphone number we must use
> openphone platform"*

**The mirror lives in owen-main** (`app/integrations/openphone/`, branch
`feature/openphone-mirror`), which already holds the OpenPhone API key and already
has a delivery path into this CRM. It reads OpenPhone and posts to `POST
/api/events` exactly like any other telephony feed. **This repository has no
OpenPhone client, no OpenPhone credential and no OpenPhone code path**, and a test
tokenises every module under `app/` to keep it that way — `api.openphone.com` and
`OPENPHONE_API_KEY` may not appear in executable code here.

### What changed on this side, and why each was necessary

**`conversation_events.dedupe_key`, UNIQUE and opt-in.** A mirrored feed can deliver
the same object twice: OpenPhone has no time-based call sweep, so the poll re-reads
its whole window every tick, and owen-main's `crm_report` job retries on a timeout —
including one that happened *after* `POST /api/events` committed and before its 201
got home. owen-main cannot know whether the first attempt landed, so the check has
to be here.

It is a NEW column and **not** a unique index on `provider_ref`, which was the
obvious cheaper move and is wrong: owen-main deliberately sends the *same*
`provider_ref` on all three call lifecycle phases ("the join key must not depend on
which event survived"). Deduping there would collapse started/answered/ended into
one row and break the live BulkVS path on the first call after deploy. A test pins
that path at three rows.

A repeat delivery returns the **existing row**, not a 409. To owen-main a repeat
delivery succeeded — the event is on the thread, which is all it wanted — and a 4xx
would dead-letter a `crm_report` job for behaving correctly. The check runs *before*
the contact is resolved, because resolving CREATES a contact for an unknown number
and fires the new-lead automation; a retry would otherwise notify the crew about a
lead already on file.

**`occurred_at` on ingest.** Without it a 30-day backfill lands as a month of history
all dated today, in poll order, which is the opposite of the single timeline the
feature exists to produce. Two consequences are handled with it:

- **`last_event_at` is now forward-only.** It orders the inbox, so a three-week-old
  mirrored call must not drag a thread that was active this morning down the list.
- **Unread counts only a FRESH inbound.** A month of mirrored correspondence was
  already read and answered — in OpenPhone, which is where it happened. A badge of
  200 trains an operator to clear it without reading, which costs them the one
  message that was genuinely new.

**`source_system` / `source_number`, and a chip on every event that has them.** A
thread can now hold two phone systems at once. An operator who cannot tell them apart
cannot answer the question that decides what to do next: which number does this
customer know us by? NULL renders **no chip** — every row written before the mirror
existed has no observed source, and labelling those "BulkVS" would be writing a guess
into a customer's record to make the UI tidier. Rows written from now on are stamped:
mirrored ones by owen-main, outbound ones by `automations.SENT_SOURCE_SYSTEM`, which
reads the bound DID at send time rather than freezing it.

### THE BUG THIS FOUND, which would have texted real customers

`automations.on_inbound_call` auto-texts the caller back for any INBOUND CALL whose
duration is 15 seconds or less. It had no notion of *age*, and it did not need one:
until now every ingested event WAS "just now", because `occurred_at` defaulted to the
ingest clock and nothing could back-date it.

A 30-day backfill breaks that assumption completely. Every short or unanswered
OpenPhone call in the window is an inbound CALL under 15 seconds. Switching the
mirror on would have queued **one auto text-back per missed call** — to real
customers, about calls up to a month old, from a BulkVS number those customers have
never seen. Today `LoggingTransport` would record them rather than send them; the day
`CRM_LINK_BASE_URL` and `CRM_LINK_API_KEY` are set, every one becomes a real text.

`automations.FRESH_EVENT_SECONDS` (one hour) guards it, and the guard can only ever
*stop* a text nobody wanted: a live missed call reaches us seconds after it ends, and
an hour of slack keeps a delayed relay or a retried job firing. Both directions are
tested — a month-old call queues nothing, a live one and a ten-minutes-late one both
queue.

### Recordings stream through owen-main, and the tradeoff is accepted

A mirrored call's `recording_url` is `/api/openphone/recordings/<id>` — a path on
**this** server. The browser's `<audio src>` resolves it same-origin and sends the
operator's httpOnly session cookie, which is the only credential it has; an `<audio>`
tag cannot send an `Authorization` header, so a CRM-relative path is the only shape
that works at all. This CRM then asks owen-main with its existing machine key, and
owen-main asks OpenPhone with the OpenPhone key:

    browser --cookie--> CRM --X-OWEN-Key--> owen-main --OpenPhone key--> OpenPhone

The bytes are streamed rather than the URL redirected. A redirect would also keep the
API key safe, but it would hand the browser a `share.quo.com` URL that is forwardable
and outlives the session.

**Nothing is copied into this database, so cancelling OpenPhone breaks this audio.**
The owner accepted that explicitly. The thread entry, the call metadata and the
transcript all survive, because those ARE ours — only the recording stops playing.

### The composer never sends through OpenPhone, and says so

There is no OpenPhone send path in this product, **not even a disabled one** — a
disabled send path is a switch somebody eventually flips. A reply typed under a
mirrored message goes out on the BulkVS line, which from the customer's side is a
text from a number they have never seen, in the middle of a conversation they were
having with a different one.

So a thread containing a mirrored event shows a banner **above** the composer naming
both numbers and pointing at the OpenPhone app for a reply on that line. Above,
because the operator has to know before they type, not after. Only in threads that
actually contain one: a banner that is always there is a banner nobody reads.

### Polling, not webhooks — decided on owen-main, recorded here

Registering an OpenPhone webhook requires `POST /v1/webhooks`, and the signing secret
is returned only in that call's response. Both halves disqualify it: the POST is
exactly the write owen-main may not make against OpenPhone, and without the secret a
webhook's signature could not be verified at all. Polling it is; the full argument is
in `app/integrations/openphone/sync.py` and `.qa/state/openphone-done`.

### One hole this closed on the way past

`test_auth.py` enumerates `app.routes` and is the enforcement behind CLAUDE.md's
claim that *"a route added later is protected by default"*. It could not fail for a
route it could not see: this FastAPI version does not splice an `include_router`'s
routes into `app.routes`, it appends one opaque `_IncludedRouter` whose children hang
off `.original_router`, and the enumerator's `isinstance(r, APIRoute)` filter skipped
them. **`/api/softphone/*` has been invisible to that gate since the softphone
shipped.** Those routes were never actually unprotected — authentication is an
app-level dependency and each answers 401 — but "protected" and "pinned by the test
that proves it" are different claims. The enumerator now follows both attributes, and
the gate went from 88 cases to 91.

### Not verified, and not verifiable from here

- **No OpenPhone endpoint was called.** There are no live credentials in this
  environment, and the key lives only on the server. `GET /messages` and `GET
  /conversations` — the two reads the mirror adds — are shaped from documented
  behaviour and are marked UNVERIFIED in the client, exactly as the rest of that
  module was before its probe ran. `app.scripts.probe_openphone` has been extended to
  confirm both, read-only, and must be run on the box before the mirror is enabled.
- **The 30-day backfill volume is unknown.** `manage.py preview` sizes it without
  writing anything; it needs the production key to answer.
- **The parity gate could not be re-run.** `captures/` is gitignored and absent in a
  fresh worktree, and regenerating it needs a live logged-in GHL browser session and
  `GHL_APP_PASSWORD`, neither of which exists here. Both Conversations-screen
  additions are conditional on data a parity capture does not contain — the chip
  renders only when `source_system` is set, the banner only in a thread holding a
  mirrored event — so in a capture of existing data neither renders and the measured
  geometry is unchanged. That is an argument, not a measurement, and it is recorded
  as such.

## AMENDMENT (2026-09-13): the Pipelines tab, its modal and its menu are GoHighLevel's — and a stage or pipeline holding deals CAN be deleted

The owner's goal, in his words: the Opportunities module must "look like the original
one" and "work with same ui and same functions" as GoHighLevel. He supplied
screenshots of the live account (`refs/opps/02-08, 10-12`, taken on his laptop — no
live session exists on the server and nothing was driven there). **The screenshots
are the specification** for the tab bar, the Pipelines list, its ⋮ menu and the
Create/Edit pipeline modal. They are not computed-style captures, so pixel values are
read off an image, not measured by `capture/extract.js`; where GHL wins on structure
below, that is why. Forecast and Bulk Actions stay OURS, as recorded on 2026-09-10.

Branch `feature/ghl-pipelines`, migration `d4a9c1e7b352` (on `b7e3f1a8c204`).

### What this overrides, item by item

1. **"A stage or a pipeline can only be deleted once it is empty" (2026-09-10) — OVERRIDDEN.**
   It now works like GoHighLevel, **but no deal is ever deleted**:
   * a stage holding deals asks which stage *of the same pipeline* receives them; a
     pipeline holding deals asks for a destination *pipeline and stage*. They are
     moved, then the stage or pipeline is deleted. Without a destination the API still
     answers **409 naming the count**, and no `force` flag skips that at any role;
   * an empty stage, and a pipeline holding no deals (stages and all), delete after a
     confirm. "Empty means empty — the columns are somebody's configuration too" is
     retired: GHL deletes such a pipeline and there is no deal in it to protect;
   * a **structural move writes `pipeline_id`, `stage_id` and the in-column rank and
     nothing else.** It is a Core `UPDATE` that sets `updated_at` to itself, so the
     column's `onupdate` does not fire; `custom_fields` — including `owen_call_id` —
     is never mentioned and comes out byte-identical. The rank is written because
     arrivals would otherwise share positions with the destination's own cards; they
     keep their relative order and land after them;
   * **a structural move fires NO automation.** The drag path calls
     `on_opportunity_stage_changed` (rule 4, a customer text per deal); this path
     does not, and a test asserts the `jobs` table is unchanged while proving the same
     contact WOULD have been queued by an ordinary move. Jobs already pending are left
     untouched: a `stage_change_notify` queued earlier for a stage that is then
     deleted still runs, and the handler already words a missing stage as "updated";
   * **one transaction.** Nothing commits until the end; a failure part-way moves
     nothing and deletes nothing (tested by making the last step raise). The final
     step refuses to delete a pipeline that still has a deal filed in it;
   * what pointed at a deleted pipeline: **saved views and calendars are DETACHED**
     (`pipeline_id = NULL`), exactly as they already were for an empty pipeline. A
     view with no pipeline re-filters whichever board is open, so the board does not
     break. Re-pointing them at the destination was rejected: a list called "Open AHS"
     silently showing Retail is a list that lies. `custom_field_pipelines` rows go, the
     definitions and every answer stay; the pipeline's permission rows go.
   * In the modal a stage deletion is **pending until Update**: Cancel discards it.

2. **"Role: ADMIN for all seven endpoints" — AMENDED.** DISPATCHER may create, edit,
   duplicate and reorder pipelines and stages. **ADMIN alone** deletes a stage or a
   pipeline (including removing a stored stage through the modal's stage list) and
   manages permissions. TECH changes nothing. The screen disables, with the reason, what
   a role cannot do.

3. **Pipeline names are now UNIQUE, case-insensitively** — the modal's own helper text
   ("Use a unique, descriptive name"). Checked on create, rename and duplicate
   ("<name> (copy)", then "(copy 2)"…). **Stage names are still NOT unique** — the two
   "Call Back" stages and the CLI's exit 5 are untouched. The check runs across every
   pipeline, including ones the caller cannot access, so a 409 can reveal that *a
   name* is taken; accepted, because two boards with one name is the ambiguity the
   rule exists for.

4. **"A new pipeline starts with no stages" — kept at the API, not in the modal.**
   `POST /api/pipelines` with no `stages` still creates an empty pipeline. The Create
   modal pre-fills GHL's four starter rows (New Lead 20 · Contacted 40 · Proposal Sent
   60 · Closed 80), visibly and editably, before anything is sent.

5. **"Per-stage win probabilities were rejected deliberately" (Forecast) — OVERRIDDEN
   for probabilities a person types.** The 2026-09-10 objection was to a rate
   *inferred* from stage history this schema does not keep. These are entered by hand
   in the modal, so nobody is being shown an unverifiable curve. Rule:
   `use_opportunity_probability` on → each open deal at its own `probability`, else
   its stage's; off → the stage's `probability`; still unset → the pipeline's
   conversion rate, **which is exactly what every existing pipeline does**, since
   every existing stage's probability is NULL. Each forecast row reports `weighting`
   (`opportunity` | `stage` | `conversion_rate`) and the stage's probability.

6. **The Custom fields tab is gone from Opportunities** — GHL has four tabs. The same
   `CustomFieldsPanel`, unedited, is mounted at **Settings → Custom Fields**
   (`/settings/custom-fields` also opens it). Everything it did still works there.

### "Show in reports" — what each switch does, and does not

Two independent flags per stage. `show_in_funnel = false` removes the stage **and its
deals** from the Dashboard Funnel's walk, so `total`, `reached` and both percentages
are the funnel of the shown stages. `show_in_pie = false` removes it from the Stage
distribution donut (`distribution` / `distribution_total` in
`GET /api/dashboard/funnel`; the card no longer draws the funnel's list). Neither
touches the board, the Forecast or `/api/pipelines`. Those two cards are the only
stage-level funnel and pie consumers in the app; Reporting's donuts are calls and
appointments.

### Display colours

`pipelines.color_mode`: `none` (default, what every existing pipeline is) · `dot` · `tint`.
The board's stage column header draws a coloured dot, or a 12%-alpha fill with a
35%-alpha border, from `stages.color`. A new stage is painted from a fixed palette by
position (`STAGE_PALETTE`, mirrored in `lib/pipelines.ts` and pinned equal by a test);
a stage created before colours existed (NULL) borrows the palette colour for its
column, so switching an old pipeline to Colored dot never draws a column without one.

### Per-pipeline permissions — "Manage permissions"

`pipeline_permissions` holds one row per ALLOWED (pipeline, user). **No rows = everyone.
ADMIN always.** Roles still decide what a user may do with what they can see.

A user without access sees neither the pipeline nor any deal in it, anywhere. Every
read goes through `app/pipeline_access.py`, which computes the set of HIDDEN pipeline
ids (empty for ADMIN and for the common no-restriction case, so every query is exactly
what it was). A lookup by id answers what a nonexistent id answers — 404, or the same
400 a wrong pair gets — never 403. Per surface: the board and `/api/opportunities`;
fetch, drag, edit and delete by id; create into a pipeline; both bulk actions;
`/api/search` (items *and* `total`); Forecast; `/api/dashboard` figures and the funnel
(including which pipeline it defaults to); the Call report's won-deal credit; the
contact panel's opportunity list on all five routes that return it; appointment list,
detail, create and edit (the visit stays, the link to the hidden deal is blanked);
saved views (hidden if filed on a hidden pipeline); calendars (link blanked);
custom-field `pipeline_ids`; every structure route; and the `ghl` CLI, which only
reaches data through the API (tested by driving the real CLI against the app).
`test_pipeline_permissions.py` has one test per surface, and
`test_every_route_that_reads_a_deal_is_on_the_audited_list` fails the day a route that
touches opportunities or pipelines is added without being recorded there.

Reordering takes a permutation of the pipelines the CALLER can see; hidden ones keep
their slots, so a refusal never lists ids the caller should not know. Duplicate copies
the access list, or a copy of a restricted pipeline would open it to everyone.

### Smaller decisions, overrulable

* `pipelines.updated_at` is NULL for every pipeline that predates it and the list shows
  "—" rather than inventing a date; it is set by every structure write from now on.
* Copy link hands out `/opportunities?pipeline=<id>`; nginx already serves `index.html`
  for any path, and the app keeps that path in the address bar until the user
  navigates away. A link to a pipeline the reader cannot access opens the first one
  they can.
* Pipelines are listed in `position`, then `id` (the list had no order at all before).

### Screenshot assumptions (what the screenshots do not show)

The stage row's chevron opens the stage's colour (palette + custom picker) — the
screenshot shows a chevron beside the report icons and nothing else; a funnel/pie
switched off is drawn dimmed with a slash; every dialog behind the ⋮ menu (Manage
permissions, Move to position, Delete, and Delete stage in the modal) is our shape in
the modal's style; the empty-list, search-miss and error states are ours; rows per page
offers 10/20/50; Copy link falls back to a copyable prompt when the clipboard is
unavailable; the tab bar's active blue and underline are read off 05-08.

## AMENDMENT (2026-09-13): the opportunity card and modal are rebuilt from the owner's GHL screenshots

The owner, in his words: the Opportunities module must "look like the original one"
and "work with same ui and same functions", and "in ours we cant add tasks or
stuff". He supplied screenshots of his live GoHighLevel card (10, 15-21) and modal
(11, 12, 22). **Those screenshots are the specification**: there is still no live GHL
session on this server and nothing was captured. So, like the Calendars Month view
and the other "OURS" screens, **nothing here is measured parity** — it is built to
match screenshots by eye, and every reading taken from them is listed in
`.qa/state/oppmodal-done`. Re-measure if a live session is ever opened.

### What this AMENDS, explicitly

- **"Answers are … NOT on the board card" (2026-09-11) stands** — the card shows no
  custom field. What changed is the card's ICON ROW: it now carries real counts
  (tags, notes, open tasks) and every icon works. The "Call and SMS are stubbed, so
  they are shown but inert" comment is gone with the emoji it described.
- **"The `owen_` namespace is now read-only in the form" (2026-09-11) is superseded
  for the SCREEN, not for the data.** The owner does not want his old account's
  `owen_*` / `workiz_*` fields rendered at all (screenshot 12 shows them in GHL; he
  said ignore them). The read-only "From OWEN" block and the "Recorded earlier" box
  (screenshot 13) are removed. Every rule on the DATA is unchanged: `merge_answers`
  still keeps every reserved key, `owen_call_id` is still refused a change,
  `workiz_*` is still read-only in every direction, no definition may claim either
  prefix. The modal now sends only answers that CHANGED (`changedAnswers`, which
  skips reserved keys), and a test proves both blobs are byte-identical after an
  Update. An answer to an archived field or a field another pipeline asks is no
  longer shown read-only in the modal; it is still kept on the deal untouched and
  reappears the moment the field is asked again.
- **"Not built: … opportunity tags, Followers" (Automations section) is closed for
  Followers** (`opportunity_followers`) and answered for tags as below.
- **"Deliberate deviation: clicking a card navigates in GHL" stands** — still a dialog,
  still no router.

### Decisions taken, all overrulable

- **Tags on the card and in the modal are the PRIMARY CONTACT's tags.** Opportunities
  have no tags of their own, and the migration the brief allowed adds no
  `opportunity_tags` table. Editing Tags in the modal adds/removes the contact's tag
  immediately, through the existing STAFF endpoints. If the owner wants deal-level
  tags separate from the customer's, that is a new table and a separate decision.
- **Notes are per opportunity (`opportunity_notes`), STAFF-only on every path**, the
  2026-09-10 rule through the same predicate, now `auth.sees_internal`. Under a deal's
  notes the contact's thread `NOTE` events (the Workiz merge notes included) are
  shown read-only. The card's note count is both: what the Notes tab lists.
- **Tasks (`opportunity_tasks`) notify nobody.** No reminder, no text, no assignee
  notification; the tests count the jobs table. Read ANY_USER, every write STAFF —
  including delete, which departs from "admin deletes": a task is the team's own
  to-do, not a customer record.
- **An assignee or follower is any active user account**, TECH included — a site task
  usually goes to a tech.
- **Custom-field groups (`custom_field_groups`, `custom_field_defs.group_id`)** are the
  modal's custom tabs. A group applies to every pipeline; a field belongs to at most
  one; removing a group moves its fields to Opportunity details and touches no
  answer. ADMIN writes, like the rest of the field structure. None are seeded.
- **Changing the Pipeline in the modal requires choosing a stage in the new one**;
  the API refuses a move without one. The deal is filed at the bottom of the new
  column and the old column is re-packed. It fires the stage-change rule exactly
  as a stage move does.
- **Primary contact is required on an edit** (GHL's red *): the API refuses
  `contact_id: null`. A deal that already has no contact still opens; Update asks
  for one. Primary email and phone edit the CONTACT, through the contact PATCH.
- **Additional contacts: at most 10, never the primary**, refused whole by the API.
- **Deleting a deal deletes its own notes, tasks and link rows** and returns the
  counts; the confirmation says so. Appointments stay detached, as before.
- **The card's "View conversations" and Associated objects' links** need the shell to
  switch pages. App.tsx gained exactly two lines (operator's fence exception,
  2026-09-13) registering `openRecord` with `lib/openRecord.ts`; without them those
  controls are not drawn. Opening a thread for a contact with none creates the empty
  thread (`POST /api/contacts/{id}/conversation`) and sends nothing.
- **"⚙ Manage fields" links to Settings → Custom Fields** (`/settings/custom-fields`,
  from `SETTINGS_SECTIONS`). It is a real navigation to the deep link App.tsx already
  honours on load, so no fenced file changed. That reloads the app, so an unsaved
  edit in the modal is confirmed before it is discarded. (Until feature/ghl-pipelines
  merged it opened the panel over the modal instead; that overlay is gone.)

### Not built

Payments tab (out of the product). An audit log and its footer id (no audit log
exists). GHL's association chip on a note card (nothing counts note associations).
A `ghl` CLI for tasks, notes or groups. A TECH completing
their own task (every task write is STAFF). Notifications for tasks, by design.

### After `feature/ghl-pipelines` merged (same day)

- **Probability** is drawn in Opportunity details only when the deal's pipeline has
  "Use opportunity-level probability" on — including the pipeline it is being moved
  TO in the same edit. Off, the field is not drawn and Update sends no probability,
  so a stored value is left alone rather than cleared.
- **The modal's Pipeline dropdown lists only pipelines the user can access**: it is
  fed by `GET /api/pipelines`, which omits the rest. The server is the gate, not the
  list: a move into an inaccessible pipeline is refused with the same
  `400 unknown pipeline_id` a nonexistent one gets, and a deal's tasks and notes in a
  hidden pipeline answer the same 404 as the deal (`pipeline_access.py`).
- **Migration `f3c8e2a61d97`**, on `d4a9c1e7b352`: five CREATE TABLEs and one nullable
  ADD COLUMN (`custom_field_defs.group_id`). Proven on a throwaway SQLite stood up at
  the previous head with representative rows: every pre-existing table hashed
  byte-identical before and after, the new tables empty, `alembic check` clean, one
  head, and a downgrade/upgrade round trip clean.

## 2026-09-13 amendment — one tab bar on every module, and GHL's pipeline dropdown

**Owner's decision, overriding the per-page tab-row measurements above** (including
the 36px Contacts tab-row gap): Opportunities, Contacts, Conversations and Calendars
draw ONE header, `components/PageTabs.tsx`, extracted verbatim from the Opportunities
bar rebuilt earlier today (refs/round3/23). Title 18px/500; tabs 14px/500, inactive
rgb(71,84,103), active rgb(56,160,219) with a 2px underline flush with the bar.

- **Restyle only.** Every page keeps exactly the tabs it had and what each did.
  Contacts' five and Conversations' six were inert labels and still are (rendered as
  `<span role="tab">`, no pointer, not buttons); Calendars keeps its two live views and
  the inert "Calendar settings"; Opportunities keeps its four, Forecast dimmed for a TECH.
  Wiring those labels is other branches' work, not this one's.
- **The pipeline picker is GHL's dropdown** (refs/round3/31), `components/PipelinePicker.tsx`,
  replacing the native `<select>`. Its list is the board's own `GET /api/pipelines`
  answer, so per-pipeline access needs no second check here. "All pipelines" is a grey
  label that cannot be highlighted or chosen — a board of every pipeline at once is not
  something this app draws. "+ New pipeline" (DISPATCHER, ADMIN) opens the existing
  Create pipeline modal; Create selects the new pipeline, Cancel changes nothing.
  Selecting sets the same `pipelineId` state the select did, so filters, saved views and
  `?pipeline=` deep links are untouched. Logic lives import-free in `lib/pageTabs.ts`
  and is executed by `tests/test_page_tabs.py`.

## AMENDMENT (2026-09-13): the Book appointment modal is GoHighLevel's, and blocked off time exists

The owner supplied a screenshot of GoHighLevel's "Book appointment" modal
(`refs/round3/29`) beside ours (`28`) and said: make it match GoHighLevel **exactly**.
Like the opportunity modal the same day, **the screenshot is the specification and
nothing here is measured parity** — no live GHL session exists on the server, and
every reading taken off the image, and every assumption where it shows nothing, is
listed in `.qa/state/appt-done`. Branch `feature/ghl-book-appointment`, migration
`a5d2c8e4f917` on `f3c8e2a61d97`.

### What this overrides, item by item

1. **"The dialog shows Status disabled … adding `status` to the POST model would be a
   contract change and is a separate decision" (2026-09-09) — OVERRIDDEN.** GoHighLevel's
   footer books with a status, so `AppointmentCreate` accepts `status` (validated against
   `APPOINTMENT_STATUSES`) and the footer control is live. `blocked` is still not offered
   in the browser. A booking made `cancelled` queues no reminder, as before.
2. **"A TECH can open a booking and read the notes" (2026-09-10) — NARROWED.** The modal's
   right column is **Internal notes**, and the owner's brief says staff-only like every
   internal note in this system. `appointments.notes` IS that field, so
   `GET /api/appointments/{id}` returns `notes: null, notes_visible: false` to a TECH,
   through the same `auth.sees_internal`. A TECH now reads the new **Description** and
   **Meeting location** on the job they are driving to instead. This removes access a
   TECH had; a Workiz-imported appointment carries no notes, so nothing a TECH read on
   production today disappears.
3. **"From the calendar it is an optional picker of OPEN deals" (2026-09-11) — OVERRIDDEN
   for the create dialog.** GoHighLevel's modal has no Opportunity field and no Assigned to
   field, and neither is drawn. Consequences kept on purpose:
   * **The assignee is the selected calendar's user** (`Calendar.user_id`). An explicit
     `assigned_user_id` still wins, so `ghl appts create --user` is unchanged; a PATCH
     that moves a booking to another calendar hands it to that calendar's user unless
     an assignee is sent with it.
   * **The opportunity modal's "Book or update appointment" tab still links the visit.**
     It opens this same dialog with `lockedOpportunity`, which is sent and not drawn.
     Binding an existing booking to a deal from the calendar side is done in the
     appointment panel, which keeps its Opportunity field.
4. **"The appointment dialog is OURS" (2026-09-09) — superseded for the CREATE dialog.** It
   is now built to the owner's screenshot. The appointment detail panel is still ours.
5. **"Nothing in the UI can create a blocked slot" (2026-09-10 audit) — CLOSED**, by a real
   feature rather than by offering `status = blocked`: see below.
6. **The 2026-09-10 "known gap" that SQLite emits naive timestamps — FIXED for appointments
   and blocked times.** Incoming times are normalised to UTC before they are stored (SQLite
   keeps a datetime's digits and drops its offset, so `13:30-04:00` read back as 13:30 UTC),
   and every appointment payload emits them aware. PostgreSQL was already right; this is
   what made a DST test on SQLite honest.

### The modal, as built

Calendar (first calendar chosen) · Appointment title prefilled `{{contact.name}}` · Add
description · Date & time (the account timezone, Start time / End time) · Meeting location
(Calendar default / Custom) | Select Contact (required) · Internal notes | Status : ·
Cancel · Book appointment. The owner's removals: **no Default/Custom toggle** (no working
hours or slots; the user always picks the times), **no Recurring event**.

- **The title is resolved on save and the resolved text is stored.** `{{contact.name}}`,
  `{{contact.first_name}}`, `{{contact.last_name}}`, `{{contact.email}}`,
  `{{contact.phone}}`. An unknown variable is **refused 400**, never stored — braces on a
  calendar chip read as a bug — and a contact variable with no contact is refused with the
  reason. Renaming the contact later does not rename a visit already booked.
- **The Select Contact requirement is the modal's, not the API's.** The CLI and the
  telephony feed may still book without one.
- **Location is stored RESOLVED, as text** (`appointments.location`). Calendar default is
  the contact's `address_*` columns on one line at the moment of booking; editing the
  contact's address later does not move a visit already booked. A contact with no address
  books with no location, and the modal says so before it is sent. It is shown on the
  appointment panel, the deal's appointment tab, the contact payload and the grid rows.
- **Times are in the ACCOUNT's zone, America/New_York, whatever the browser's zone is.**
  `frontend/src/lib/accountTime.ts` computes every offset from the platform zone database
  (EDT GMT-04:00 in summer, EST GMT-05:00 in winter — nothing hardcoded) and is executed
  under node with the process zone set to Berlin, Caracas and Tokyo. A time that does not
  exist (2:30 AM on the spring-forward day) lands at 3:30 AM; one that happens twice is the
  first. The timezone control lists the account zone first plus the US zones (and the
  browser's own), and changes only how times are picked, never what is stored.
- **The time list is the whole day in 15-minute rows**, 12:00 AM to 11:45 PM, which covers
  the owner's "at least 5 AM to 11 PM" without cutting off a 2 AM emergency tarp.
- **Reminders are exactly as before.** The modal posts to the same endpoint; nothing in the
  reminder path changed and the tests count `jobs` rows for create, reschedule away and
  back, and edits of description/location (which churn nothing).

### Blocked off time — a table of its own

`blocked_times` (title, calendar, start, end, optional note, created_by). CRUD at
`/api/blocked-times`. **It sends nothing and enqueues nothing** — no route touches the queue
or an automation, and a test compares the whole `jobs` table around create, edit and delete.

- **Its own table, not `appointments.status = 'blocked'`.** A block has no contact, no
  reminder and no report tile; filing it as an appointment would put it in the Appointment
  report and in every appointment query that forgot to exclude it. The old `blocked` status
  and the Manage view's `kind=blocked` filter are left exactly as they were.
- **Roles: read ANY_USER, create/edit/delete STAFF.** Delete is a real delete and is STAFF,
  not ADMIN — a block is the team's own schedule, not a customer record; the same call made
  for an opportunity's tasks. Overrulable.
- **Booking over blocked time on the same calendar is refused 409 until confirmed** with
  `allow_blocked_time: true`; the refusal writes nothing and names the block. The modal
  shows the sentence with "Book anyway"; the appointment panel does the same for a
  reschedule or a calendar change ("Save anyway"). Touching ends do not overlap; a booking
  with no calendar, or a cancelled one, is never asked.
- **Drawn grey and hatched on the Day/Week grid and as hatched chips in Month view**, never in
  a calendar's colour; a click opens it on the modal's Blocked off time tab to edit or
  delete. "View by type: Appointments" hides blocks; All and Blocked slots show them. The
  Users filter selects the blocks on those users' calendars.

### The grid hours

The Day/Week grid already drew all 24 hours; it now **opens scrolled to 5 AM** and still runs
to 11 PM. **Known gap, not fixed:** the grid positions bookings in the BROWSER's zone
(`calendarGrid.ts` is local-time day arithmetic), while the modal and the panel use the
account's. For staff in Florida the two are the same; a laptop set to another zone draws the
grid shifted while the modal reads correctly. Moving the grid onto the account zone is a
change to `calendarGrid.ts` and to the measured Week view, and is its own task.

## AMENDMENT (2026-09-13): every call and text, both lines, one inbox — and an unknown number is never a contact

The owner, in his words:

> *"show everyone that texts/calls the number binded to the system and the number from openphone/quo"*
> *"show all the conversation in chronological order whether we are texting from quo or from this ghl clone"*
> *"if its a number that is not registered as a contact, it should not be saved as contact but the numbers with the conversation must display anyways"*
> *"i need to have tracking of all the communication we have with the customer across the platforms, centralized in this module"*

Branch `feature/unified-inbox` (this repository) and `feature/quo-inbox` (owen-main).

### What this OVERRIDES, item by item

1. **"`POST /api/events` accepts a phone number, and creates the contact" (2026-09-11) —
   OVERRIDDEN.** A number no contact holds (last ten digits, the shared rule) creates **no
   Contact, no Opportunity and no job**, from either line, call or text. The event lands on
   a **number-only thread**. The judgement recorded then ("a roofing lead that rings once and
   is never recorded is a lost job") is still honoured — nothing is dropped, the thread is in
   the inbox, unread — but spam no longer becomes contacts. `new_lead_notify` is not fired
   for an unknown number. A too-short number (fewer than ten digits) is refused 422 rather
   than filed under a fragment. The explicit-`contact_id` path is unchanged.
2. **Rule 1, missed call → auto text back — DISABLED.** "Nothing texts anyone back
   automatically — contacts included." `automations.on_inbound_call` returns
   `MISSED_CALL_DISABLED` before doing anything, and the worker's `missed_call_textback`
   handler refuses too, so a job queued before deploy sends nothing. The rule's code and its
   `is_fresh` guard are kept, readable, behind `MISSED_CALL_TEXTBACK_ENABLED = False`. The
   "Automations — BUILT" table above is historical for rule 1. Tests assert on the `jobs`
   table that no `missed_call_textback` is ever enqueued.
3. **"Polling, not webhooks — decided on owen-main" (2026-09-11) — AMENDED.** The owner
   registers the Quo webhook in Quo's dashboard and pastes its signing secret into owen-main's
   config, which removes both disqualifiers. owen-main now receives
   `POST https://api.owen.santiagoproperties.uk/webhooks/openphone`, HMAC-verified, off by
   default, and the poll stays on as the backstop. owen-main still makes no non-GET request to
   OpenPhone. Setup steps: owen-main `docs/QUO_WEBHOOK.md`.
4. **The source chip says "Quo"**, not "OpenPhone" — the owner's name for it. The wire value
   stays `OpenPhone`. BulkVS events relayed by owen-main now carry `source_system = BulkVS`
   and the DID, so new rows on both lines show their line and system; rows from before still
   render no chip (no guessing, as recorded 2026-09-11).

### The number-only thread — design, and why

`conversations.contact_id` and `conversation_events.conversation_id` are both NOT NULL, and
the migration rule is CREATE TABLE / ADD COLUMN only. So a thread with no contact cannot be a
`conversations` row, and its events cannot be `conversation_events` rows. Two new tables:

* `number_threads` — `phone` (as `store_phone` stores it), `phone_key` (last ten digits,
  UNIQUE), `quo_name` (display only), `unread_count`, `starred`, `last_event_at`,
  `created_at`: exactly a conversation's thread state, keyed by number instead of contact.
* `number_thread_events` — `conversation_events` column for column with the parent swapped,
  including the unique `dedupe_key` index. A test pins the two column sets equal.

Rejected: a sentinel "unknown" contact (it IS a contact row, which the owner forbade);
nullable columns on the existing tables (needs an ALTER); a flag on `contacts` (still a
contact, still in every contact list, search and export).

**Adoption.** When a contact comes to exist with that number — "Add as contact", any
`POST /api/contacts`, a phone edited on a contact (the opportunity modal's Primary phone goes
through the same PATCH), the Workiz import, or any other ORM write — a `before_flush` listener
on the Session class (`app/db.py` → `number_threads.adopt_on_flush`) copies every event onto
the contact's thread and deletes the number thread, in the same flush. No event lost (every
payload column copied), none duplicated (a `dedupe_key` already on a contact thread is
skipped), unread count and star carried over. It imports neither `automations` nor `queue`,
so the Workiz import's jobs-table guard still holds. Hooked on the Session rather than on each
route so a path added later is covered by default.

**One inbox.** `GET /api/conversations` returns both kinds mixed by `last_event_at`, each row
carrying `kind` (`contact` | `number`) and `key` (`c<id>` / `n<id>`) — ids come from two tables
and can collide, so the browser and the CLI never select by id alone. Every surface applies
one rule to both: Unread/Starred tabs (the same two columns), Recent/All, `assigned=me` (a
number has no owner, so it is in the team inbox and nobody's own — like an unowned contact),
the in-place search (the number by the shared digits rule, and Quo's name), read/star
(`PATCH /api/number-threads/{id}`, same body), delete (`DELETE`, ADMIN, same as a thread),
and staff-only notes (the thread view for both is ONE function, `_thread_events`, through
`auth.sees_internal`). The ctrl+K palette is unchanged: it searches contacts, deals and
contact-thread message bodies, and does not list number-only threads — the inbox search does.

**Usable without saving.** `POST /api/number-threads/{id}/messages` goes through the same
`get_transport()` — LOGGED_ONLY while unarmed, REFUSED by owen-main while SMS is dark — and
`/call` through the same `_dial` a contact call uses, so owen-main's `CRM_LINK_ALLOWLIST` (empty
allows nothing) gates a number exactly as it gates a contact. Replies always leave on the
BulkVS DID; the Quo banner names `+1 954-482-9099`.

**Quo's name.** owen-main reads Quo's contact book (GET only, cached) and sends
`source_contact_name`; it is shown "from Quo" and only prefills the Add-contact form.

**The one auto-created contact in production** is converted by
`python -m app.convert_auto_contacts` (dry run by default, `--commit` to write), which only
touches a contact created by `owen-main` with no deal, no additional-contact link, no
appointment, no task, no tag, no thread note, no field changed from what the ingest wrote and
`updated_at` within two seconds of `created_at`, and whose number no other contact holds. The
operator runs it.

### Screens — ours, not measured

GoHighLevel has no number-only thread, so the "Not a contact" pill (on the row and in the
panel), the header's "Add as contact" button beside the formatted number, the right-hand
number panel, the "from Quo" label and the transcript disclosure under a call are OUR design
in the existing Conversations style. Checked in a browser at 1440×900 against a disposable
database: the header holds the number, the button and the measured icon row without pushing
the panel off screen; a longer header was tried first and did. The measured
geometry of the list, header and composer is untouched for a contact thread; the star in the
header now works for both kinds.

## AMENDMENT (2026-09-14): a Workiz job whose customer is not in the clients file makes its own contact — and AHS jobs pair with their email card

AMENDS "The Workiz import — the real business data arrives (2026-09-11)". A dry run of the
owner's newer jobs export (416 jobs) against the original clients file skipped 4 jobs with
"no client record matches its phone or its name": customers created in Workiz after the
clients file was exported. **Owner's decision: make the contact from the job row.**

### What this overrides

- **"no client record matches" is no longer a skip.** The job row's `Client`, `Phone`
  (through `store_phone`), `Email`, `Address` / `City` / `State` / `Zip code` and `Source`
  make a contact, `contact_type` Customer. Jobs sharing the last ten digits of a phone are one
  customer (one contact, one opportunity each); a row with no usable phone joins the group
  that carries its email, or groups on the email. **A row with neither is still skipped**,
  with its reason — there is nothing a re-run could find it by.
- A cancelled job for such a customer is still nothing at all (unchanged, and deliberately
  so: there is no client record to keep).

### How a re-run finds a job-row contact — there is no `workiz_id`

A job-row contact has no Client #. It carries `custom_fields.workiz_from_jobs` — the Job #s
it was made from, in the reserved read-only `workiz_*` namespace (`Contact.custom_fields` is
not writable through the API at all). A re-run looks for the customer in this order:

1. a contact whose `workiz_from_jobs` holds one of the jobs — survives a phone corrected by hand;
2. a contact with the same last ten phone digits (the identity rule everywhere else);
3. a contact with the same email, case-insensitively.

A contact **this importer made from a job row** (`created_by` "Workiz import", `workiz_from_jobs`
set, no `workiz_id`) is updated in full. **Any other match — a contact typed in by hand, or
saved from an inbound call — gets the deal and only its blanks filled**; its name is a human's
and is never overwritten, and a Lead becomes a Customer. Several matches: the importer's own
contact wins, else the lowest id, and the rest are listed under Conflicts. When the customer
later turns up in a newer clients file, that Client # is written onto the same contact rather
than creating a twin.

### Opportunities the export no longer mentions — left untouched

The dry run lists every CRM opportunity whose `workiz_id` is not a Job # anywhere in the file
(cancelled, skipped and duplicate rows count as present), by Job # only. **The owner decided
to leave them alone**; nothing reads that list. Not addressed, and worth a human's eye: a job
that WAS imported open and is now **cancelled** in the export is also left exactly as it was,
because a cancelled row still imports as nothing.

### AHS jobs arrive twice — owner decision Q15 (2026-09-13)

AHS jobs come from the warranty company's email (the ahsmail intake makes the card) AND from
later Workiz exports, whose AHS rows carry no AHS job number. When a Workiz job routes to the
AHS board and no opportunity has its `workiz_id`, the importer looks for a card that is:

- **`created_by` = "AHS email"**, **`custom_fields.ahs_job_id` set**, and **no
  `custom_fields.workiz_id`** — these three facts are the contract with ahsmail, pinned as
  constants (`AHS_EMAIL_CREATED_BY`, `AHS_JOB_ID`) and by fixture tests;
- for the same customer: the contact the job resolved to, or any contact whose phone matches on
  the last ten digits;
- created within **3 days either side** of the job's `Job Created`.

**Exactly one** such card: the job's `workiz_id` is written onto it and its pipeline, stage,
status, value, source and Job type follow Workiz as usual. Its title, `created_by`, `created_at`,
contact (filled only if empty), `ahs_job_id` and its notes are the email's and are kept — on
every later run too. **None, or more than one**: the job gets its own card as before and is
listed under "AHS jobs not matched to an email card" (Job # and why; for several, how many).
A card that is the only candidate of **two** Workiz jobs attaches to neither — pairing both
would merge two jobs into one card. Nothing merges or deletes a card; a card that already has
a `workiz_id` is never paired again. A job with no `Job Created` date is listed, not matched.

Smaller calls, all overrulable: the window is measured against the card's `created_at` (the
email's arrival as ahsmail records it); a future-dated Workiz visit on a paired card books its
appointment in "Workiz Jobs (imported)" exactly as any imported job does, whether or not
ahsmail booked one. No migration: everything lives in existing JSON columns.

## AMENDMENT (2026-09-14): American Home Shield work-order emails become cards, as they do in GoHighLevel

The owner's decisions, implemented as given. Branches `feature/ahs-email-jobs` (this
repository) and `feature/ahs-email-to-crm` (owen-main).

owen-main has relayed every AHS work order in the Dispatch mailbox to GoHighLevel since
before this CRM existed. It now relays each one to BOTH: GoHighLevel exactly as before, and
this CRM through its own queued job (`email_relay_crm`) with its own recorded status per
email (`inbound_emails.crm_status`). Neither can block, delay or re-send the other. Off by
default on owen-main (`CRM_LINK_EMAIL_JOBS_ENABLED=false`).

### What arrives here

`POST /api/ahs-jobs` and `POST /api/ahs-jobs/cancellations` (`app/ahs_jobs.py`, routes in
`main.py`), one transaction each:

* **A card**: pipeline **Dream Team Roofing AHS**, stage **New Lead**, status open, titled
  `<job id> <service> - <customer>` (GoHighLevel's name, built the same way), value in
  integer cents (a float on the wire is refused 422, not rounded), source `AHS`,
  `created_by = "AHS email"`.
* **A new card per AHS job**, repeat customer included.
* **The customer**: last ten digits of the phone, then email (case-insensitive); otherwise a
  contact is CREATED from the email — name split on the first space, phone through
  `store_phone`, email, the service address into the `address_*` columns,
  `created_by = "AHS email"`, source `AHS`. A matched contact is never edited.
* **The work order** (the same `job_description` GoHighLevel's note gets) as an opportunity
  NOTE — STAFF-only on every path already.
* **A cancellation** adds `JOB CANCELLED by AHS: job <id> (<date in America/New_York>)` to
  that job's card and changes nothing else. No card → `outcome: no_card`, 200, nothing
  written; owen-main records `skipped_no_card`. The same cancellation twice is one note.

### AMENDS "`POST /api/events` … an unknown number is never a contact" (2026-09-13) — for AHS only

That rule protects against spam CALLERS. A work order American Home Shield dispatched to
this company is not spam, so this path creates the contact, as the owner said. The events
path is unchanged.

### `ahs_job_*` is a reserved namespace — the prefix is NOT `ahs_`

`custom_fields.ahs_job_id` is the idempotency key: the same email delivered twice answers
**200 with the existing card** (never 409 — a retry of a request that committed before its
answer got home must complete, not dead-letter). It is added to `RESERVED_PREFIXES` and
read-only in every direction through the SAME loop `workiz_*` uses (`READ_ONLY_PREFIXES`),
hidden by the browser's mirror of that tuple, and no field definition may claim it.

**Judgement call:** the reserved prefix is `ahs_job_`, not `ahs_`. The owner's own question
"AHS claim number" derives the key `ahs_claim_number` (it is the example in this file's
2026-09-11 custom-fields section). Reserving all of `ahs_` would have frozen those answers,
hidden them from the modal, and refused the definition. A test pins that it still works.

### THE CONTRACT with the Workiz importer

A card created from an AHS email has `created_by = "AHS email"`, `custom_fields.ahs_job_id`
set, and NO `custom_fields.workiz_id`. The importer finds an email card by exactly those three
facts. `test_the_contract_with_the_workiz_importer` pins them.

### Nothing is notified

`app/ahs_jobs.py` imports neither `app.automations` nor `app.queue` (a test parses its
imports), and counts the `jobs` table before and after inside the transaction: a new row is a
500 and the write rolls back (tested by making the count lie). `new_lead_notify` is never
enqueued.

### Auth: the same token and the same `events:write` scope as `/api/events`

Same machine account, same feed, same role check (`require_events_ingest`: ADMIN or
DISPATCHER; a TECH is refused). A second scope would only mean a second token to mint and
rotate for no narrowing that matters. `auth.EVENTS_WRITE_PATHS` lists the four paths.
Per-pipeline access: if the token's user cannot see the AHS board it is refused as "not
found", and a cancellation for a card in a hidden pipeline is `no_card`.

### Refusals, all 4xx so owen-main does not retry them

No pipeline of that name (or hidden) → 422. Two pipelines of that name, or two `New Lead`
columns on it → 409, "refusing to guess". A missing `customer_name` or a malformed job id →
422. Each is recorded on the email on owen-main as `refused` with the reason.

### Known limits, stated

* The address split keeps anything not confidently identified in `address_street`, the
  importer's rule. Dispatch writes `14436 SW 95TH LN MIAMI, FL 33186` — no comma between
  street and city — so the city stays inside the street and only state and ZIP are split.
* Idempotency on a JSON key has no unique index behind it (that would be a migration). Two
  deliveries of one job are serialised by a transaction-scoped advisory lock on PostgreSQL;
  owen-main drains its queue one job at a time anyway.
* **No migration on this side.**

## AMENDMENT (2026-09-14): the floating softphone dock is gone — one status dot, top right, every page

The owner, looking at the bottom-right "Ready for calls / Switch off / This browser rings
alongside the mobiles / Registered as …" card (refs/round3/33): *"i want something more subtle
... on the top right that shows on every page or view, but very subtle and if i hover onto it
it should show any more details if needed, but it should always show if we are online or not
so i know if everything working good"*.

### What this AMENDS in "The browser softphone is OURS" (2026-09-11)

- **"The dock is always on screen and says which of eight states it is in"** → the DOT is
  always on screen and its COLOUR says whether everything works; the eight states are still
  written out in plain words, one hover (or keyboard focus) away. Registration state still
  comes only from a SIP registration callback — that decision is unchanged.
- **The in-call bar** (Mute / Hang up) moved into the same hover card. While a call is live
  the dot becomes GHL's filled green phone button with the call timer and a soft pulse.
- **"A deployment with no phone system shows nothing at all"** → the dot always shows. The
  owner asked for "always"; the phone row then says browser calling is not set up.
- The incoming-call card is unchanged.

### The three checks, and what counts

The dot is the WORST of: **this browser's phone** (the softphone hook), **the link to the
phone system** (can the CRM reach owen-main, and is `CRM_LINK_ENABLED` / telephony on there),
and **Quo sync** (mirror on and keyed, last poll tick within 2 poll intervals, 30-day
backfill completed, webhook on with a secret; the last webhook time is shown but never turns
anything amber — a quiet line gets no webhooks). Ranking: red > amber > grey > green, so a
switched-off phone is grey rather than "all good", and anything broken beats it.

`GET /api/connection-status` (ANY_USER) asks owen-main's new read-only `GET /api/link-status`
server-side with the key this CRM already holds (`CRM_LINK_API_KEY` if the send link is
configured, else `OWEN_SOFTPHONE_KEY`, both `crm_link`-scoped), 3 s timeout, 30 s cache shared
by every tab, and **always answers 200**: owen-main unreachable is `link.state = "down"`, not
an error. The browser polls every 60 s and on window focus.

### Judgement calls, all overrulable

- **Quo sync switched off is RED**, not amber or grey. The owner named Quo sync as one of
  the three things "working" means, so its absence is broken, not optional. If the mirror is
  ever turned off on purpose, the dot will stay red until this is revisited.
- **Webhook off is AMBER.** The poll still backstops it; only latency degrades.
- **"Another tab is the phone" is GREY**, not green: this tab cannot see whether that tab is
  actually registered, and green would claim it is.
- **owen-main answering 404** (an older build without the status route) is amber on the link,
  not red — it is reachable.
- **The CRM API itself failing, or the browser offline, is RED** on the link row.
- **The Dashboard header gained `marginRight: 44`** on its right-hand controls. It is the one
  page whose own header puts controls in the top-right corner; in a real browser the dot sat
  on its ⋮ menu. No other page needed a change (checked at 1440×900 and 1024×768).

### Screenshot assumptions (what the references do not show)

GHL's top bar (refs/opps/02) shows a filled green round phone button, a bell and an avatar,
and nothing about hovering them. The idle look (a grey phone glyph with a small coloured
dot), the hover card's layout and wording, and the live pulse are OURS, styled after the
Opportunities dropdown/card look already rebuilt (white, 1px rgb(234,236,240), 8px radius,
soft shadow). Colours are the dock's own. The dot sits at `top: 9px; right: 16px` because this
app has no global top bar — GHL's icon row sits in the page header's top strip, and ours is
pinned into the same strip of every page header.

## 2026-09-14 — Quo call recordings play: a real player, and the 499 blanks repaired

**What was wrong (measured on production, read-only).** All 499 mirrored Quo calls had
`recording_url = NULL` (296 on contact threads, 203 on number-only threads), so the `<audio>`
under each had no `src` and read "0:00 / 0:00". Quo's `GET /v1/call-recordings/{id}` answers
`{"data": [ ... ]}` — a list — and owen-main treated it as one dict. Above that sat a player
row that was pure decoration: a play icon wired to nothing, a `▁▃▅▂▇` glyph "waveform", a
hard-coded time and "1x".

**owen-main** (`feature/quo-recordings`) reads the list (`openphone_client.pick_recording`),
so the poll and the webhook send `recording_url` from now on, and gains
`python -m app.integrations.openphone.manage recordings [--commit]`: a dry run by default that
re-sends, for each mirrored call WITH audio, one enrichment under the call's SAME dedupe key.
This CRM already fills a blank `recording_url` on a repeat delivery (`ENRICHABLE_FIELDS`,
`_duplicate_event` looks in both `conversation_events` and `number_thread_events`), so **no
change was needed here for the repair** — `test_call_player.py` now pins it on both thread
kinds, including that nothing but the blank is written.

**This CRM:**

- `components/CallRecordingPlayer.tsx` + `lib/callPlayer.ts`: play/pause, a plain seek bar,
  elapsed / total, 1x → 1.5x → 2x. One `<audio>` per call, `preload="none"` and **no `src`
  until play is pressed**. No player at all without a `recording_url`. A 404/502 from the
  stream replaces the row with "Recording unavailable". The native `<audio controls>` is gone.
- `GET /api/openphone/recordings/{id}` now honours **HTTP Range** (206 / 416,
  `Accept-Ranges: bytes`). Chrome will not seek a media response without it — measured in
  headless Chromium: the seek bar restarted the audio at 0 against a full-body 200 and
  landed at 0:10 of 0:20 once the route answered 206. Each seek is one more CRM → owen-main
  → Quo round trip. **Deliberately not cached server-side**: a cache would keep audio
  playing after owen-main or Quo went away, which the outage test pins must not happen.

**Assumptions the screenshots do not settle (overrulable):** the seek bar is a native range
input tinted GHL blue (the reference shows a waveform, which the brief ruled out as fake);
a pause icon (ring + two bars) mirrors the existing play icon; pressing play on one call
pauses any other call that is playing; a finished recording shows its full length with the
play icon, and pressing play restarts it; before loading, "total" is the event's own
`duration_seconds`, replaced by the media's real length once it loads.

## AMENDMENT (2026-09-14): every opportunity carries its job's address

**The owner approved it: an opportunity stores the address of the job / property being
worked on.** Until now the only address was the CONTACT's, so one customer with six
properties (six cards, one contact) kept one address and each card's property survived only
in its title. Workiz stores an address per job; every AHS work order carries a service
address per job. Branch `feature/opportunity-address`.

### What this AMENDS

- **"contact address, for the Workiz import" (2026-09-11)** stays exactly as it was for the
  contact. What changes is that it is no longer the ONLY address: a card has its own four
  columns, and where the two disagree the card's is the job's.
- **The Book appointment amendment (2026-09-13), "Calendar default" location**: it resolved
  to the contact's property address. It is now **the opportunity's address when the booking
  is linked to a card that has one, else the contact's**. Still resolved on save and stored
  as text. A card in a pipeline the editor cannot access lends a booking nothing (a PATCH
  re-defaulting a booking linked to a hidden card uses the contact's address).
- **"Search: opportunity title only, per the owner" (the palette)**: `/api/search` and the
  board's search box now match a card's title, **street or city** — one shared clause
  (`main._opportunity_text_match`), always inside `pipeline_access` like everything else.
  The contact's address is still NOT searched; nothing asked for it.
- **Workiz import card titles** collapse internal whitespace (`" ".join(name.split())`):
  Workiz drops "&" from a name and leaves "ANGELO  ALEXIA PURP". Imported appointments are
  named after the same string.

### The schema — additive only

`b3e9a7c51d28`, `down_revision = c8f2b6d41a93` (the single head on origin/main and in
production). Four `op.add_column` on `opportunities`, all NULLABLE, typed exactly like the
contact's: `address_street` VARCHAR(255), `address_city` VARCHAR(120), `address_state`
VARCHAR(80), `address_postal_code` VARCHAR(20). No server default (nullable; an empty-string
default would make "none" and "blank" the same). No backfill: existing cards read NULL
until the importer is re-run by a human.

### Who writes it

- **The API**: `POST /api/opportunities` and `PATCH /api/opportunities/{id}/detail` take the
  four fields; max lengths mirror the columns (422 naming the field), whitespace is stored as
  NULL. Role rules unchanged — whoever may edit the card (STAFF) edits its address; a TECH
  reads it. The board list, the detail read and the palette rows carry it.
- **The Workiz importer**: each card's address comes from ITS job row (`Address`, `City`,
  `State`, `Zip code` via `address_from_job`), **all four as a unit** on every run, so a
  re-run fills the cards imported before the columns existed and follows a correction made
  in Workiz. **A job row with no address writes nothing to the card** — a new card stays
  empty (never another job's, never the contact's), and an existing address (typed by a
  human, or from an AHS email) is not wiped by a blank export. *Judgement call, overrulable:*
  a paired AHS email card also takes the Workiz job's address when the row has one — it is the
  same job, and Workiz's is split into four columns where the email's city is stuck inside
  the street. The email card's title, creator, date and notes are still the email's.
- **`POST /api/ahs-jobs`**: the service address goes on the CARD through the same
  conservative `split_address` the contact gets. A matched contact is still never edited. A
  repeat delivery answers with the existing card and changes nothing (pinned column by
  column). Cards created before this change are not touched by a repeat delivery either.

### Screens

- **Opportunity modal → Opportunity details**: an **Address** group after the ungrouped
  custom fields — Street address (full width), City · State, Zip code. Honours "Hide empty
  fields" (the whole group hides when the card has none). While the card has no address and
  the primary contact has one, the contact's values show greyed as the inputs' placeholders,
  with the sentence "No address on this opportunity. Showing the contact's address." and a
  blue **Use contact address** text button on the heading row. Only that click copies it;
  Update then saves it like any other edit.
- **Board card**: ONE grey line (12px, rgb(152,162,179)) directly under Value — "street,
  city", truncated with an ellipsis, full text on hover — only when the card has a street or
  city, and only in the layouts that draw Value (not Unlabeled). Nothing else on the card moved.
- **Book appointment**: the "Calendar default" caption shows the card's address when opened
  from a card that has one, else the contact's.
- **ctrl+K palette**: an opportunity row's second line ends with "street, city" when set.

### Screenshot assumptions (the references do not show an opportunity address)

GoHighLevel's opportunity screen has no native address group in the owner's screenshots 11,
12 and 22 — the placement, the "Address" heading styled like "Opportunity details", the field
labels (GoHighLevel's contact labels: Street address, City, State, Zip code), the greyed
placeholder fallback and the text-button styling are ours, built from the modal's existing
parts. The card line's colour and position are ours, kept one line so the measured card is
otherwise identical.

### Filling the existing cards in production (a human runs this, after the migration)

`uv run python -m app.workiz_import` (dry run) then `--commit`, with the same clients and
jobs files as the last import. It writes each card's address from its job row; it also
re-applies everything the importer always writes (stage, status, value, source, Job type,
title — now whitespace-collapsed — and contacts from the clients file). It never creates a
second card for a Job # it already imported, never deletes, and does not touch cards made by
hand, AHS email cards it has not paired, or cards whose Job # the export no longer mentions.

## AMENDMENT (2026-09-14): a call Checklist on every opportunity, and GoHighLevel's Add new opportunity modal

The owner's decisions, implemented as given. Branch `feature/opportunity-checklist`.

**The Checklist is ordinary custom fields in ONE custom tab named "Checklist"**, so the owner
rewords, reorders, adds and archives its questions himself in Settings → Custom Fields. It
stores ANSWERS in `opportunities.custom_fields`, exactly like every other job question, and
every rule of the 2026-09-11 section still holds: nothing answered is lost when a question is
edited or archived, and nothing blocks creating or saving a card.

### What this AMENDS, explicitly

- **"Five types: text, number, dropdown, date, yes/no" (2026-09-11)** — a sixth, `paragraph`
  (multi-line text, 5000 characters). Still no multi-select.
- **"Answers are … NOT on the board card" (2026-09-11, upheld 2026-09-13)** — still no
  answer on the card. The card gains ONE small COUNT at the right of its icon row, "3/16"
  beside a clipboard tick (green when complete), only when the card's pipeline asks at least
  one Checklist question. M counts only the questions attached to THAT card's pipeline
  (AHS 16, Retail 13); a click opens the modal on the Checklist tab.
- **"Custom-field groups … None are seeded" (2026-09-13)** — the Checklist tab and its 16
  questions are created by a management command, `python -m app.checklist_seed` (dry run by
  default, `--commit` writes), NOT by a migration. It is idempotent on fixed `checklist_*`
  keys: a key that exists is left exactly as it is, so a re-run never duplicates and never
  undoes the owner's later edits. It refuses, writing nothing, on a missing or ambiguous
  pipeline name, two tabs called "Checklist", a `checklist_*` key held by a field of another
  type, or an unmigrated database. `job_type` stays in Opportunity details.
- **The Add opportunity dialog ("Optional — an opportunity can be filed without a contact")**
  is replaced by GoHighLevel's **Add new opportunity** modal (refs/round3/38),
  `components/AddOpportunityModal.tsx`, in the edit modal's family. **Primary contact is
  REQUIRED in the modal**; the API is unchanged, so the AHS relay, the Workiz import and the
  CLI still create cards with no contact. `POST /api/opportunities` additionally accepts
  `status`, `owner_id`, `follower_ids`, `business_name`, `source` (all optional), so one Create
  files the card with its checklist answers.
- **AHS card titles (2026-09-14 AHS amendment)** are now `"<Customer Name> - <job id>
  <service>"` ("Savannah Vanwyk - 84745849 ROOF"). The Workiz pairing contract is unchanged
  (`created_by = "AHS email"`, `ahs_job_id`, no `workiz_id`). Existing production cards are
  not retitled — the operator does that; a repeat delivery never touches a title.

### Question settings — three nullable columns (migration `a7d4c2e9f130`)

On `custom_field_defs`, each a SETTING on the question, never an answer:

- `script` TEXT — any question; drawn under the question in the modal.
- `linked_field` VARCHAR(40) — yes/no only: `contact_email` or `opportunity_address`. The
  modal draws the REAL value editable beside a "Verified" tick. The inputs are the modal's own
  Primary email and Address state, so editing them saves through the contact PATCH and the
  detail PATCH with those endpoints' validation and role rules (a TECH gets them disabled and
  the API refuses with 403; a bad email is a 422 and nothing moves). Nothing is copied into
  the answers.
- `details_when` JSON — dropdown only: the choices that open a details box. Its answer is
  stored beside the main one under `"<key>__details"` (`slug_for` collapses underscores, so
  no derived key can collide) and follows every `merge_answers` rule of its field: kept when
  not sent, kept when archived, refused on a pipeline that does not ask the field.

**Why columns, not the existing `options` JSON:** `options` is a list of strings every client
reads as the dropdown's choices (browser, CLI, CSV); changing its shape would break them, and
putting settings inside it would make a setting look like a choice. A setting on the wrong
type is refused (400), not ignored. Retiring a dropdown choice prunes it from `details_when`.
Migration: three `op.add_column`, all nullable, no default, no backfill, `down_revision =
b3e9a7c51d28`; proven on a throwaway SQLite at that head with production-shaped rows
(`test_checklist.py`).

### Judgement calls, all overrulable

- **The card badge looks for a tab NAMED "Checklist"** (case-insensitive). Renaming the tab
  removes the badge; its "N / M answered" heading follows the same rule. A per-group flag would
  have been a second migration for one tab.
- **"Answered" = not blank; `false` counts.** "No, not explained yet" is a choice the
  dispatcher made. A details box is not counted as a question.
- **A linked yes/no is a tick: ticked = Yes, unticked = no answer.** A plain yes/no (14, 15)
  keeps the existing --/Yes/No select, so "No" stays expressible.
- **The Checklist tab is ONE column**, read top to bottom as a call script; two columns left
  holes beside questions that span the row. Other custom tabs keep two.
- **Choosing a non-trigger option hides the details box and keeps what was typed.** Clearing
  is explicit, as everywhere in custom fields.
- **The Add modal names the card after the contact** when a contact is picked and no name
  has been typed (GoHighLevel's behaviour); a typed name is never replaced.
- **The Add modal sends only the chosen pipeline's answers** (`answersFor`), so answering an
  AHS-only question and then switching to Retail does not turn Create into a refusal.
- **A changed Primary email/phone is saved on the contact BEFORE the card is created**, so a
  refused email stops before any card exists.
- **The board follows the new card's status** after Create (the modal has a Status now).

### Screenshot assumptions (screenshot 38 shows only the top of Opportunity details)

The Checklist nav entry, the "N / M answered" heading text (13px, grey, right of the tab
heading), the script's style (13px rgb(102,112,133) under the label), the "Verified" tick box
beside the input, the address split (street full width, then City / State / Zip), the
"Details" box, the "+ New" text link beside the contact label and the "New contact" row at the
foot of the contact list, the Address group and custom fields below Business name / Source,
the error sentence left of Cancel, and the card badge are ours, built from the edit modal's
and the card's existing parts. The Settings → Custom Fields editor remains OUR design.

## AMENDMENT (2026-09-14): CompanyCam job photos in the CRM — the photos stay in CompanyCam

The crew photographs every job in CompanyCam (measured read-only on the owner's account:
1,759 projects, 837 with photos, 53,403 photos). The owner's decisions, implemented as given,
with the two changes the operator made during the build recorded below. Branch
`feature/companycam`. Code: `app/companycam.py` (the only module that talks to CompanyCam),
`app/companycam_api.py` (routes), `app/companycam_link.py` (the linking command), a thread in
`app/worker.py`; screens `components/CompanyCamPhotos.tsx`, `components/CompanyCamSettings.tsx`,
a section in `ContactDetailsPanel.tsx`.

### What is stored, and what is not

Four NEW tables, nothing else in the schema touched: `companycam_links` (card ↔ project, how —
`workiz_job` / `address` / `manual` / `created` — when, by whom, and an `unlinked_at`),
`companycam_review_items` (name-only matches), `companycam_sync_state` (one row: the hourly
check's heartbeat and cursor) and `companycam_project_requests` (cards waiting for a project,
UNIQUE per card). **No photo, no photo URL and no token is ever stored.** A link's
`project_name` is a display copy taken when it was linked. Both foreign keys to `opportunities`
are `ON DELETE CASCADE`: deleting a card is never refused over a photo link, and nothing in
CompanyCam is touched by it.

### The image bytes go through the CRM — the browser never loads a CompanyCam URL

`GET /api/companycam/photos/{id}/{thumbnail|web}` relays the bytes; the JSON the browser gets
carries only CRM paths. The brief asked whether CompanyCam's photo URIs are signed or expiring:
**that could not be measured** (no token on the build machine). The relay is right either way,
which is why it was chosen without the measurement:

- if the URIs do NOT expire, they are bearer links to photos of customers' homes — forwardable
  and alive after the viewer's session, role or pipeline access are gone;
- if they DO expire, a cached list hands the browser dead links, and re-resolving server-side
  is what the relay does anyway;
- per-pipeline access then applies to the picture itself: an image is served only when its
  project is linked to a card in a pipeline the reader can see;
- it is the call already made for Quo recordings (2026-09-11): stream, do not redirect.

The token is sent only to `api.companycam.com`; an image host is asked without it, and only a
host under `companycam.com` that answers 401/403 is asked again with it. Cost: bandwidth
through the VPS for four users, softened by `Cache-Control: private, max-age=3600`. Photo
lists are cached 2 minutes, projects 10 minutes, in process; **Refresh** bypasses both.

### Linking existing projects (decision 3) — AND the operator's first rule

`python -m app.companycam_link` (dry run; `--commit` writes; `--json`; `--status`) and the
hourly check apply ONE planner, in this order:

1. **Workiz job number (operator, 2026-09-14).** A project whose name matches
   `^Workiz (\S+) - ` links to THE card whose `custom_fields.workiz_id` is that job number
   (method `workiz_job`). Measured by the operator: all 441 Workiz-typed projects are named
   `Workiz <job #> - <customer>`, 374 carry a job number a card holds. Such a project does NOT
   also go through the address rule, so a repeat customer's other cards at that street do not
   receive it.
2. **Address.** Street number + normalised street name (USPS suffixes, directionals, ordinal
   words, unit designators dropped), the card's own address else its contact's. One card, or
   ALL cards sharing the address.
3. **Name only** (the contact's first + last name adjacent in the project name) → the review
   list, **never linked**. Settings → CompanyCam lists them for an ADMIN to link by hand
   (`manual`) or dismiss.
4. **No match** → left alone. Nothing creates a contact or a card from CompanyCam.

Counts printed (and nothing else — no name, address or project name): projects scanned, linked
by Workiz job number, linked to one card, linked to several, name-only for review, unmatched,
already linked. A second `--commit` changes nothing (tested as a hash of every row).

Judgement calls, all overrulable:
- **A different 5-digit ZIP on both sides is a different house**, even with the same street.
  Only when both ZIPs are known. This can only make the measured match counts smaller.
- **A city glued onto the street** (`14436 SW 95TH LN MIAMI`, the AHS dispatch format) still
  matches `14436 SW 95th Lane` — but only when the shorter street ends in a street suffix, so
  `123 MAIN` never swallows `123 MAIN ST`.
- **An ADMIN's unlink is permanent for that card**: the row is kept with `unlinked_at`, so the
  next sweep does not link it back.
- Archived projects are linked like any other (none exist today).

### The hourly check (decision 4)

The worker's CompanyCam thread ticks every 60 s. With `COMPANYCAM_SYNC_ENABLED=true` it runs the
check when an hour has passed since the last one STARTED (read from the database, so a restart
does not re-run it): projects updated since the last success minus 15 minutes, filtered
client-side on `updated_at` (`modified_since` is also sent, unmeasured — if CompanyCam ignores
it we read more pages, never fewer projects). **Once a day it reads every project**, which is
what links an old project to a card that arrived later (a Workiz import run by hand a week after
Workiz made the project). Heartbeat: `companycam_sync_state` — last started / finished /
success / full sweep, last counts, last error — shown on Settings → CompanyCam, by
`GET /api/companycam/status` (ADMIN) and by `python -m app.companycam_link --status`. An outage
or a bad token is `last_error`; the cursor does not move; nothing else stops. The Photos tab
says **"Photos are unavailable right now"**.

### Project creation — AMENDS decision 5: ON by default (operator, 2026-09-14)

The brief shipped creation switched off. **The operator overrode that**: creation is ON whenever
`COMPANYCAM_API_TOKEN` is set (`COMPANYCAM_CREATE_PROJECTS=false` turns it off). Everything else
in decision 5 stands.

- **Exactly two doors, plus the first address.** `POST /api/opportunities` (the Add opportunity
  modal, and `ghl opps create`) and `POST /api/ahs-jobs` (a new AHS work-order card) record a
  request when the card has a street; `PATCH /api/opportunities/{id}/detail` records one when a
  card that had no street is given one. Nothing else records a request — not the Workiz importer,
  not a drag, not a bulk action.
- **A card the Workiz importer made never creates** — `created_by = "Workiz import"`, or a
  `workiz_id` on a card not created by an AHS email. Its project already exists; it links.
- **Search first.** The worker pages `GET /projects?query=<street>` and compares addresses
  itself; any project at that address is LINKED (`address`) instead of creating a duplicate.
- **Then create once**, and never twice for one card: the request row is UNIQUE per card, a card
  holding any link is finished without a request to CompanyCam, and the state is committed as
  `creating` BEFORE the POST, so a crash between CompanyCam answering and our commit is followed
  by a search that finds the new project. Up to 5 attempts, then `failed`.
- **Named like Workiz's projects** (operator's answer, from the measured `Workiz <job #> -
  <customer>`): an AHS email card → `AHS <ahs_job_id> - <customer>`; any other card →
  `CRM <opportunity id> - <customer>`. The customer is the contact's name, else the card title.
- **`primary_contact`** = the contact's name, email and phone (blank ones omitted).
- **The AHS path's "Nothing is notified" guarantee is unchanged**: a creation request is a row in
  `companycam_project_requests`, not a `jobs` row, and it texts nobody. The AHS module itself was
  not edited; the route records the request after `deliver_job` returns 201.

### The one write — REPLACES "creation OFF sends zero non-GET requests"

`_request` refuses every method but GET before a client exists — except `POST /projects` while
creation is on. So **the only non-GET request this package can ever send is the project-create
POST, from the two doors above, never for a Workiz-imported card**. There is no upload, delete,
edit, tag or webhook call (decision 6: view only). `test_companycam.py` drives the whole package
— linking run, hourly check, photo reads, image relay, contact panel, admin page, unlink, card
delete, the Workiz importer, a hand-made card, a card given its first address, an AHS email, a
Workiz card given an address — through a recording transport and asserts exactly three POSTs,
to the three expected cards; a source test pins that `_request` has one POST call site.

### Who sees what (decision 7)

Viewing: every signed-in role, TECH included. Linking, unlinking, the review list and the status
page: ADMIN. Per-pipeline permissions: every read resolves the card through `pipeline_access`
(404 exactly like a missing card); the contact panel lists only projects on the contact's
VISIBLE cards; an image needs a link to a visible card. The routes are on
`test_pipeline_permissions.py`'s audited list, whose module filter now covers
`app/companycam_api.py`.

### Screens — OURS, not measured

GoHighLevel has no CompanyCam integration in the owner's screenshots, so all of it is ours,
built from the opportunity modal's parts (refs/opps/11, 22): the **Photos** nav item after
Associated objects; its heading row with Refresh and "Open in CompanyCam"; project groups only
when a card has several; a 4-across square thumbnail grid (`auto-fill, minmax(140px, 1fr)`),
`loading="lazy"`, "Load more photos" per 50; the dark full-screen viewer (portal on `<body>`,
above the status dot) with ‹ › and ← → Esc, "n of N", date in the account zone, "Photo by",
"Annotated", description, "Open in CompanyCam"; the contact panel's collapsible "CompanyCam
projects (n)" section, drawn only when there is at least one (so a contact with none renders the
measured panel unchanged), each opening a project dialog; and Settings → CompanyCam (ADMIN only).
Newest first is decided in the browser across loaded pages, because CompanyCam's own photo order
is not measured. Checked in headless Chromium at 1440×900 against a throwaway database and a fake
CompanyCam.

### Not verified without the real token

The request body `POST /projects` accepts (`name`, `address{…}`, `primary_contact{name, email,
phone_number}` — shaped from CompanyCam's public docs, unwrapped); whether `GET /projects` honours
`query` and `modified_since`; `GET /photos/{id}` (used only when an image is asked for before its
list was read); whether photo URIs are signed/expiring and whether the image host wants the token;
how fast a just-created project becomes searchable; CompanyCam's rate limits (429 is retried with
`Retry-After`, pages are 0.25 s apart).

### Migration `c4e8a2f6b913`, on `a7d4c2e9f130`

Written after `feature/opportunity-checklist` merged (e69a9ae), on the single head it left. Four
CREATE TABLEs and their plain `op.create_index`es, nothing else — no ALTER, no DROP, no UPDATE, no
batch mode. `tests/test_companycam_migration.py` stands a throwaway SQLite up at `a7d4c2e9f130`
with production-shaped rows, upgrades, and asserts every pre-existing table has identical columns
and identical rows, exactly the four new tables appear and are empty, the server defaults fill,
a card delete cascades its link and request rows with foreign keys enforced, and a downgrade /
upgrade round trip is clean.

## AMENDMENT (2026-09-14): dial any number from Conversations, one in-call window, and the browser answers its own outbound leg

The owner's decisions for `feature/call-dialer`: a "Call a number" dialer on the Conversations
page; calling requires the browser phone to be Ready; an in-call window like owen-main's
`InCallModal`; the browser auto-answers its own outbound leg and never shows "Incoming call" for
it; after the call, the duration and "Add as contact" (unknown number) or "Open conversation".

### What this AMENDS

- **"The browser softphone is OURS" (2026-09-11) — "it drives its own leg only: answer, hang up,
  mute"** → also sends keypad tones (DTMF) and switches microphone/speaker. Still no hold,
  transfer or bridging from the browser (see below).
- **`_place_call` — "the conversation happens on real handsets"; owen-main rings the binding's
  `outbound_operator`** → every call route (`/api/contacts/{id}/call`,
  `/api/conversations/{id}/call`, `/api/number-threads/{id}/call`, and the new
  `/api/calls/dial`) takes an optional body `{"ring_browser": true}`. With it, owen-main is asked
  to ring **the signed-in user's own operator first** (`operator` = the principal's email, which
  owen-main slugs with the same `operator_slug` its credential minting uses). The browser sends
  it only while its phone is Ready and idle; otherwise the call rings the binding's default
  operator exactly as before. The operator is never taken from the request — a client that names
  one is ignored (tested).
- **The thread header's phone icon and the board card's call icon** now go through the same
  launcher, so a call placed from them while the phone is Ready rings this browser and opens the
  in-call window. With the phone not Ready they behave as before.
- **The status dot's hover card** closes on a press or focus anywhere outside it. Found in the
  headless run: after "Switch on" the focused button is replaced, a removed element never fires
  `blur`, and the card stayed open over the in-call window.

### `POST /api/calls/dial` — not a second dialling path

Body `{number, ring_browser}`; ANY_USER, like the other call routes. The number is validated by
`dial_problem` (North American only, 10 digits, NANP area code/exchange, not the bound DID) —
the browser's `lib/dialPad.ts` says the same sentences word for word, and a test runs both on the
same inputs. Then: a number a **contact** holds (last ten digits, lowest id) is exactly a contact
call (DND refused, logged on their thread); anyone else goes through `_dial`, and **only once
owen-main has accepted** is the number-only thread found or created and the call logged there. No
contact is ever created. Every refusal is a 200 with `placed: false` and a sentence, and writes
nothing — not even an empty thread.

### Placement: the inbox header, not the rail

A phone icon left of the Team inbox header's measured filter and sort icons (which do not move).
It is an action on the inbox, where GoHighLevel puts its compose icon. The rail was rejected:
each rail row is a *view* whose highlight states the filter applied to the list (`railActive`),
and a dialer is not a view.

### Outbound intent — owen-main's design, plus a number match

Ported from owen-main `lib/outboundIntent.ts`: module scope, marked at ONE choke point
(`lib/callLauncher.ts`, before the request leaves), TTL 45 s, single-shot, cleared on refusal or
error. **Added:** the INVITE's caller-ID must match the dialled number (last ten digits). owen-main
claims whatever INVITE arrives first, which would auto-answer a real customer who rings during
those seconds; here their INVITE shows as an incoming call and the intent waits for the leg that
matches. An INVITE with no readable caller-ID is never claimed. Known limit: if the very number
being dialled happens to call in during those seconds, it is indistinguishable and would be
answered as the outbound leg.

### Hold and transfer: NOT built, and what owen-main would need to expose

`POST /api/crm-link/calls` does return `operator_channel`, `callee_channel` and `linkedid`, so
for an OUTBOUND call the CRM backend could know the channels. But nothing the CRM can call acts on
them: owen-main's `/api/telephony/control/hold` and `/control/transfer` authenticate an owen-main
**user login** (`current_user`), not the `crm_link` API key, and for an INBOUND ring-group call
the CRM receives no channel id at all (the operator leg's INVITE carries only caller-ID and the
dialled DID). So neither is rendered. owen-main would need:

1. `POST /api/crm-link/calls/{linkedid}/hold` `{hold: bool}` and
   `POST /api/crm-link/calls/{linkedid}/transfer` `{kind, target}` under `require_scope(crm_link)`,
   acting only on calls on a bound DID and resolving the far-party channel itself from the
   linkedid (so the CRM never handles raw channel ids);
2. for inbound calls, the linkedid on the operator leg — e.g. an `X-OWEN-Linkedid` SIP header on
   the INVITE `ring.py` originates (SIP.js exposes `request.getHeader`), or
   `GET /api/crm-link/calls/active?operator=<slug>`;
3. for transfer, a list of valid targets the CRM may offer (`GET /api/crm-link/transfer-targets`).

### Judgement calls, all overrulable

- **The in-call window shows while `phase === 'in-call'`**; the few hundred milliseconds of
  auto-answering (`connecting`) are shown by the dialer that placed the call. A call started from
  a thread or card icon shows nothing during that moment.
- **The timer starts when THIS browser's leg connects**, not when the customer answers: owen-main
  sends the CRM no event for the callee answering. The first seconds are ringback and the
  recording notice.
- **"REC" is always shown during a call.** owen-main records every bridged call on the bound DID
  (`OUTBOUND_RECORDING_ENABLED`, and the ring group records), but the CRM is not told per call. If
  recording is ever switched off there, this indicator would be wrong; owen-main returning
  `recording: bool` from `/calls` would fix it for outbound.
- **The dialer's own microphone failure** (the auto-answer throws) is reported in the dialer as a
  sentence, never as an incoming-call card — owen-main's lesson.
- **A TECH may dial** (ANY_USER, like every call route) but gets "Add as contact" disabled with
  "Only staff can add contacts", as everywhere else.
- **The after-call view stays until closed** (or until the next call), so "Add as contact" cannot
  vanish while somebody is reaching for it. It never saves: it opens the existing Add Contact
  form, prefilled with the number.

### Screenshot assumptions (refs/round3/41 shows only where the dialer goes)

GoHighLevel's dialer and in-call UI were never captured. OURS, styled from the rebuilt
Opportunities modal (refs/opps/04) and dropdown card: the dialer modal (340 px, 16px/600 title,
close cross, 13px/500 label, 40 px number field with a backspace glyph, a 3×4 keypad of 48 px keys
with letters, a bordered "Calling from (954) 482-9099" row, Cancel + blue Call); the in-call
window (320 px, fixed top-right under the status dot at `top: 52px`, avatar + name/number +
"Outbound call · 0:12", red "REC", four 44 px round buttons Mute / Keypad / Audio / Hang up, an
expanding keypad with a readout, and Microphone/Speaker selects); the after-call view (duration +
one full-width blue action). No emoji; icons are the app's outline set (`components/Icon.tsx`).
Not draggable (owen-main's is) — it sits where the call's status dot is.

### Verified, and not

Verified in headless Chromium against a throwaway SQLite database with the SIP user FAKED at the
dev server (`frontend/e2e/`) and every owen-main hop replaced by a recorder that refuses real
requests — `uv run python -m tests.browser_dialer`, 58 checks. **No real call has been placed.**
Not verified: a real SIP.js `sendDTMF` reaching Asterisk, `replaceTrack` on a live
RTCPeerConnection when the microphone changes, `setSinkId` on real hardware, the operator leg's
caller-ID actually arriving as the dialled number (it is owen-main's `caller_id=callee_number`,
read from source), and owen-main ringing `operator=<email>` for a provisioned CRM user.

## AMENDMENT (2026-09-15): "Only assigned data" — a technician sees and works only their own jobs — and My Staff

The owner's decisions, implemented as given; the operator's addendum of the same day adds the
My Staff screen that is this setting's home. Security work in the class of per-pipeline
permissions (2026-09-13): every read path was enumerated and each is proven by a test.

### What this AMENDS, explicitly

* **"Internal notes (NOTE, INTERNAL_COMMENT) are STAFF-only" (2026-09-10) — narrowed, on the
  record.** A TECH with "Only assigned data" on may now READ and ADD an **opportunity's own
  notes on their own jobs** (`assigned_access.sees_opportunity_notes`). They may not edit or
  delete one. Conversation-thread `NOTE` / `INTERNAL_COMMENT` events stay STAFF-only exactly as
  before — including the contact's thread notes the Notes tab lists under a deal's own, which a
  TECH is not sent — and so does an appointment's "Internal notes". A TECH still cannot see any
  note on a job that is not theirs (404), and a TECH with the switch OFF is refused 403 as before.
* **"Everyone reads" (Auth — BUILT).** No longer true for a user with the switch on.
* **"A TECH … cannot edit records" (CLAUDE.md).** A restricted TECH, on their own job, may
  answer its custom-field / Checklist questions (the answers only — the "verified" tick, not the
  contact email or card address beside it), move its stage from the modal as well as by drag, add
  a task and complete a task that is theirs (assigned to them or added by them).
* **Settings gains "My Staff"**; `GET /api/users` gains `q` / `role` filters and, for an ADMIN
  only, `phone`, `only_assigned_data`, `must_change_password` and `machine`.

### The rule

* **`users.only_assigned_data`**, ON by default for a user CREATED as a TECH, OFF for everyone
  else; an ADMIN toggles it in Settings → My Staff. **An ADMIN is never restricted** (the same
  "ADMIN always" per-pipeline access has): turning it on for an admin is refused 400, and
  promoting a restricted user to ADMIN clears it. It is read from the user row on every request,
  so a change applies on the next click with no re-login.
* **"Their jobs"** = opportunities they OWN, plus opportunities with a **non-cancelled**
  appointment on a calendar they own (`calendars.user_id`) or assigned to them
  (`appointments.assigned_user_id`). Per-pipeline permission still applies on top: a job in a
  pipeline they cannot access is not visible even if it is theirs.
* Everything a restricted user can see follows from that, in `app/assigned_access.py`, which
  returns `None` — no extra filter, byte-identical SQL — for everyone unrestricted. A lookup by id
  of anything outside answers the same 404 a missing id answers.

### Every read path, and how it is filtered

`tests/test_only_assigned_data.py::AUDITED` records all 118 `/api` routes; a route added later
fails `test_every_route_is_on_the_audited_list` until somebody records how it enforces this, and
`test_every_route_reading_customer_records_goes_through_a_scope` checks the recorded answer is
true in the source.

| Surface | Filter |
|---|---|
| Board, `/api/opportunities`, saved views applied | `assigned_access.opportunities(scope)` |
| Deal by id: modal, drag, detail PATCH, tasks, notes, photos, delete | `pipeline_access.get_opportunity` → 404 (the one chokepoint) |
| Bulk stage / owner | `_bulk_load` through the scope → the whole request 404s |
| Pipeline list stage `count` / `value_cents` | only their jobs, so a column header cannot show others' money |
| Contacts list (+ total), contact by id, tags on / contact PATCH | contacts of their jobs (primary + additional) → 404 |
| Contact panel's opportunities and appointments | only their jobs / only their calendar's visits |
| Tag counts | over their contacts |
| Inbox, Unread badge, thread events, mark read / star, send, call, open thread | threads of their contacts → 404; **no number-only thread at all** |
| `/api/calls`, `/api/messages` | `_search_events(scope)`, rows and totals |
| ctrl+K `/api/search` | all three groups over one scope, items and totals |
| OpenPhone recording relay | only a recording on one of their threads, else the "no recording" 404, nothing fetched |
| Incoming-call caller name | blanked for a customer not on their jobs |
| "Call a number" dialer, `POST /api/calls/dial` | rings only a number one of their contacts holds; any other number is refused 200 `placed: false` with the same sentence whether or not someone else's contact holds it, nothing rung or written |
| Calendars list | calendars they own + calendars holding a visit assigned to them |
| Appointments list / by id / edit / cancel | on their calendar or assigned to them → 404; a deal link is shown only for their job (a cancelled visit lends no title) |
| Blocked time list / by id / edit / delete | on calendars they own → 404 |
| CompanyCam: card projects, photo pages, image relay, contact projects | card must be their job; an image needs a link to one |
| Dashboard, funnel, Forecast, Call report, Appointment report | **403 "…not available to a user with “Only assigned data” on — they show money and other people's work."** |
| `ghl` CLI | only through the API (driven for real in the test) |

### What a restricted user may DO, and what is refused (nothing mutated — re-read in tests)

Allowed on their own job: move stage (drag, bulk, modal), answer questions (`custom_fields`),
add notes, add tasks, complete their own tasks, text and call the customer through the existing
guarded paths. Refused 403 on their own job: title, value, status, pipeline, owner, followers,
source, business name, address, primary / additional contacts, the contact's details, editing or
deleting a note, editing or deleting a task, completing someone else's task, deleting anything.
The detail PATCH answers "a technician can answer this job's questions and move its stage, but
not change …" naming the refused fields; the modal disables those controls with that reason.

A restricted DISPATCHER is filtered exactly the same; their ROLE still decides what they may do
with what they can see (they may edit their jobs; they cannot reach a job, contact, visit or
block that is not theirs — 404). Creating a contact, deal, visit or block is not narrowed.

### My Staff (operator addendum)

* **Settings → My Staff, ADMIN only** (the tab is not drawn for anyone else, and every write is
  `auth.ADMIN`). GoHighLevel's table (refs/round3/42): initials avatar + name; email with the user
  id and a copy button beneath; phone; User Type (Admin / Dispatcher / Technician, drawn in the
  screenshot's small capitals) with "Only assigned data" beneath when on; edit and deactivate
  icons; a User Role filter; a search box (name, email, phone, id — server-side); "+ Add User";
  Page N / Previous / Next (10 per page). **Machine accounts** (no password — the telephony feed)
  are listed in their own table beneath and can never be given a password (400).
* **Add / Edit User modal**, in the Create pipeline modal's style (no screenshot of GoHighLevel's
  exists): first and last name (stored as the one `users.name`, split at the first space when
  editing), email, phone, role, the "Only assigned data" switch (follows the role's default until
  touched, then stays as set; drawn off and locked for Admin), and a first password on Add /
  "Reset password" on Edit. **No per-module permissions (Q4).**
* **Deactivate, never delete (Q1).** The trash icon deactivates: sign-in refused, `token_version`
  bumped so every browser session ends now, every API token a PERSON holds revoked. Their name
  stays on jobs, notes and tasks; an ADMIN reactivates (a revoked token stays revoked — they mint
  a new one). A MACHINE account's tokens are refused while it is inactive but not revoked, and work
  again on reactivation: a token's secret is shown once, and destroying the telephony feed's is
  how the live credential was lost before. **An admin cannot deactivate or demote themselves
  (400), and the last active ADMIN who can sign in can never be deactivated or demoted (409)** —
  a token-only ADMIN does not count, since it cannot open My Staff to put things right.
* **Forced password change (Q2).** A user created by an admin, and a password reset by an admin,
  sets `users.must_change_password`. Until they change it, `auth.require_auth` answers every
  request except `/api/auth/me`, `/api/auth/password` and `/api/auth/logout-all` with 403 "choose a
  new password before doing anything else" — server-enforced, so no token can be minted and no
  record read; the browser shows only the change-password card. The new password must differ from
  the one given. No email invitation is sent.
* **A new Technician gets a calendar (Q3)**, owned by them and named "<Name>", in the same
  transaction. Never a second: a user who already owns a calendar gets none. If a calendar with
  that exact name exists (someone else's or nobody's) it is left completely alone — handing it
  over would give them every visit already booked on it — and theirs is "<Name> (2)", "(3)"…; the
  response and the notice say so. Only on creation: changing a role later creates nothing.
* **Phone on users (Q5)**, stored as `store_phone` stores a contact's, shown formatted.

### Migration `b5d1e8f3a276`, on `c4e8a2f6b913` — ONE revision

Three `op.add_column` on `users` and nothing else: `only_assigned_data` BOOLEAN NOT NULL
server_default false, `phone` VARCHAR(40) NULL, `must_change_password` BOOLEAN NOT NULL
server_default false. Every existing user — every production TECH included — keeps their access
(false), keeps their password without being forced to change it (false), and has no phone.
`tests/test_users_migration.py` stands a throwaway SQLite up at `c4e8a2f6b913` with users, a
machine token, a calendar and a deal, upgrades, and asserts every table keeps its columns and rows
byte-for-byte, `users` gains exactly the three columns reading false / NULL / false, the server
defaults fill, the round trip is clean, and `upgrade()` is three `add_column` calls.

### Judgement calls, all overrulable

* A deal's ADDITIONAL contacts count as "contacts of their jobs" (the job modal already names them).
* A cancelled visit still SHOWS on the tech's calendar (it is on their day); it only does not make
  its deal a job, so its deal link is blanked.
* The calendar page's Users filter offers a restricted user only themselves; the calendar list
  holds their own calendars and any calendar with a visit assigned to them.
* A drag on a restricted board lands above the card the tech dropped on, counting only the cards
  they can see, so other people's cards keep their order.
* Dashboard / Reporting are not drawn, Forecast is not drawn (not dimmed), and a restricted user
  who opens a link to one lands on Opportunities.
* Reporting refuses with 403 and a sentence rather than 404: the screens exist for everyone and
  hide nothing's existence; the user needs to know why.

### Screenshot assumptions (what 42 and 43 do not show)

GoHighLevel's third action icon (a boxed ✕) is not built — it has no counterpart here. The
Reactivate icon, the "Deactivated" and "Password change pending" chips, the Machine accounts
table, the deactivate confirmation, the Add/Edit modal, the change-password card, the role-filter
option list, the empty and no-match states, and the page size (10) are ours, in the Pipelines
screen's and pipeline modal's style. Settings keeps its existing tab row (My Staff is a tab there)
rather than GoHighLevel's separate Settings sidebar in screenshot 43.

### What the operator must do on production

1. Rehearse, then `./deploy.sh --with-migrations` (revision `b5d1e8f3a276`).
2. **Every existing user stays OFF.** Turn "Only assigned data" on, in Settings → My Staff, for
   each technician who should be limited. Before doing so, make sure their jobs are theirs: set
   them as the OWNER of their cards, or book their visits on a calendar they own / assign the
   visits to them — a restricted tech with neither sees an empty board.
3. Existing technicians have no calendar of their own unless one was made; creating one is only
   automatic for users added through My Staff.
4. Nobody existing is forced to change a password; resetting one in My Staff forces it.

## AMENDMENT (2026-09-15): the Workiz `Tech` column lands on cards and assigns the job's appointment

The owner's decision, "assign the job's appointment". AMENDS "The Workiz import — the real
business data arrives (2026-09-11)" and, for exactly one key, the modal rule of 2026-09-13 that
no `owen_*` / `workiz_*` field is drawn anywhere. No migration, no API route, no new column.

### What the importer does with `Tech`

* **Names on the card.** `custom_fields.workiz_tech` = the job's `Tech` column as a list, in the
  export's order, whitespace collapsed; a name repeated in any case is kept once where it first
  appears. "Sheila & Leo" is one Workiz technician (a crew) and stays one name. A re-import
  follows Workiz; a job whose Tech is now empty loses the key. Paired AHS email cards get it too
  (same job). Read-only through the API like every `workiz_*` key (400, nothing written).
* **Names to users.** `--tech-map PATH`, a JSON object `{"Workiz name": "user email"}`, wins;
  its keys match case-insensitively with whitespace collapsed; `null` leaves a name unmapped on
  purpose even if a user has that name. Without an entry: exactly one ACTIVE user whose full name
  equals the Workiz name, case-insensitively with whitespace collapsed. A map entry naming a
  deactivated or unknown user, two active users with the name, or no user at all is UNMAPPED and
  reported with the reason. Machine accounts (no password) are never matched by name. **No user
  is ever created.** An unreadable / malformed map exits 4 before anything is planned or written.
* **The appointment.** For a job with a FUTURE appointment (the only kind the importer books), the
  visit is assigned to the FIRST mapped technician in job order. **It stays on "Workiz Jobs
  (imported)"** — see below. **Card owners never change.**
* **Whose assignment it is.** `custom_fields.workiz_tech_assigned_user_id` on the card records the
  user this importer assigned (an appointment has no JSON column; one card has one Workiz-calendar
  visit). A re-import may change (`reassign`) or remove (`clear`) the assignment ONLY while the
  appointment's `assigned_user_id` still equals that record. Anything else a person did is left
  alone and listed: assigned someone else (`manual`), cleared the importer's assignment (`manual`
  — a person's "nobody" is a choice too), or assigned the very tech Workiz names before the
  importer did (`already`, not recorded as the importer's, so a later removal in Workiz leaves it).
  A tech who stops mapping (deactivated, map changed) clears only an importer-made assignment.
  The rule is one pure function, `workiz_import.tech_assignment`, decided at plan time for the
  report and again against the row at write time.
* **Past visits are never touched.** A visit that was future when it was assigned and is past now
  is not in the plan's appointments, so its assignment stays as it is.

### Why the visit is not moved to the technician's own calendar

Checked, and nothing needs it: "Only assigned data" already counts a non-cancelled visit
*assigned* to the user (`appointments.assigned_user_id`) — the restricted tech's board and the
card by id follow (proven through the API in `test_workiz_techs.py`), and the contact, thread and
calendar list follow from the same `assigned_access` rule; the Calendars page's Users filter is by `assigned_user_id`, so the visit
shows under the technician. Moving it would change the owner's recognised "Workiz Jobs (imported)"
calendar and break the importer's own lookup (card + Workiz calendar), making the next run book a
second visit. **One consequence, proposed rather than done:** a technician who filters Calendars by
their *personal calendar* (rather than by user) does not see Workiz visits, and blocked time on
their personal calendar does not warn about one. If that matters, the fix is in the calendar page
(treat "my calendar" as "my calendar or assigned to me"), not in the importer.

### The dry run's "Technicians" section (no customer data — Workiz tech names, staff, Job #s)

Per Workiz name: how many of this run's cards carry it, and the CRM user (name, id, "by map file"
/ "by name") or `UNMAPPED: <why>`; map entries on no card; cards carrying names / with none /
whose names change / are cleared (Job #s); future appointments to assign / reassign / clear /
manual left alone / already assigned by a person / unchanged (Job #s). `--json` carries the same
under `technicians`. Written counts: `appointments_tech_assign|reassign|clear|left_alone`.

### The modal

"Workiz technician(s)" in Opportunity details, full width after Tags: the names joined with ", ",
a read-only input in the disabled grey, for every role that can open the card, hidden by "Hide
empty fields" when the card has none. `changedAnswers` still never sends a reserved key. No
screenshot shows this field (GoHighLevel has no such data); it follows the modal's own label +
input look. Driven in headless Chromium by `python -m tests.browser_workiz_tech`.

## AMENDMENT (2026-09-15): AI Agents, phase 1 — the foundation

The owner wants GoHighLevel's AI Agents module, "scalable and customizable". Build order agreed
with him: **1 Foundation (this)**, 2 text follow-up in Suggest mode, 3 voice Receptionist on
owen-main, 4 outbound (locked). Phase 1 is built so 2–4 are configuration plus small additions.
Branch `feature/ai-agents-foundation`, migration `d7c3a9e5f214` on `b5d1e8f3a276`. Code:
`backend/app/ai/` (its `__init__.py` is the map), `frontend/src/pages/AiAgentsPage.tsx`,
`frontend/src/components/ai/`, `components/AiConnectionsSettings.tsx`, `AiAlertBell.tsx`,
`AiSuggestions.tsx`, `lib/aiAgents.ts`.

### What this AMENDS, explicitly

* **"Scope — v1 … Out: … AI Agents"** — AI Agents is in scope, Text/Chat agents only.
* **"The three remaining dimmed items — AI Agents, Automation, Media Storage — were left exactly
  as they are" (2026-09-10)** — AI Agents is a REAL sidebar row, first after the divider where
  GoHighLevel has it (refs/round3/43), drawn only for an ADMIN or a DISPATCHER without "Only
  assigned data"; for anyone else it is not drawn at all. Automation and Media Storage stay
  dimmed. `test_frontend_nav.py` pins both halves.
* **"A task notifies nobody" (2026-09-13)** — still true of every task a person or an agent's
  "create task" makes. An agent's ESCALATION is the one exception: an **urgent** task
  (`opportunity_tasks.priority`, new column, `normal` for every existing task) assigned to the
  agent's escalation users, AND an in-app alert. This CRM had no notification mechanism, so a
  minimal one was added: `ai_alerts` and a bell beside the status dot (`AiAlertBell.tsx`).
  Only escalations write alerts today.
* **"Rule 1, missed call → auto text back — DISABLED" (2026-09-13)** — unchanged, and the
  worker still refuses its job. An agent's "Unanswered inbound call" trigger is a separate,
  per-agent configuration, and every agent is created Off.
* **"No soft delete anywhere"** — deleting an agent ARCHIVES it (`ai_agents.archived_at`): its
  runs, versions and suggestions are log records. That rule is for this new table only.
* **The booking, rescheduling, cancelling, stage-move, answer, note and task code paths were
  extracted into service functions** the staff routes now call (`main.book_appointment`,
  `edit_appointment`, `cancel_appointment_record`, `move_to_stage`, `answer_questions`,
  `opportunity_workspace.add_note`, `add_task`). Behaviour of every route is unchanged. Four
  pre-existing test files were edited, each for a change recorded here: `test_only_assigned_data.py`
  (the new routes on its audit list, and its source fence accepts `book_appointment(` as the
  scoped call), `test_frontend_nav.py` (AI Agents is live), `test_connection_status_ui.py` (the
  Dashboard margin and the bell) and `test_pipelines_ui.py` (the new Settings section label).

### The owner's decisions, as built

1. **Module & roles.** A page with PageTabs: Agents · Knowledge Base · Templates · Agent Logs.
   ADMIN: everything. DISPATCHER: opens the module, reads agents / knowledge bases / templates,
   runs an agent by hand, approves or dismisses suggestions — and nothing else; Agent Logs is
   not drawn for them and `/api/ai/runs`, `/metrics`, knowledge gaps, connections and settings
   answer 403. TECH and every restricted user: **403 on every `/api/ai` route**
   (`test_ai_permissions.py` enumerates the router).
2. **Structured, prompt-based agents.** Name, folder, channel, description, role/persona, goals,
   do / don't rules, knowledge bases, allowed actions, triggers, schedule (days + hours,
   America/New_York; outside = do not run), mode, sleep-when-staff-reply (default on), wait
   minutes, max messages per conversation, connection + model, escalation users, extra
   instructions. `prompt.compile_prompt` assembles the system prompt DETERMINISTICALLY (fixed
   order, no timestamp, no id, no customer data) and the builder shows it read-only.
3. **Versioning.** Editing saves the draft; Publish writes an immutable
   `ai_agent_versions.config` (owen-main's `agent_versions` shape — draft, version and template
   are one dict shape, `config.py`). Runs record `version_id` / `version`. Nothing is pushed to
   owen-main in this phase.
4. **Channels.** `text` and `voice` exist; a Voice agent cannot be created (400 naming phase 3)
   and the browser does not offer it. Voice-only actions (transfer_call, end_call) are in the
   catalogue and offered to no Text agent.
5. **AI Connections** (Settings → AI Connections, ADMIN): any number; Anthropic (official
   `anthropic` SDK, Messages API + custom tools + tool_result loop), OpenAI and
   OpenAI-compatible with a base URL (official `openai` SDK, chat.completions + tools), behind
   ONE interface (`providers.py`). New Anthropic connections prefill `claude-sonnet-5`; "Load
   models" lists what the provider lists; price per 1M input/output prefilled for known models
   and editable. **Test** makes one minimal request and writes nothing. To Claude nothing
   sends `temperature`, `top_p` or a thinking budget; thinking is left at the model default;
   the system block carries `cache_control`; `refusal` and `max_tokens` end a run without
   running any tool call from that turn; tool inputs are validated against their schema.
   **Keys:** Fernet under `AI_SECRETS_KEY`; saving without it is refused (503) with the
   sentence telling the operator what to set; only "•••• last4" is ever returned; a provider
   error body is redacted before it becomes a sentence (an OpenAI 401 quotes part of the key).
6. **Knowledge bases.** FAQs, articles, files (PDF via `pypdf`, .docx via `python-docx`). **A
   file keeps its extracted text, name, type and size — not its bytes: this CRM has no file
   store.** Chunked; retrieval is lexical BM25 over stored word lists (same on SQLite and
   PostgreSQL) behind `knowledge.Retriever`, scoped to the agent's attached knowledge bases.
   A search in which no chunk holds at least half the question's words is "nothing useful":
   the model is told not to guess, and a knowledge gap is recorded (deduplicated on the
   normalised question, count, last seen, run). Resolving a gap writes the FAQ. Customer facts
   come from `get_context`, never from a knowledge base.
7. **Triggers** (`triggers.py`): unanswered inbound call, inbound text unanswered for N minutes,
   opportunity enters stage X, appointment booked / rescheduled / cancelled, manual. Each
   enqueues ONE `ai_agent_run` job through `app.queue` with a dedupe key. A staff text or call
   puts sleeping agents to sleep on that customer and cancels their queued runs, **releasing
   the job's dedupe key** the way `_drop_pending_reminders` does. A number no contact holds
   never triggers anything.
8. **Actions** (`actions.py`): read context, search knowledge, send text, book / reschedule /
   cancel appointment, fill checklist answers, add note, create task, move stage, escalate,
   report knowledge gap. Every write goes through the staff service functions as a
   DISPATCHER-level system principal with no user id, recorded "AI: <agent name>" through
   `ai_agent_id` on `conversation_events`, `opportunity_notes`, `opportunity_tasks`,
   `appointments` (and `number_thread_events`, column for column). **Off** never triggers;
   **Suggest** turns every write into a pending suggestion executed exactly once on approval
   (a conditional UPDATE); **Auto-pilot** is ADMIN-only and needs `confirm: true`.
9. **Escalation:** urgent task per escalation user (on the run's opportunity, or the customer's
   only open one), an alert each, a staff-only NOTE on the thread; **emergency** also texts the
   on-call phone through `get_transport()` — LOGGED_ONLY / refused today.
10. **Try-it:** the DRAFT (unsaved edits included), optionally as a chosen contact/opportunity.
    The model and the read tools run for real; every write is a "Would: …" card; logged
    `is_test`. ADMIN only.
11. **Templates:** "Save as template" copies the draft; "Create agent from template". None ship.
12. **Agent Logs & Metrics** (ADMIN): every run including skips, with version, trigger,
    subject, mode, test flag, transcript, actions executed / suggested / refused / would with
    reasons, connection, model, tokens (in/out/cache write/cache read), cost, latency, outcome.
    Kept indefinitely. Metrics exclude test runs.

### Safety, and how each rule is enforced (all tested)

* **Nothing runs by itself after deploy.** Every agent is created Off (column default and
  API); `ai_settings.paused` ("Pause all AI agents") is checked when a trigger fires AND
  before every run; a paused trigger is logged, not queued.
* **No real model call in tests or on this server.** `tests/ai_guard.py`, installed by
  conftest.py, refuses DNS and connections to api.anthropic.com / api.openai.com and FAILS any
  test that attempted one; `test_ai_network_guard.py` proves it catches the real SDK path.
  Providers are mocked at the HTTP boundary (`providers.HTTP_CLIENT_FACTORY` + httpx2
  MockTransport), so the real SDK code builds and parses every request.
* **Agents never create contacts or opportunities:** no such action exists, and a write
  needing a deal that does not exist is refused with the reason.
* **Texting stays dark:** `send_text` is `automations.send_outbound` — the composer's path,
  the same transport, DND and no-phone suppression. No new transport. OpenPhone/Quo is no
  channel.
* **Untrusted input:** customer texts are fenced as data in the first message; the prompt
  says so; and it does not matter if the model obeys them anyway — every call is checked
  server-side: schema (no extra keys, so a `contact_id` is refused), the run's own contact and
  opportunity, per-pipeline access (a pipeline restricted to named users is hidden from
  agents), the service's own validation. A refusal mutates nothing.
* **A trigger never breaks the request that fired it:** each hook runs in a SAVEPOINT and
  swallows its own failure. **An agent's own change does not trigger agents** (a contextvar
  set while an action executes), so agents cannot loop.
* **A failed run is never retried** (it could text a customer twice): the handler logs
  `error` and returns.

### Judgement calls, all overrulable

* `get_context` does NOT include internal notes: they are the team talking to itself, and not
  sent to a model provider.
* A staff reply is a text, email or call a PERSON sends from the CRM (`_send_to_contact`,
  `_place_call`). Internal notes do not count. An agent asleep on a conversation stays asleep
  until an ADMIN wakes it (`POST /api/ai/agents/{id}/wake`); a new text does not wake it.
  Approving a suggestion is a person's decision and is not blocked by that sleep (it is by
  the message cap).
* Max messages counts texts the agent actually sent on that customer's conversation; once
  reached, runs of an agent allowed to text are skipped with the reason.
* "Inbound text unanswered": one queued follow-up per agent per customer; the run reads the
  latest three texts. It waits the longer of the trigger's minutes and the agent's wait time,
  and stands down if anyone from the company texted, emailed or called since.
* Stage-entered fires wherever rule 4 fires (drag, modal, bulk move); a structural move
  (deleting a stage) fires nothing, as for rule 4.
* An agent in Suggest mode suggests an escalation too — the owner's rule is "EVERY write".
* A DISPATCHER may switch an agent OFF (the emergency stop), never on.
* Cost: Anthropic cache writes at 1.25× and reads at 0.1× the input price; OpenAI cached
  input at 0.5×. OpenAI prices in `pricing.KNOWN_PRICES` were the list prices known when this
  was written — **the owner should check them**; a blank price means unknown cost ("—"), never
  $0.
* Manual runs are queued for the worker (not run inside the request) and ignore the wait time.
* Deleting a knowledge base is refused while an agent's draft or published version attaches
  it; deleting a connection is refused while a draft uses it; deleting a folder moves its
  agents out.
* Escalation users must be active ADMINs or unrestricted DISPATCHERs.

### Screens — OURS; no GoHighLevel AI Agents screenshot exists

Built in the Opportunities module's rebuilt style (PageTabs, the Pipelines list table and ⋮
menu of refs/opps/02–03, the Create pipeline modal of refs/opps/04, the modal's left nav of
refs/opps/22). No emoji; outline icons. Every layout assumption:

* **Agents tab:** header + subtitle + "+ Create agent" (ADMIN); folders as pill filters in the
  table toolbar ("All agents (n)", each folder, "+ New folder", Rename/Delete for the selected
  folder); columns Agent name (description and folder beneath) · Channel · Mode chip ·
  Published (vN or Draft, "Unpublished changes") · Triggers · Last run (time + outcome chip) ·
  Actions ⋮ (Open, Duplicate, Move to folder, Save as template, Delete for ADMIN; a dispatcher
  gets Open and, while the agent is on, Switch off). No pagination.
* **Create agent:** a modal in the Create pipeline style — name, description, folder, "Start
  from template" (only when templates exist).
* **Builder:** full page under the tab bar. Top bar: back, name, published chip, unsaved /
  unpublished hint, an Off / Suggest / Auto-pilot segmented control (Auto-pilot asks in a
  confirmation dialog), Save draft, Publish (its refusal sentences shown). A 210 px left nav
  (Basics, Persona & goals, Rules, Knowledge, Actions, Triggers, Schedule & behaviour, AI
  connection, Escalation, Advanced, Compiled prompt, Versions), a white form card, and a 380 px
  Try-it panel. Goals and rules are numbered rows with ↑/↓ and trash; actions and knowledge
  bases are checkbox rows with descriptions; triggers are bordered cards with an "Add trigger"
  select; the schedule is a switch, day toggles and from/until times; the compiled prompt is
  monospace in a grey box. A dispatcher sees values as text, with no Save, Publish or Try-it.
* **Try-it:** "Test as" is a contact search then an opportunity select; customer bubbles blue,
  agent bubbles white; "Would" cards amber with the sparkle icon; a tokens · cost · latency line.
* **Knowledge Base:** a table of knowledge bases; a knowledge base opens a page with FAQs ·
  Articles · Files sub-tabs (counts), each a table; FAQs and articles edited in a modal, files
  through "Upload file"; a Test search card under the table. For an ADMIN, "Knowledge gaps" is
  a sub-tab beside "Knowledge bases" with open / resolved / dismissed pills; answering a gap
  opens a modal.
* **Templates:** a table with "Create agent from template" and Delete; the empty state points
  to "Save as template".
* **Agent Logs:** Logs / Metrics sub-tabs. Logs: agent, outcome, from/to dates, include-tests
  filters; a table; pagination past 50 runs; a run opens an 860 px modal with 8 stat tiles, the
  reason, the transcript (system prompt collapsed, tool calls and results as monospace blocks,
  actions as coloured rows with a status chip) and its suggestions. Metrics: 4 KPI cards and 6
  cards of horizontal bars plus a top-actions table, 7 / 30 / 90-day select.
* **Settings → AI Connections:** header with "Add connection"; a card with the Pause switch
  (confirmed) and the on-call phone; the connections table (name, provider, default model,
  "•••• last4", prices, agents using it, edit / delete). Add/Edit modal: the Test sentence in
  the footer left of Test / Cancel / Save; "Load models" fills a datalist; a blank price is
  described as "Unknown — no cost shown"; an amber banner when `AI_SECRETS_KEY` is missing.
  Settings keeps its tab row (as My Staff did) rather than GoHighLevel's settings sidebar.
* **Alert bell:** fixed top 9 / right 52, left of the status dot; red unread badge capped 9+;
  a 340 px dropdown; urgent items with a red left border; "Mark all as read"; a click opens
  the opportunity, else the contact. The Dashboard header's right margin went 44 → 80 so the
  bell does not cover its ⋮ menu.
* **Suggestions and "Run AI agent":** in the contact panel's Actions tab (already OUR design),
  between the delete block and the footnote, drawn only when an agent can run or something is
  pending; in the opportunity modal as the last nav item, "AI agent", after Photos. Agents
  offered are those on, published, with Manual in the PUBLISHED version.
* **"AI: <agent>"** is a small chip on a thread event an agent wrote; a task an agent made
  shows "Created by: AI: …" and an "Urgent" chip; a note reads "AI: …" as its author.

### Extension points left for phases 2–4

* **Phase 2 (text follow-up in Suggest):** configuration — an agent with the inbound-text or
  missed-call trigger in Suggest mode already runs end to end. The suggestion inbox UX is the
  addition (the API — list, approve, dismiss — exists; the UI is a minimal list).
* **Phase 3 (voice Receptionist):** `channel = voice` exists in the schema and config;
  `transfer_call` / `end_call` are catalogued (`VOICE`); a version's `config` is the dict to
  push to owen-main's `agent_versions`; runs have `trigger`/`trigger_ref` for a call id. To
  add: allow creating Voice agents, the push, and a trigger fed by owen-main.
* **Phase 4 (outbound):** a new trigger type in `config.TRIGGERS` and a hook; the actions,
  guards, logging and cost already apply.
* **Embeddings:** implement `knowledge.Retriever` and point `RETRIEVER` at it (its own table).
* **Another provider:** one `Provider` subclass in `providers.py` and one entry in `build()`.

### Not built

Voice agents and the owen-main push (phase 3); a polished suggestion inbox (phase 2); moving
the Try-it "Test as" onto the shared ContactPicker; editing a knowledge-base FILE's text (upload
it again); keeping uploaded file bytes (no file store); streaming replies in Try-it; spending
caps (the owner said none); Anthropic server-side refusal fallbacks (not requested); a `ghl`
CLI for agents. **No real provider has been called**: there are no keys on this server, so a
live Anthropic / OpenAI request, real prompt-cache hits and real refusal behaviour are
unverified.

### Migration `d7c3a9e5f214`, on `b5d1e8f3a276` — ONE revision

Fifteen CREATE TABLEs (`ai_settings`, `ai_connections`, `ai_folders`, `ai_agents`,
`ai_agent_versions`, `ai_knowledge_bases`, `ai_kb_items`, `ai_kb_chunks`, `ai_knowledge_gaps`,
`ai_runs`, `ai_run_steps`, `ai_suggestions`, `ai_agent_threads`, `ai_templates`, `ai_alerts`)
with plain `op.create_index`es, and six ADD COLUMNs: `ai_agent_id INTEGER NULL` on
`conversation_events`, `number_thread_events`, `opportunity_notes`, `opportunity_tasks`,
`appointments`, and `opportunity_tasks.priority VARCHAR(20) NOT NULL DEFAULT 'normal'`. No
ALTER of an existing column, no DROP, no UPDATE, no backfill, no batch mode in `upgrade()`.
Runs, suggestions and alerts refer to contacts, deals, conversations and appointments by
plain integer, so no delete path gains a foreign key to trip over. `tests/test_ai_migration.py`
stands a SQLite up at `b5d1e8f3a276` with production-shaped rows in every touched table,
upgrades, and asserts columns and rows unchanged, the new columns NULL / 'normal', exactly
the fifteen tables new and empty, a clean round trip, and an `upgrade()` that only creates
tables, creates indexes and adds columns.

### What the operator must do on production, in order

1. Generate the key ONCE and keep it (changing it makes every saved API key unreadable):
   `uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
   Put `AI_SECRETS_KEY=<key>` in `.env.prod` (the API and the worker both read it).
2. Rehearse on a copy, then `./deploy.sh --with-migrations` (revision `d7c3a9e5f214`; new
   Python dependencies `anthropic`, `openai`, `cryptography`, `pypdf`, `python-docx` are in
   `uv.lock`, so the image rebuild installs them).
3. Nothing runs yet: no connection exists and every agent is Off. In Settings → AI Connections
   add a connection and press Test; set the on-call phone if emergencies should text it.
4. Build an agent, Try it, Publish, and only then switch it to Suggest. Auto-pilot last.

## AMENDMENT (2026-09-15): texting goes live — manual texts only, and "New message" to any number

The 10DLC campaign is approved and the operator is switching SMS on in owen-main. From then on
`CrmLinkTransport` → owen-main `POST /api/crm-link/messages` → BulkVS sends REAL texts. Branch
`feature/sms-live`. **No migration** (head stays `d7c3a9e5f214`).

### The owner's decisions (2026-09-15), as built

1. **No automatic texts at all — only texts a person sends.** Rules 3 (appointment booked →
   reminders at T-24h / T-1h) and 4 (stage change → text the customer) are switched OFF exactly
   the way rule 1 was on 2026-09-13: a module flag (`APPOINTMENT_REMINDERS_ENABLED`,
   `STAGE_CHANGE_TEXT_ENABLED`, both `False`), the `on_*` hook answers with the sentence and
   enqueues nothing, and the worker handler refuses a job of that type already in a queue — it
   sends nothing and logs why (a reminder queued in production before deploy is drained as
   `done`, having sent nothing). The rules' code is kept readable behind the flags.
   `on_opportunity_stage_changed` still asks the AI agents' "stage entered" trigger BEFORE the
   flag: agents are Off / Suggest / Auto-pilot on their own and are not automations.
   `_drop_pending_reminders` still retires a booking's queued reminder jobs on a reschedule or
   cancel, so any left from before deploy are released too.
2. **"New message" to any number** — a compose icon left of "Call a number" in the Team inbox
   header, opening `NewMessageDialog.tsx` in the dialer's modal family. `POST /api/messages/new`
   `{number, body}`, ANY_USER like the dialer: the number is validated by `text_problem`
   (the dialer's rules in words about texting; the browser's `textProblem` says the same words,
   executed against the server in a test). A number a contact holds (last ten digits, lowest id)
   is exactly `POST /api/contacts/{id}/messages` — DND suppression included; anyone else's goes on
   the NUMBER-ONLY thread, found or created, through `send_outbound_to_number`. **No contact is
   ever created.** A bad number or a restricted user's refusal is a 200 with `recorded: false`
   and a sentence, and writes nothing — not even an empty thread. After Send the page refetches
   the inbox, widens it (Team inbox, no search, All) and opens that thread.
3. **The composer.** Sending works on both kinds of thread. Under a bubble: QUEUED → SENT →
   DELIVERED (a receipt on `POST /api/events/delivery` advances it on the next poll); REFUSED shows
   owen-main's own answer as a sentence (opted out / STOP, blocked, switched off, not allowlisted)
   and offers nothing; FAILED ("could not reach the phone system", or a carrier failure) says it
   did not arrive and **can be retried**, with a Retry that sends the same words again as a NEW
   message (the failed one stays — it is what happened). The copy that said texting was "waiting
   on carrier (10DLC) approval" (`crmlink._HUMAN`, the AI escalation help, Settings → AI
   Connections) now reads owen-main's actual answer: `CRM_LINK_SMS_ENABLED=false` is "texting from
   the CRM is switched off in the phone system (owen-main)"; the DID's own gate is "the phone
   system says this number is not enabled for outbound texts".
4. **Inbound from an unknown number** lands on its number-only thread with no contact and a fresh
   unread count (unchanged since 2026-09-13, now pinned for SMS). **What an MMS carries, read from
   owen-main's `integrations/crm/events.py`: the words plus `"[N attachments — view in OWEN]"` and
   NO media URLs**, and the CRM's event row has no media column. The images therefore cannot be
   shown; the note is rendered as an attachment line ("2 attachments — view in OWEN. The phone
   system does not pass pictures on to the CRM."). Showing them needs owen-main to relay media
   (as it relays call recordings) and one ADD COLUMN here.
5. **Quo (OpenPhone) is never a sender.** No route takes a `from_number` (an extra field is
   ignored); every outbound row is stamped `BulkVS` and the DID. The dialog's "Sending from" is
   read-only text naming (954) 482-9099.
6. **"Only assigned data"**: New message follows the dialer — a restricted user may text only a
   contact on their own jobs; any other number (someone else's customer or nobody's) gets the same
   sentence and nothing is sent or written.

### Every path that could send an SMS without a person pressing Send, and its state

| Path | State |
|---|---|
| Rule 1 — missed call → text back (`POST /api/events` inbound CALL) | OFF since 2026-09-13: hook + worker refuse |
| Rule 2 — new lead → notify team | ON, internal: a log line, texts nobody |
| Rule 3 — appointment reminders (staff booking, the Book appointment modal, AI `book_appointment`, reschedule / revive) | **OFF**: hook + worker refuse |
| Rule 4 — stage change (drag, modal, bulk move, AI `move_stage`, an approved suggestion) | **OFF**: hook + worker refuse; AI trigger still asked |
| Deleting a stage / pipeline (moves deals) | never fired a rule |
| AI agent `send_text` | per agent: Off (default) sends nothing; Suggest needs a person's Approve; Auto-pilot (ADMIN, confirmed) sends through `send_outbound`. "Pause all AI agents" stops every agent |
| AI escalation → on-call phone (a STAFF number, not a customer) | only in Auto-pilot or on an approved suggestion, and only if an on-call phone is set |
| Workiz import | cannot: imports neither `automations` nor `queue`; one new `jobs` row rolls the import back |
| AHS work-order emails (`POST /api/ahs-jobs`) | cannot: nothing enqueued, no transport import |
| OpenPhone / Quo mirror (inbound `POST /api/events`) | writes rows only; rule 1 off; an AI inbound trigger follows the agent's mode |
| Bulk actions | no bulk text exists; bulk stage move fires rule 4, which is off |
| `ghl` CLI | `msg send` is a person with `--yes`; `jobs` only lists |
| CompanyCam, checklist seed, convert_auto_contacts, bootstrap | no send path |

### Screens — OURS, not measured (GoHighLevel's compose screen was never captured)

- New message dialog, 420 px, the Create pipeline modal's look: "New message" 16px/600 + close
  cross; "To *" 40 px number field; the hint line (grey, amber once typed, red for a server
  refusal); "Message *" 5-row textarea; "47 characters · 1 segment" left, "47/1600" right;
  a bordered "Sending from (954) 482-9099" row; Cancel + blue Send with the chat icon. Ctrl/Cmd+Enter
  sends from the message box; Enter in the number field moves to the message.
- The compose icon is an outline pencil-on-square (`IconCompose`), 20 px, left of the dialer's
  phone icon — GoHighLevel conventionally puts compose in this row.
- Settings → **Automations**, for everyone, read-only: one card per rule with On / Off and the
  reason; no switches (a switch here would either do nothing or undo the owner's decision). The
  sidebar's dimmed "Automation" row is unchanged.
- A composer note is pinned to the thread it is about and is not shown over another thread.

### Judgement calls, all overrulable

- North American numbers only, as the dialer (the DID is a US 10DLC line); "10+ digits" is read as
  10, or 11 starting with 1.
- The 1600-character cap (about ten segments) is ours; owen-main sets none.
- A text to a contact on DND from New message is suppressed with the reason, like the composer,
  rather than offered.
- Segment arithmetic: GSM-7 160/153 with extension characters counted twice; any other character
  makes the whole text UCS-2, 70/67 in UTF-16 units.

### Tests

`tests/test_sms_live.py` (behaviour, owen-main mocked at the HTTP boundary), additions to
`test_only_assigned_data.py`, `tests/test_new_message_ui.py` (node-executed libraries and source
wiring), `tests/test_owen_network_guard.py` + `tests/owen_guard.py` (package-wide: no test reaches
owen-main), and `uv run python -m tests.browser_sms` (headless Chromium, throwaway SQLite, owen-main
replaced by a recorder, 38 checks). Existing tests that pin rule 3 / rule 4 mechanics (the reminder
dedupe trap, reschedules, cancel-withdraws, one text per real move from each path) now ask for the
rule by name (`rule_3_armed` / `rule_4_armed` fixtures); two AI engine assertions and three
`test_crm_link.py` expectations (E.164 `to_number`, the new refusal wording) were changed for the
decisions above. Every text is now handed to the transport as E.164 (`automations.sms_number`), so
owen-main's opt-out and block checks match a contact saved as "(941) 555-0101".

### What the operator must verify live, after owen-main's switch

1. `GET /api/health` → `"crm_link": true`; Settings → Automations shows three rules Off.
2. **First send**: New message to a staff mobile. The bubble reads "queued"; owen-main's
   `messages` row exists with the CRM marker; the phone receives it from (954) 482-9099.
3. **Delivery receipt**: within a poll the bubble moves to "sent" then "delivered". If it stays
   "queued", owen-main's `/api/crm-link/delivery-receipts` relay is not reaching
   `POST /api/events/delivery` (check its `crm_report` jobs).
4. **Inbound reply**: reply from that mobile. It appears on the same thread, unread; from a number
   no contact holds it is a number-only thread and no contact is created. Send a picture: the
   attachment line appears.
5. **STOP**: reply STOP, then send again. The bubble reads "not sent — This number has opted out of
   texts (they replied STOP)". Reply START (owen-main's opt-in) before further tests.
6. A restricted technician cannot text a number outside their jobs; and a reminder job left in
   the production queue from before deploy drains as done with no outbound row.

## AMENDMENT (2026-09-15): every Workiz job is on the calendar, past or future, and the week view lays overlapping visits side by side

The owner's decisions after comparing his Workiz week (Sep 13–19) with ours: production had 15
scheduled jobs that week and the CRM drew 9, one of them on the wrong day, two titled
"Inspection", and two 3–5 PM visits on Tuesday drawn one on top of the other. No migration (head
stays `d7c3a9e5f214`), no new route.

### What this AMENDS, explicitly

* **"Only FUTURE jobs become appointments"** (The Workiz import, 2026-09-11) — REVERSED. Every
  non-cancelled job with a `Scheduled` time gets exactly one appointment on "Workiz Jobs
  (imported)", past or future. The reason that rule existed ("back-dating a booking is the shape
  that would have produced the texts") was about going through `POST /api/appointments`. The
  importer never does: it writes rows through the ORM, it cannot import `app.automations` or
  `app.queue`, and `run` rolls everything back if the `jobs` table gains a row. Nothing in `app/`
  scans the appointments table to send anything (reminders exist only as `jobs` rows made by
  `on_appointment_booked`; the AI context reads upcoming appointments but never triggers on a
  row). The guard test now covers a past visit and a re-import that moves and cancels.
* **"Past visits are never touched"** (the Workiz `Tech` column, 2026-09-15) — REVERSED, same day.
  The assignment rule (`tech_assignment`) applies to past visits too, so a technician with a login
  sees their history ("Only assigned data" counts an assigned visit, cancelled ones excepted).
  Dry-run wording: "Appointments, past and future (N)".
* **The grid hours** (2026-09-13: "opens scrolled to 5 AM") — the grid opens at **7 AM**, where
  Workiz starts, and each hour is as tall as it can be while 7 AM–7 PM fits without scrolling
  (`hourPxFor`: pane height / 12, never below 48 px). All 24 hours are still drawn.
* **Day and Week view were "measured and unchanged"** — the block's CONTENTS and the overlap layout
  changed (below); the grid, header, toolbar, colours and Manage view panel did not.

### The importer: one visit per scheduled job, and what it may write to

* **Create** — past or future, status `confirmed`, the card's title (the customer's name; an AHS
  email card keeps its own name-first title, and the visit carries it). A job whose End is on a
  later date keeps its real end; no End, or End not after Scheduled, is 2 hours (unchanged).
* **Move** — Workiz's start or end changed, past or future: the same row, new times.
* **Retitle** — the visit's title differs from the card's (the old "Inspection" titles).
* **Cancel** — the job is now Canceled/Cancelled in Workiz, or has no Scheduled time: status
  `cancelled`, never a delete; the assignee stays so the history says who it was. **Restore** — the
  importer's own cancellation, and Workiz schedules the job again: back to `confirmed`.
* **Whose visit it is.** `custom_fields.workiz_appointment` on the CARD records what the importer
  last wrote to its visit: `{id, starts_at, ends_at, status, title}` (on the card for the same
  reason as `workiz_tech_assigned_user_id`: an appointment has no JSON column). The importer may
  write to that row only while its time, status and title still equal the record. Anything else —
  a person moved it, cancelled it, renamed it, or moved it to another calendar or card — is
  **`manual`: left alone entirely (its assignee too) and listed with what changed**. The importer
  never books a second visit beside a manual one. Who ASSIGNED it is not part of this test: that
  has its own record and rule, and a dispatcher assigning a tech is not a reason to stop following
  Workiz's times. Read-only through the API like every `workiz_*` key.
* **Visits made before the record existed** (production today). With no record, the card's first
  visit on the Workiz calendar is ADOPTED if it looks like the old importer's output: status
  `confirmed`, and no notes, description, location or AI agent (the old importer wrote none of
  those; the Book appointment modal always resolves a location). Adopted visits are listed in the
  dry run. Anything else on that calendar with no record is `manual`. **Judgement call:** a person
  who moved an old imported visit before this change, without touching anything else, cannot be
  told apart and will be moved back to Workiz's time on the first run — the dry run lists every
  move by Job # so the owner can check them first.
* **Jobs the export no longer mentions** are still left exactly as they are (2026-09-14); only a
  job that IS in the export, cancelled or unscheduled, cancels its visit.

### The dry run's "Appointments" section (Job #s only, no customer data)

```
Appointments (one per scheduled job, past or future, on 'Workiz Jobs (imported)')
  2      to create — 1 in the past, 1 in the future
        past jobs OLD001
        future jobs NEW001
  1      to move: Workiz's scheduled time or end changed
        jobs J2P6JO
  0      to restore: the importer had cancelled it and Workiz schedules it again
  1      to retitle to the customer's name, as on the card
        jobs 9CNTDD
  2      to cancel (status, never deleted): cancelled or unscheduled in Workiz
        job ELK2KF     no Scheduled time in Workiz
        job WFNSXZ     cancelled in Workiz
  1      changed by a person since the import wrote it — LEFT ALONE
        job N61PDL     a person changed its time
  7      unchanged
  1      of those made by an earlier import that kept no record — adopted, and recorded from this run on
        jobs 9CNTDD
  reminders scheduled: 0, always. ...
```

`--json` carries the same under `appointments` (`create_past`, `create_future`, `move`, `restore`,
`retitle`, `cancel`, `manual`, `unchanged`, `adopted_from_an_earlier_import`). Written counts:
`appointments_created_past|created_future|moved|restored|retitled|unchanged|cancelled|left_alone`.

### The week and day view

* **Side by side** (`calendarGrid.layoutDay`, executed under node in `test_calendar_layout.py`).
  Items that overlap, directly or through a chain, form a group; each takes the first lane free
  when it starts (earliest first, longest first on a tie); the group's width is split into as
  many lanes as it needed; an item then widens into lanes to its right that nothing overlapping it
  uses. Touching (12–1 and 1–3) is not overlapping. No two items that overlap in time share
  horizontal space. Blocked off time is laid out in the same pass, so neither hides the other.
  Workiz staggers its overlaps slightly; GoHighLevel splits the column; we split.
* **Height = real duration.** No minimum height any more (it was 24 px, which could push a short
  visit over the next one); a zero-length item is drawn 15 minutes tall.
* **Past midnight / multi-day**: drawn on every day it touches — start to midnight, whole days in
  between, midnight to end — with the squared-off edge where it continues. A carried-over segment
  puts its words at 7 AM rather than at midnight, where nobody would see them. Its range names
  both days: "Thu 7:30 AM – Fri 10:00 AM". Month view lists it on each day ("until 10:00 AM" on
  the later ones). Ending exactly at midnight stays on its own day.
* **The block** (our design, the GHL look): the title, the time RANGE ("7:30 – 10:00 AM",
  "11:30 AM – 1:00 PM"), then, as height allows, "Job #03T4HE" and the CARD's street and city (a
  visit with no card shows its meeting location instead). A block one line tall reads "Title,
  range". Every line truncates with an ellipsis; hovering shows everything plus the calendar; a
  click opens the existing appointment detail panel. `GET /api/appointments` gained
  `opportunity_address` and `workiz_job_id`, blanked together with `opportunity_title` for a deal
  the reader may not see.
* **Month view** shows three chips and "+N more", which now opens a card listing every booking of
  that day (GoHighLevel's behaviour), each opening its detail panel; Escape or a click outside
  closes it.

### Screenshot assumptions (refs/round7/57 and 58 do not show these)

Neither screenshot shows a day or month view, a multi-day block's second day, a block too short
for two lines, the "+N more" card, or the hover. All of those are built to GoHighLevel's
convention in our existing look and are listed here. Workiz's tags (Callback, Tile Roof, AHS –
Repair Scheduled) are not drawn: the CRM does not import Workiz tags. Very narrow lanes (three
overlapping visits in a week column with Manage view open are ~37 px) truncate to a letter or two
by design; closing Manage view or Day view gives them room.

### Not built

* **The grid is still in the browser's timezone** (the 2026-09-13 known gap). Staff are in
  Florida, so it matches; the headless check runs in America/New_York.
* No drag to move or resize (unchanged decision).

### What the operator must do on production

Nothing to migrate. After deploying, with `DATABASE_URL` on production's database and the
latest Workiz export, exactly as every earlier import was run:

    cd backend
    uv run python -m app.workiz_import --clients <workiz_clients.csv> --jobs <workiz_jobs.csv> [--tech-map techs.json]
    # read the Appointments section: "to move" (an old visit a person moved by hand cannot be
    # told apart — see above), "to cancel" and "LEFT ALONE"; then the same line with --commit

(Without `--clients` / `--jobs` it reads `~/workiz/workiz_clients.csv` and
`~/workiz/workiz_jobs.csv`.) Run it again after every new export: a second run with the same
files changes nothing.

---

## Amendment — pictures in text messages, received and sent (2026-09-16)

The owner: a customer texts a photo of the roof, and the operator sees the photo. Both
directions. Nothing public, nothing automatic, thread only.

### What was true the morning this started

owen-main had stored inbound MMS media since Ticket 09 — `messages.num_media`,
`messages.media_urls` — and the CRM link deliberately did not pass it on:
`integrations/crm/events.message_body` appended `"[2 attachments — view in OWEN]"` because
this CRM had nowhere to put a picture. The thread rendered that count as an attachment line
(`lib/mmsNote.ts`, 2026-09-15) and said so plainly. That is what changed.

### 1. The CRM keeps its own copy. The bytes are not in Postgres

**LOCKED.** A carrier MMS media link EXPIRES — days, sometimes hours. So a picture is
fetched ONCE, when the text arrives, and kept. That is the opposite of the choice made for
a mirrored OpenPhone call recording (2026-09-11), which is streamed from owen-main every
time it is played and stored nowhere, and the difference is the source: OpenPhone holds a
recording as long as the account exists, a carrier does not.

The bytes go on disk under `MEDIA_ROOT`, content-addressed as `<sha[:2]>/<sha>`
(`app/attachments.py`), with one `message_attachments` row per picture saying where.
Measured against how this database is actually used: a `pg_dump` the operator takes to look
at 800 contacts must not carry a gigabyte of JPEG, and every byte in a `bytea` column is
written again to the WAL and again into every base backup. Content addressing also makes
"stored once" literal — the same picture twice is one file, and `forget()` counts the rows
holding a sha before it unlinks.

`MEDIA_ROOT` defaults to `/var/lib/ghl-clone/media` **in `Dockerfile.api`**, which is
exactly where `docker-compose.prod.yml` mounts the named `media` volume. Set in the image
rather than in `.env.prod` so the path and the mount come from the same repository and
cannot drift. `GET /api/health` reports `"media_writable": true|false` — the one thing a
deploy can silently get wrong, whose symptom (every photograph reading "Picture
unavailable" a week later) looks like a carrier problem.

### 2. Inbound: the text never waits for the picture

`POST /api/events` gained `num_media`. owen-main now sends it (`to_crm_message_event`) and
**still appends its note**, deliberately: a CRM deploy that predates this must not render a
blank bubble. `lib/mmsNote.ts` strips the note when it has the pictures and prints the count
when it does not.

The CRM writes N PENDING rows in the same transaction as the event and queues one
`fetch_message_media` job per picture. The FETCH IS A JOB, not part of the ingest request:
inline, owen-main's `crm_report` job would wait on three carrier round-trips before its
201, and on a slow carrier that is a timeout, a retry and a second delivery of a text that
already landed. So the words land immediately and the pictures arrive seconds later.

**A failed fetch never loses the text.** The row goes FAILED with a sentence, the bubble
shows "Picture unavailable" and a Retry, and the words are untouched. Too big or not an
image is REFUSED instead — no Retry, because trying again cannot make a PDF a photograph.
Re-delivery stores nothing twice: the event's `dedupe_key` returns the existing row, and a
unique index on `(parent event, position)` catches anything that gets past it.

### 3. The type limit is decided by the BYTES

`attachments.sniff()` reads the magic number: JPEG, PNG, GIF, WebP, HEIC, HEIF and nothing
else. A `Content-Type: image/png` on a zip is a claim by whoever sent it. What is SERVED is
what was sniffed, with `X-Content-Type-Options: nosniff` so the browser does not form its
own opinion either. owen-main sniffs independently, on its own side, because "the other
system checked" is not a check. Caps: 5 MB per picture, 10 per inbound message, 5 per
outbound one — a backstop against a runaway disk, well above what any carrier will carry.

### 4. Nothing public on the CRM side

`GET /api/attachments/{id}` is the ONLY way a picture reaches a browser, behind the same
app-level `require_auth` as every other `/api` route, so an `<img src="/api/attachments/12">`
carries the session cookie because the path is same-origin. `message_media.visible` asks the
THREAD's question — `assigned_access` for a contact thread, a flat refusal for a
number-only thread when "Only assigned data" is on — and answers **404, never 403**, so an
id cannot be walked. A draft belongs to the operator who uploaded it until it is sent.

### 5. Outbound: owen-main publishes, the CRM never does — and the exposure that creates

BulkVS sends an MMS by FETCHING the media itself, over the public internet, from a URL in
`/messageSend`. Somebody has to serve a customer's photograph to a carrier that cannot
authenticate. **It is owen-main**, which already has a public hostname (`api.<APP_DOMAIN>`,
where the BulkVS webhooks already land) and already holds the BulkVS credential. The CRM
uploads the bytes to `POST /api/crm-link/media` and gets back an OPAQUE id; owen-main mints
the URL at send time and hands it to BulkVS. **Nothing in the CRM repository ever holds a
URL a carrier can fetch**, which is what keeps the exposure one carrier fetch wide rather
than one CRM deploy wide.

**The exposure, stated plainly: for as long as that URL lives, anyone holding it can fetch
that one picture with no credential.** That is not a flaw to engineer away — it is what MMS
means, and it is equally true of every Twilio, SignalWire and BulkVS media URL in existence.
What is controlled is how wide and how long:

* **unguessable** — a 192-bit `secrets.token_urlsafe(24)` id plus an HMAC-SHA256 over
  `id|expiry`, keyed on `CRM_LINK_MEDIA_SECRET` (falling back to `CRM_LINK_TOKEN`), which
  the CRM never receives. A tampered expiry is refused because the expiry is signed;
* **short-lived** — `CRM_LINK_MEDIA_TTL_SECONDS`, 30 minutes. The carrier fetches within
  seconds; the rest is slack for a retry;
* **one object** — the route serves the one file that id names. No listing, no directory,
  no way to walk from one picture to another;
* **GET only, no cookies, no CORS**, `Cache-Control: private, no-store`, `X-Robots-Tag:
  noindex`;
* **swept** — the worker deletes expired files every ten minutes, so an expired URL has
  nothing behind it as well as an invalid signature;
* **every refusal is the same 404** — bad signature, expired, never existed, swept, feature
  off — so the route cannot be used to learn which.

Rejected: a permanent public URL (the exposure never ends); a public URL on the CRM (a
second public surface serving customer content, and Traefik routes no such path to it);
embedding the bytes in the BulkVS call (their API does not take them). **This is the
minimum that sends a picture at all.** It is OFF unless
`CRM_LINK_MEDIA_PUBLIC_BASE_URL` is set: unset, `POST /api/crm-link/media` answers 409 with
a sentence and nothing is ever published.

**Inbound is deliberately not symmetric.** An inbound picture is never published: the CRM
asks the key-gated `GET /api/crm-link/messages/{id}/media/{i}`, owen-main dereferences the
carrier URL with the carrier's own credential, and the bytes come back over the internal
network — the same three-hop shape as the OpenPhone recording stream, one floor down.

### 6. A refused send keeps the pictures, and a picture that cannot be published stops the send

A send owen-main declines is recorded on the thread WITH its pictures — that is what was
attempted, and the rule that a refusal is recorded predates this. A send suppressed before
anything was attempted (DND, no phone, a restricted technician) writes nothing and **leaves
the drafts attached**, so the operator fixes the number and presses Send again without
picking the photographs a second time. And if the picture cannot be handed to the phone
system at all, **nothing is sent**: a text that arrives without the photograph — "here it
is", with nothing attached — is worse than one that does not arrive.

An abandoned draft — a refused send the operator walked away from, or a closed tab — is
swept after 24 hours, **on that operator's next upload** rather than by a scheduled job:
the only way drafts accumulate at all is by uploading more of them, so the sweep runs
exactly when it can have something to do, and it is scoped to one user so it can never
take a picture out of somebody else's open composer.

### 7. Nothing sends a picture by itself

The automations stay off (2026-09-15) and no AI agent can attach media. Kept by there being
no automatic caller that HAS a draft: `pictures=` is passed only by the API's send routes,
which require an id uploaded by a signed-in person through the composer.
`test_message_pictures.py` asserts that property by reading the source, because that is
where it lives.

### 8. The delivery bubble that said "queued" for ever — fixed on the owen-main side

The first real send, the morning texting went live, succeeded (`messages.status='sent'`)
and `bulkvs_client.send_message` logged `ref=None`. `/webhooks/bulkvs/message-status`
matches a DLR on `bulkvs-<RefId>`, so with no RefId nothing correlated and the CRM's bubble
sat on QUEUED. Two changes, both on owen-main:

* **`_extract_ref_id` now walks** dicts and lists to a bounded depth. The old version read
  only the top level of a dict, and the BulkVS documentation shows `/messageSend` answering
  with a per-recipient `Results` list — an id nested one level down was invisible to it.
* **The whole decoded response body is kept** on `messages.raw_payload` under
  `bulkvs_send_response`, beside (never over) the CRM-link marker. The first send threw the
  evidence away with a `ref=%s` log line; the next real send writes the answer down where a
  person can read it.

**What BulkVS actually returns from `/messageSend` on this account is still not known, and
this does not claim to settle it.** Production was not touched and no new text was sent to
find out. So the correlation no longer depends on it:
**`handle_message_send` reports `sent` to the CRM itself**, from owen-main's own knowledge
that the carrier answered 2xx for that exact row, keyed on `messages.id` — which is the
CRM's `provider_ref` and is right there. The carrier's word, not an advanced status: a real
DLR, if one ever correlates, advances it to `delivered`, and both sides apply a forward-only
ladder so a late or repeated receipt is harmless. This is the one-line fix
`.qa/state/relay-done` §5 predicted would be needed, made necessary by exactly the
circumstance it predicted.

### Six fences fired, and what each one caught

Worth recording, because five were widened and one was a real bug — and the difference is
the point of having them.

* **`test_only_assigned_data` / `test_crm_link` (the transport doubles) — A REAL BUG.**
  `send_outbound` passed `media_ids=` on EVERY send, so every stand-in for
  `MessageTransport` — whose shape has been `send_sms(to, body, from_number)` since the
  seam existed — broke for a feature it does not use. Fixed at the call site
  (`automations._send_sms`): the keyword goes only when there is a picture, so an ordinary
  text is byte-for-byte the call it always was. The same choice `CrmLinkTransport` already
  makes one layer down, and the reason the browser check's `crmlink.send_sms` double keeps
  working too.
* **`test_frontend_writes`, the fetch fence.** `upload` is a fourth transport helper and
  cannot go through `send`: that helper sets `Content-Type: application/json`, and a
  multipart body needs the boundary the browser generates. Widened, with a new test that it
  carries the CSRF token, the session cookie and the refresh retry exactly as `send` does —
  the fence's subject is the token, not the number of helpers.
* **`test_sms_live`, "every handler is a rule".** `fetch_message_media` is not a rule.
  Widened, with a new test that it cannot reach `send_sms`, `send_outbound`,
  `get_transport` or `send_email` at all.
* **Two `test_new_message_ui` sender fences.** Both matched brittle literals
  (`sendNewMessage(to, body)`, `{ number, body }`) that the attachment ids broke. Rewritten
  onto the property they exist for: the call and the body name NO SENDER.
* **`test_crm_link`, the composer's disabled condition.** Moved into `sendOff` because a
  picture with no words is now a message. Widened; blocked, sending and nothing-to-send are
  all still in it.

### What the operator must do, in order

1. Merge and deploy **owen-main** first — it has the routes the CRM will call:
   `ssh owen-main`, `cd /opt/santiagoproperties/owen-main`, `make check`, then the usual
   deploy. **No migration**; the `messages` columns it writes have existed since Ticket 09.
2. In owen-main's `.env.prod`, add **`CRM_LINK_MEDIA_PUBLIC_BASE_URL=https://api.<APP_DOMAIN>`**
   — the origin Traefik already routes to `callmon_app`. Optionally set
   `CRM_LINK_MEDIA_SECRET` to a fresh `openssl rand -base64 48`, so rotating the CRM's API
   key does not invalidate media URLs already handed to the carrier. Restart owen-main.
   **Leaving it unset is a supported state**: inbound pictures work, outbound ones are
   refused with a sentence.
3. Merge and deploy **the CRM**: `cd /opt/santiagoproperties/ghl-clone`,
   `./deploy.sh --with-migrations` — this revision (`e1b4d7c96a05`) is CREATE TABLE only.
4. `docker compose --env-file .env.prod -f docker-compose.prod.yml up -d` creates the named
   `media` volume by itself; nothing is provisioned by hand. **Do not set `MEDIA_ROOT` in
   `.env.prod`** — `Dockerfile.api` already points it at the mount.
5. Check `curl -s https://crm.dreamteamroofingfl.com/api/health` shows
   `"media_writable": true`. If it is false the volume did not mount, and every picture
   from then on reads "Picture unavailable".
6. Add the `media` volume to whatever backs this box up. It is the only customer data in
   this stack that is not in Postgres.
7. Send yourself one picture from a mobile, and text one back. That is the first real MMS
   either direction; nothing here has been exercised against BulkVS.

### Not built, and known gaps

* **No thumbnails are generated.** The full picture is served for the grid too, sized by
  CSS. A carrier-capped MMS is well under a megabyte, and generating thumbnails means
  Pillow — a compiled dependency on a host where that has cost us before. If the owner ever
  attaches a 5 MB photo, a grid of three costs 15 MB to render.
* **HEIC renders in Safari and not in Chrome.** The carrier passes it through unconverted
  and we store what arrived rather than throwing the customer's picture away. Converting it
  needs the same dependency as thumbnails.
* **No "save to job" and no CompanyCam attaching** — the owner's decision 3, this phase.
* **Nothing has touched BulkVS.** No MMS has been sent or received end to end; both
  directions are exercised against a double. The first real one is a test to a number the
  owner controls, not a rollout.
* **Whether a BulkVS inbound media URL needs the REST credential is not documented and has
  not been observed.** `media.fetch_carrier_media` tries WITHOUT one first (correct for a
  pre-signed link) and retries WITH the `/tnRecord` Basic auth on a 401/403, for a
  `bulkvs.com` host only. One of the two attempts is right whichever it turns out to be.

### UI assumptions (no screenshot shows any of this)

`refs/round3` and `refs/opps` contain no MMS bubble, no viewer and no composer with an
attachment. Everything below follows GoHighLevel's conventions as they already appear in
this rebuild, and is listed so nobody rediscovers it as a decision:

* thumbnails are 104px squares, 8px radius, 1px `rgb(234,236,240)` — the card border used
  across Opportunities — laid out in a wrapping row under the words;
* the viewer is a full-screen `rgba(16,24,40,0.85)` scrim (the scrim behind every modal
  here), the picture centred and never upscaled, with the sender, the time and "2 of 3" in
  a header, round chevron buttons either side, Escape and the arrow keys;
* the arrows stay in place and dim at the ends rather than disappearing, so the picture
  does not shift sideways under the cursor mid-browse;
* the composer's paperclip sits left of the message box, inside the same 40px tray, and the
  attached pictures are 56px squares above it with a remove cross; drop and paste are
  accepted anywhere on the composer row and on the New message dialog's message box;
* a picture with no words is a valid message — Send is live with either.

---

## Amendment — delivery receipts are receipts, not messages (2026-09-16)

**Measured on production, the morning after texting went live.** BulkVS posts carrier
delivery receipts to the SAME webhook it posts inbound texts to. owen-main stored each one
as an INBOUND message and relayed it here like any other, so this CRM filed them on
customers' threads as words the customers had written:

    id:1162999967 sub:001 dlvrd:000 submit date:2609160247 done date:2609160247
    stat:UNDELIV err:255 text:Dream Te...

One number-only thread held two of them. They are not messages — they are the answer to
"did the text arrive?", the exact question the owner asked for, and they were being shown
as the question instead.

### 1. Recognised by the BODY, and the match is deliberately strict

The SMPP `deliver_sm` shape: seven fields, in order, anchored at the start, `text:`
optional because it is a truncated echo a carrier may omit. **The property that matters is
that a real customer text is never mistaken for one** — everything downstream hides,
reclassifies or deletes on the strength of this match, and a person cannot type it by
accident. A receipt-shaped body the pattern cannot read is logged at ERROR and still
ingests, so an unrecognised carrier variant is LOUD rather than silently in a thread.

**Which field the raw payload flags it with is still not known.** Production could not be
read from here, so detection is on the body, which is definitive.
`providers/bulkvs._DLR_FLAG_KEYS` is a defensive second route over plausible key names, it
can never reclassify a customer's text on its own, and **every recognised receipt now keeps
its whole raw payload** on the outbound row (`raw_payload.bulkvs_dlr.raw`) — so the next
live one writes the answer down instead of it being guessed at again. The same device used
for the `/messageSend` response.

### 2. Correlation, exactly — and why it is not the id

**The DLR's `id` is not the send's `RefId`.** Measured: `RefId 4551F89F` against
`id:1162999967`. Hex against decimal, eight characters against ten — two identifier spaces,
not one number in two renderings. A future reader who "fixes" this by matching
`provider_message_sid` will silently receipt nothing.

So `services/dlr.correlate` matches on four facts the receipt carries and the outbound row
also holds:

1. **our DID** — the receipt's `To` is the number the text was sent FROM;
2. **the recipient** — the receipt's `From` is the number it was sent TO. Both are inverted
   relative to a normal inbound message, because a receipt is addressed to us about them.
   Compared on the LAST TEN DIGITS, this platform's identity rule everywhere;
3. **the text prefix** — `text:` is a truncated echo; the outbound body must START WITH it,
   trailing ellipsis removed, compared case-insensitively;
4. **the submit time** — `submit date` is `YYMMDDhhmm[ss]` **on the SMSC's own clock in a
   timezone the receipt does not state**, so it is never treated as authoritative: it only
   has to fall within ±2 days, which absorbs any plausible offset, and it breaks ties by
   nearness.

An empty `text:` drops fact 3. **Ambiguity is resolved, never guessed**: an unreceipted row
beats a receipted one, then nearest submit time, then most recent. The case this can get
wrong is the same text sent twice to the same person inside the window — recorded here
rather than hidden.

Idempotent: every applied receipt is remembered as `raw_payload.bulkvs_dlr_seen`
(`"<id>:<stat>"`), so a carrier re-POST changes nothing and the backfill can be re-run
freely. New keys are written BESIDE the CRM-link marker, never over it — that marker is the
only thing that says a message was the CRM's.

### 3. What the operator reads

`stat` becomes the platform's own vocabulary (`DELIVRD`→delivered, `ACCEPTD`→sent,
`UNDELIV`→undelivered, `REJECTD`/`EXPIRED`/`DELETED`/`UNKNOWN`→failed) so the existing
forward-only ladder applies unchanged on both sides. **A carrier word nobody here has seen
becomes `failed`, not `sent`** — "did it arrive" answered "probably" is never useful.

The sentence is written for a roofer, and the error code is carried **verbatim and not
interpreted** (BulkVS's codes are per-carrier and undocumented for this account):

    failed — not delivered
    The carrier rejected it (error 255). It did not arrive — you can retry it.

### 4. Two guards, because the systems deploy separately

owen-main recognises a receipt at the webhook and never stores or relays it. The CRM ALSO
refuses to file one on ingest (`app/dlr.py`, `POST /api/events`), because for however long
an older owen-main is running, every receipt it relays would otherwise land in a
conversation. Two copies of one regex, deliberately: a guard that needed the other side
deployed first would not be a guard. A test pins that they agree.

### 5. Existing junk — one command each side, dry run by default, counts only

* **owen-main** `python -m app.scripts.backfill_dlrs` (`--apply` writes). Parses each stored
  inbound row with the SAME parser the webhook uses, applies it to its outbound message,
  and **marks it hidden** (`services/dlr_junk`). **It deletes nothing.** The row is the only
  record that a carrier ever said anything, and hiding is reversible. `_msg_stmt()` — the
  one statement the Inbox list and the thread view are both built from — excludes marked
  rows, NULL-safely (a bare `~has_key` would have hidden every outbound row the CRM did not
  send, i.e. most of the operator's Inbox).
* **the CRM** `uv run python -m app.dlr_cleanup` (`--commit` writes). Removes the junk
  events from their threads and repairs what they broke while they were there: the unread
  badge (recomputed, and only ever DOWNWARD, so a thread somebody had read is not marked
  unread by a cleanup), `last_event_at` (it orders the inbox), and a number-only thread the
  receipts created that now holds nothing.

**Why the CRM deletes where owen-main hides.** Here the row carries no information at all —
this CRM receives receipts through `POST /api/events/delivery`, a different route entirely
— so a hidden row would mean a permanent read-time regex over every customer's words on
every thread query. That is a worse thing to own than a one-off, dry-run-by-default removal
of rows positively identified as machine output. Both are scoped to INBOUND SMS; a call, a
note and an outbound row are never touched.

A receipt that correlates to nothing is **kept, hidden and never relayed** on owen-main
rather than dropped: losing it would mean losing the only evidence of a failed text.

### Not settled

* whether BulkVS flags a receipt in the payload, and how (see §1);
* whether the `/messageSend` `RefId` ever appears in a receipt at all. It does not in the
  one measured, and nothing depends on it;
* the same-text-twice-in-the-window ambiguity (§2).

---

## Amendment — the CRM's line moved, and now lives in one place (2026-09-16)

The owner replaced the CRM's number. **`+19547758492`** ("CRM number" in owen-main) is the
bound DID; **`+19544829099` is FULLY UNBOUND** and is no longer the CRM's line. A carrier
delivery receipt confirms texts from the new line deliver.

### One definition

The line was hard-coded in FIVE places — `lib/dialPad.ts` (`CALLING_FROM`),
`ConversationsPage.tsx` (`BULKVS_LINE`), `AiConnectionsSettings.tsx`, `ai/AgentBuilder.tsx`
and `crmlink.DEFAULT_FROM_NUMBER` — so four screens went on telling customers to reply to a
number that no longer reaches anybody.

**`app/crmlink.py` is now the only definition**, overridden by `CRM_LINK_FROM_NUMBER`.
`GET /api/connection-status` reports it to the browser as `our_line`, and
`lib/useOurLine.ts` reads it from there — sharing the `['connection-status']` query the
status dot already polls on every signed-in page, so the dialer, the composer and the AI
settings pay nothing for asking.

That endpoint was chosen because it is already polled everywhere and is already
signed-in-only. Its "names nobody" property is **kept and narrowed to what it was for**: no
key, no URL and no customer's number survives into the body, and a test still asserts it.
Our own line is not a leak — it is the number on the company's vans, already printed on the
composer. It is read PER REQUEST rather than from the 30-second cache; the cache exists to
stop N tabs becoming N requests to owen-main, and a stale number on screen is the bug this
whole change is about.

`lib/ourLine.FALLBACK_LINE` and `dialPad.CALLING_FROM` are the only remaining literals, and
they are fallbacks for a render that has not polled yet — never what a send uses. The
SERVER picks the line.

### "It cannot text itself" follows the configuration

`dialProblem` / `textProblem` / `normaliseNumber` / `normaliseTextNumber` take the line as a
parameter. So the refusal moves when the owner moves the number — and, just as importantly,
**the retired number stops being refused**: `+19544829099` is somebody else's line now and
there is no reason this CRM may not text it. The server's own check already read
`crmlink.current().from_number` and needed nothing but the new default.

### History keeps its own number

**Nothing relabels an old event and nothing drops its label.** A thread row carrying
`source_number: +19544829099` was genuinely sent on that line and still says so: the source
chip reads the EVENT's number, never the configured one, and a test plants an old message
beside a new one and asserts both are labelled with the line each actually used. A number on
a thread is a fact about when something happened, not a setting.

The thread's Quo reply banner changed for the opposite reason: it used to name the newest
outbound event's `source_number`, falling back to the hard-coded DID. Both are wrong once
the line moves — an old message's number is not the number the next one goes out on — and
its whole job is to name what the customer is about to see. It reads `useOurLine()` now.

### What the operator must do

Set **`CRM_LINK_FROM_NUMBER=+19547758492`** in the CRM's `.env.prod` (production already
has it) and restart. Nothing else: the frontend has no copy left to update. Verify with
`curl -s https://crm.dreamteamroofingfl.com/api/connection-status` as a signed-in user —
`our_line` is the answer every screen shows.

## AMENDMENT (2026-09-16): Zuper — the source of truth for jobs; Send to Zuper, mirror locks, lead outcome. Built switched OFF

The owner's decisions, grilled twice on 2026-09-16 (the brief, then `zuper-decisions-v2`, which
REPLACED its ownership rules, which records sync and when, the automatic outcomes, deletes and
the initial-load selection). Roles: **the CRM captures customers** — AI agents answer retail
campaign calls and texts, every lead is tracked, all calls and texts live here; **Zuper is the
source of truth for jobs** (AHS and Retail) and owns quotes, invoices, payments and reports
(QuickBooks Online later: keep the API-key user separate from any QBO sync user). Branch
`feature/zuper-sync`. Code: `backend/app/zuper/` (its `__init__.py` is the map),
`backend/app/lead_outcomes.py`, a thread in `app/worker.py`, the flush listener registered in
`app/db.py`; screens `components/ZuperSettings.tsx`, `ZuperMoneyPanel.tsx`, `ZuperPhotos.tsx`,
`lib/zuper.ts`, plus the Send / locks / lead outcome controls listed under Screens.

### What this AMENDS, explicitly

* **"Nothing here ever deletes a deal" / "never delete opportunities as a side effect"
  (CLAUDE.md, the pipeline delete rule, `owen_call_id`)** — for SENT jobs and their customers,
  **deletes are mirrored both ways** (the owner's explicit choice). A job deleted in Zuper deletes
  its card here with the CRM's own delete semantics (visits detached; the card's own notes, tasks
  and links removed). A customer deleted in Zuper deletes the contact ONLY when nothing else of the
  CRM's hangs off it: **a contact that also has an unsent card or any conversation is kept** — only
  its sent cards are removed — and that is logged ("kept"). BEFORE every mirrored delete a full
  restorable snapshot (the row, its own children's rows, the ids it detached, and for a contact
  every conversation and event) goes to `zuper_delete_snapshots`, listed on Settings → Zuper →
  "Deleted by sync" with Restore. Nothing cascades beyond the record's own children. A visit is
  still never deleted: a Zuper appointment deleted cancels the CRM visit (status), logged.
* **A card's stage, visits, address, value, title, status, pipeline and owner, and a customer's
  name, phone, email and address, are no longer editable in the CRM once the card is sent** — the
  mirror locks, below. This amends every screen and route that edited them (the modal, the board
  drag, bulk actions, the calendar, the contact panel, the CLI, AI agents).
* **"Suggest turns every write into a suggestion" (AI Agents, 2026-09-15)** — two exceptions:
  `suggest_send_to_zuper` is ALWAYS a suggestion, even on Auto-pilot; and a change request on a
  sent job (reschedule, cancel, book, move stage) is carried out at once in every mode but Try-it,
  because what it does is create an urgent task for staff — it changes nothing else.
* **"A task notifies nobody" (2026-09-13)** — still true; the change-request task is urgent and
  assigned to the card owner, and notifies nobody.
* **Closing a card** (status lost / abandoned) that was never booked now requires a **lead
  outcome**. Every card closed before this stays blank; nothing is backfilled.
* **The Workiz importer** — gains ONE refusal: on and after the **Workiz cutover date** (Settings →
  Zuper; empty = Workiz still in use) it refuses before reading the export (exit 10, with the
  sentence). Its session is marked quiet so the sync's listener adds no `jobs` row: its jobs-table
  guard is untouched and still holds (tested with the sync armed).
* **`auth.EXEMPT`** — one more path, `/api/zuper/webhook`: no user credential, its own shared
  token (below). `test_auth.py` still enumerates every other route.
* **`queue.claim`** takes `types` / `exclude_types`: the main drainer no longer claims
  `zuper_push` / `zuper_delete` / `zuper_send`, which the worker's Zuper thread drains, so a slow
  or rate-limited Zuper never delays anything else. Every existing job type is unchanged.
* **The access audits** (`test_only_assigned_data.py`, `test_pipeline_permissions.py`) list the
  new routes; their module filters cover `app/zuper/api.py`.
* **AI agents still never create contacts or opportunities**, and never write to Zuper.
  Sync-created contacts and cards (`created_by = "Zuper sync"`) come only from Zuper jobs.

### Which records reach Zuper, and when

* **Retail: only when an ADMIN or an unrestricted DISPATCHER presses Send to Zuper** on the card
  (`POST /api/opportunities/{id}/zuper/send`; a TECH or any "Only assigned data" user: 403). First
  required, each missing one a sentence and the button disabled with them: the customer (a contact)
  with a name and a phone, and **the JOB's address** — street, city, state, ZIP on the card (the
  contact's can be copied with "Use contact address"). The card is a locked mirror from the moment
  Send is pressed ("queued"); the worker sends the customer (found by its CRM id, else created),
  the job, its visits, notes and tasks. A second press does nothing. A send Zuper refuses shows
  "failed" with the sentence, and Send can be pressed again.
* **AI agents may NOT send.** `suggest_send_to_zuper` makes a pending suggestion; a staff approval
  sends once (the suggestion's exactly-once approval), whatever the agent's mode.
* **AHS:** a card made by `POST /api/ahs-jobs` is sent automatically right after creation — only
  while the sync is armed and the card has what a send needs; otherwise nothing is queued and it can
  be sent by hand. The ingest's response is unchanged (owen-main's contract).
* **Leads that never book stay CRM-only.** A contact reaches Zuper only as the customer of a sent
  job. A customer created in Zuper with no job creates no contact here.
* **The day-one load** (`python -m app.zuper.load`, dry run; `--commit`): EVERY card in the AHS
  pipeline, and Retail cards in **Scheduled, Inspection / Estimate, Estimate Sent (open)** and
  **Invoice (open and won)** — selected by stage NAME inside the Retail pipeline; the dry run prints
  the count per group; a missing named stage is a refusal with a sentence. Retail New Lead and
  Follow Up stay CRM-only. A selected card a send would refuse (no customer, name, phone or job
  address) is skipped and listed by id, never sent half-made (judgement call).
* **New Workiz jobs before the cutover date:** an AHS one is sent; a Retail one is sent when the
  import files it in a booked or owing stage. The 15-minute sweep does it, because the importer may
  queue nothing itself. After cutover the importer refuses.
* A job created in Zuper in the AHS or Retail category becomes a card here (its customer a contact,
  created if needed); Zuper's template categories are ignored.

### After a card is sent: the mirror

* The card stays on its board. **Zuper-only, refused server-side with 409 "Change this in Zuper…"
  before anything is written** (`app/zuper/locks.py`), and disabled in the UI with that reason:
  stage, schedule / visits, job address, value, title, status, pipeline, owner — and the name,
  phone, email and address of any contact that has at least one sent card. Paths: the modal's
  PATCH (an unchanged echo passes), the board drag, bulk stage and owner (refused whole), book /
  edit / cancel a visit (the calendar and its drag), the contact PATCH, the CLI (it is the API) and
  AI actions (they call the same services). The locks hold whether or not the sync is switched on.
* **Editable in the CRM and synced both ways:** notes, Checklist answers, opportunity tasks ↔ Zuper
  service tasks (completion included). The CRM-owned "CRM" job fields go to Zuper.
* **The board** badges a sent card "Managed in Zuper" and does not drag it; the modal shows the
  badge and a link to the job (`ZUPER_JOB_URL_TEMPLATE`, default
  `https://app.zuperpro.com/jobs/{uid}/details` — UNVERIFIED). **The calendar** draws a sent job's
  visits read-only.
* **AI agents** read the mirrored job in `get_context` — status, visits, quotes and invoices — and a
  change request on a sent job (reschedule, cancel, book, move stage; there is no address action)
  becomes an URGENT task for staff naming the Zuper job, assigned to the card owner, with zero
  Zuper writes; the agent is told never to say it is done.
* **Quotes, invoices and payments:** read-only, on the card ("Quotes & invoices") and in the contact
  panel — number, status, total, balance, dates. The card's value follows the invoices' total, else
  the approved quotes' total, once one exists.
* **No automations run on Zuper jobs in the CRM.** "Invoice paid → Won" and "quote declined → urgent
  task" were built and then DROPPED by the owner. `app/zuper/outcomes.py` is the one documented
  place a future owner-approved rule would hook in: `ENABLED = False`, `RULES = []`, pinned by a test.

### Field ownership on a sent job, every overwrite logged

A three-way merge per field against `zuper_mappings.base` (what both sides last agreed):

| field | winner |
|---|---|
| title, stage, job address, owner, value, a visit's schedule; the customer's details | **Zuper** |
| a Workiz-origin job's title, stage, address, and its Workiz visit's schedule — before cutover | **Workiz** (the importer's value is pushed; a Zuper edit is put back) |
| the "CRM" job fields (CRM Opportunity ID, CRM Link, Technicians, Workiz Job #, AHS Job ID, CompanyCam Project, Job Type) | **CRM** |
| notes, Checklist answers, tasks | **latest edit** |

`zuper_conflict_log` records each value a side had changed that the sync overwrote: record, field,
rule, winner, side written to, before and after. Judgement calls, all overrulable:

* A plain propagation (only one side changed) is counted, not logged.
* The CRM owner follows Zuper's assigned user only when a CRM user has that email; a job assigned to
  nobody the CRM knows leaves the CRM owner as it is (technicians have no Zuper users).
* A Zuper Checklist answer the CRM question cannot hold (an option it does not offer) is put back in
  Zuper and logged (`crm_cannot_hold`).
* The importer bypass covers the job fields the owner named. The importer also rewrites a contact's
  details; those are Zuper's, so the next sync puts them back (logged) and does not push them.
* A card's primary contact is not on the lock list (the owner's list names the contact's details,
  not which contact); changing it re-points nothing in Zuper.
* Deleting a stage or pipeline still moves its cards (a structural admin edit, 2026-09-13), sent
  ones included; the next sync puts a sent card back where Zuper has it when it can.

### Switched off, three ways — and never notifying a customer

1. `ZUPER_SYNC_ENABLED` (default false): the client refuses before a connection exists, the listener
   returns on its first line, the worker's Zuper thread does nothing, the panels read only the local
   cache, Zuper photos report "off", Send to Zuper answers 409, and the webhook answers **503** with
   a sentence. Tested: CRM edits, deletes, a worker tick, the panels, the setup check and the webhook
   make ZERO requests and queue nothing.
2. The Settings → Zuper switch, refused (409, the reasons) until the setup check passes and every
   "confirm by hand" item is ticked by an ADMIN. Un-ticking a safety item pauses the sync at once.
3. `ZUPER_API_KEY` / `ZUPER_API_KEY_FILE`. The operator's commands (`python -m app.zuper.setup`,
   `python -m app.zuper.load`) need only the key and are dry runs unless given `--commit`.

`app/zuper/client.py` is the only code that sends a request to Zuper. Before anything is built it
refuses a **denylist** — Zuper Connect `/telephony/*` (real SMS), every `send` endpoint (quotes,
invoices, payment requests), customer-portal invites, notification sends, reminders, email
endpoints — and every write to quotes, invoices, payments and credit notes; then an **allowlist**
of exactly the endpoints used; then the off switch; then a dry run's writes. The region lookup
(`accounts.zuperpro.com/api/config`) never gets the key. `tests/zuper_guard.py` (conftest) strips
`ZUPER_*` and refuses DNS / connections to `*.zuperpro.com`; the fake Zuper records and fails any
denylisted request that reached it. The customer-notification switches in Zuper are on the setup
checklist (G1–G6) as confirm-by-hand items.

### Speed, loops, the webhook, the sweep, the digest

* **CRM → Zuper:** one flush listener (every path covered). After the ROOT commit — a SAVEPOINT's
  commit does not count (the AI hooks run in one; found by a test) — one `zuper_push` job per
  changed record of a SENT card (the card, its notes, its tasks), dedupe key
  `zuper:push:<kind>:<id>`, written in a session of its own so a coalesced duplicate can never fail
  the person's save; the job releases its key as it starts. A delete is snapshotted inside the
  deleting transaction; `zuper_delete` mirrors it.
* **Zuper → CRM:** `POST /api/zuper/webhook`, token in `X-Webhook-Token` (or `?token=` if Zuper cannot
  send a custom header — UNVERIFIED); wrong or missing = 401 and nothing stored. Raw deliveries are
  stored FIRST in `zuper_webhook_inbox` (a repeated body is one row); a signature header, if one ever
  arrives, is verified as HMAC-SHA256 under `ZUPER_WEBHOOK_SECRET` (bad = 401, nothing stored).
  Processing never trusts the payload: it drops events made by the "CRM Sync" user, re-reads the
  record, and ignores content whose hash has not changed (echo).
* **The 15-minute sweep:** Zuper records updated since the cursor (minus 5 minutes) for customers,
  jobs, appointments, quotes and invoices, after checking every sweep, per list, that
  `filter.updated_at_from` really narrows (asked for the future; if not, the list is paged whole and
  compared on `updated_at`); deleted customers / jobs by `filter.is_deleted`; new qualifying Workiz
  jobs sent; and CRM → Zuper by content hash for sent records (what a quiet importer session or a
  paused switch changed; a mapped record gone from the CRM). At most 300 pushes a sweep. The initial
  load starts the cursor.
* 180 requests / minute (the account allows 200), 429 retried up to 6 times honouring Retry-After;
  GETs retried twice on 5xx; an outage is a heartbeat error, never a crash.
* **The daily digest:** one private note per customer on their most recently updated open SENT job:
  `Sep 16 - 3 texts (2 in, 1 out) - 1 call, 4 min, answered - <CRM link>` — counts only, never a
  message, recording or transcript (tested). America/New_York days, written after 01:00; from the
  day the switch was first turned on.

### Lead outcome (CRM-only)

`opportunities.lead_outcome` / `lead_outcome_note` / `lead_outcome_set_at` (`app/lead_outcomes.py`):
Spam, Wrong number, Not interested, Price shopping, Out of service area, Duplicate, No response,
Other (+ a note, required for Other). **Required when a card is closed (lost / abandoned) without
booking** — "booked" = it has a visit that is not cancelled, or was sent to Zuper — on the modal's
PATCH and on Add opportunity. Settable in the modal, by bulk action
(`POST /api/opportunities/bulk/lead-outcome`, STAFF) and by an AI agent (`set_lead_outcome`; Suggest
mode makes it a suggestion). Reporting: `GET /api/reports/lead-outcomes?since&until&pipeline_id` —
counts by source (the card's) and by campaign (`owen_campaign`), for the period the outcome was set,
over the cards the reader may see; refused for restricted users like the other reports. Columns on
the card rather than a table: one value per card, read with the card everywhere, never a history.
Zuper never sees it.

### Setup, the initial load, Settings → Zuper

`python -m app.zuper.setup` checks every item of the owner's checklist with GETs (the region when
`ZUPER_COMPANY_NAME` is set, the key's user is "CRM Sync" with Admin rights, the customer and job
fields with exact labels, types and options, the lead sources, the categories and statuses, a
webhook pointing at this CRM). Where Zuper documents no way to read a setting (notification
switches, the Lead master tag, company time zone / currency, the old key deleted, and — until the
first probe shows otherwise — custom-field definitions and lead sources) the item is **confirm by
hand**. `--commit` creates the categories "AHS" and "Retail" with the pipelines' stages 1:1 as
statuses, mapped by uid (board order: position, then id — every Retail stage has position 0 in
production, so id decides; AHS's empty "Submit Invoices" included; a repeated name gets " (2)"), and
records the results. The load is idempotent (customers and jobs indexed by their CRM id field before
anything is created — Zuper has no idempotency keys), resumable (each record committed `creating`
before its create, so a crash is followed by a search, never a duplicate), rate-limited, and ends
with a verification report (CRM vs Zuper per type, mismatches by id). Reports carry counts and ids
only. Settings → Zuper (ADMIN): connection, the switch, the cutover date, setup results with
confirm-by-hand ticks, heartbeat, counts per type, errors in sentences, the webhook inbox, the load
report, the conflict log and "Deleted by sync" with Restore.

### Screens — OURS; GoHighLevel has no Zuper screens

Built in the Opportunities module's rebuilt style (refs/opps; `components/opportunity/ui.tsx`
tokens, the CompanyCam and My Staff cards and tables). No emoji; outline icons. Every assumption:

* **Settings → Zuper** (ADMIN only; section after CompanyCam): chips green (On, PASS,
  Paid/Approved), amber (Paused, Confirm by hand, Partial), red (FAIL, Declined/Void/Overdue), blue
  (other statuses), grey (Off, Draft). The switch asks for confirmation both ways (My Staff's
  dialog) and shows a 409 sentence under it; while anything blocks it the switch is NOT drawn — "The
  sync can be switched on when:" and the reasons are listed. Cutover date: a date input, Save faded
  until a new date is typed, Clear only when a date is saved; "Workiz is still in use" /
  "…until <date>" / "Workiz retired on <date> — the importer refuses to run". "Run setup check"
  is replaced by a sentence without the env flag or the key; each confirm-by-hand item shows
  "Confirmed by hand — by <email> at <time>", and un-ticking goes straight to the server (which
  pauses the sync). Tables page 25 rows, Previous / Next only past one page. The conflict log's
  Record column reads "Opportunity #id" with the Zuper uid beneath; `value` before / after reads as
  money, other values cut at 120 characters, objects as JSON; rule names in words (lib/zuper.ts);
  field labels: `cl:` → "Checklist: …", `crm:` → the label, first_name → "First name", company →
  "Business name", source → "Lead source", owner_email → "Owner", starts_at / ends_at → "Start" /
  "End", body → "Note". "Deleted by sync" states: Waiting, Mirrored, Skipped, Kept, Failed, Restored,
  Restore failed; Restore only on restorable rows, with a dismissible result box. Failed sync jobs
  read "Push a change", "Mirror a delete", "Send a card". Times in America/New_York
  ("Sep 16 2026, 9:17 AM (EDT)").
* **Send to Zuper**: a line under the modal title. Not sent: "Send to Zuper" with a confirmation
  saying the customer and job are created in Zuper and what becomes Zuper-owned; disabled when it
  cannot be sent, the sentences as its tooltip AND as grey text beside it (reasons must not be
  hover-only); not drawn at all for a technician or an "Only assigned data" user. Queued: a
  "Sending to Zuper…" chip; the modal re-checks every 3 s. Sent: a "Managed in Zuper" badge, "Open
  in Zuper" when a job link is configured, and the sent time. Failed: the sentence and "Send to
  Zuper again".
* **The locks in the UI**: title, pipeline, stage, status, value, owner and the job address inputs
  disabled and greyed with the tooltip "Change this in Zuper"; "Use contact address" is not drawn on
  a locked card (the Address heading carries the reason); the Checklist's linked address (and, for a
  locked contact, the linked email) disabled; booking disabled with "This job's visits are scheduled
  in Zuper." A managed board card shows the badge, does not lift or start a text selection, and a
  drop of one is ignored. The calendar has no drag or resize today; a locked visit is marked, and its
  dialog keeps the fields visible but disabled and offers only Close, with "This visit belongs to a
  job managed in Zuper. Change this in Zuper." The contact panel says once that the customer's
  details are managed in Zuper (its address is not editable there anyway), and Add opportunity locks
  a locked contact's email and phone.
* **Quotes & invoices** (modal nav item after Photos, before AI agent; drawn only when the card is
  linked or has a document): "From Zuper — read only", last synced; Quotes (Number, Status, Total,
  Date, Expires) and Invoices (Number, Status, Total, Balance, Date, Due); "No quotes or invoices in
  Zuper for this job yet."; "The Zuper sync is paused / off, so these may be out of date."; dates
  "Sep 10, 2026", never time-zone shifted. Contact panel: a collapsible "Zuper quotes & invoices (n)"
  drawn only with documents, rows "Quote Q-1001" / "Invoice INV-7", chip, "$total · card title"
  (the title opens the card). Zuper photos: a "Zuper · N files on the job in Zuper" group below
  CompanyCam's, a thumbnail opens the file in a new tab (CompanyCam's viewer would print "Photo by
  unknown"); one line when unavailable, nothing when off or not linked.
* **Lead outcome**: a full-width "Lead outcome" select under Status, shown when the status is Lost or
  Abandoned or an outcome exists; a required note box for Other (a note on another outcome only when
  one exists); Update / Create stays disabled with the server's exact sentences (pinned by a test).
  Bulk "Set lead outcome" (not drawn for a TECH) with an inline note for Other. Reporting → "Lead
  outcomes": the shared date range (the end day included), a pipeline select, By source and By
  campaign tables, blank rows "No source" / "No campaign", zero cells grey.
* **Deep links** `/opportunities?opportunity=<id>` and `/contacts?contact=<id>` (the "CRM Link"
  fields) open the record through `openRecord`, then drop the parameter so a reload does not reopen
  it; other parameters are kept.
* `tests/browser_zuper.py` drives it in headless Chromium against a fake Zuper on a throwaway SQLite
  (73 checks): setup check, confirm by hand, switch on, the load, a Send (one customer and one job
  POST, a second press answers "already"), a managed card's drag and title PATCH refused with
  nothing changed, the locked visit and contact, closing a lead (no outcome refused, Other without a
  note refused), the Lead outcomes report, a technician with no Send button and a 403, the conflict
  log, Restore, money panels and Zuper photos.

### Zuper facts relied on that are UNVERIFIED (no key on the build machine)

Every path is one constant in `client.PATHS` and every response is unwrapped in `zapi.py`, so a wrong
guess is a one-line fix. The first live **read-only** probe (GETs only, the CRM Sync key, a quiet
hour) should confirm, in order: `POST accounts.zuperpro.com/api/config {"company_name"}` answers
`dc_api_url` (the one POST that writes nothing); `GET /user` returns the key's own user (first / last
name, `user_uid`, role); `GET /user/all` rows carry `email` and `user_uid`; `GET
/customers?page=1&count=1` wraps rows in `data` with `total_records` / `total_pages`, and a customer
carries `customer_uid`, `customer_contact_no.mobile`, `customer_address{street,city,state,zip_code}`,
`customer_company_name`, `customer_tags`, `custom_fields[{label,value}]`, `source_uid` or
`customer_source`, `updated_at`, `is_deleted`; `filter.keyword`, `filter.updated_at_from` (asked for
a future moment it must return 0 — the sweep checks this itself) and `filter.is_deleted=true` on
customers AND jobs; `GET /jobs/category` and `GET /jobs/status/{category_uid}` field names; a job's
`job_category`, `current_job_status{status_uid}`, `customer`, `assigned_to`, `customer_address`,
`custom_fields`; `GET /jobs/{uid}/note`, `/service_tasks`, `/attachments` and `GET
/appointments?filter.job_uid=` exist, with their uid / text / status / time field names; `GET
/estimate` and `GET /invoice` field names (number, status, total, balance, dates, `job.job_uid`);
`GET /settings/custom_fields?module=` and `/settings/lead_sources` (a 404 keeps them confirm-by-hand);
`GET /service/notifications/webhook`. NOT probe-able read-only — confirmed on the first Send of ONE
test card in a quiet hour, then its deletion in Zuper checked against "Deleted by sync": the create /
update bodies (`{"customer": …}`, `{"job": …}` with `job_uid` in the body for `PUT /jobs`), `POST
/jobs/status_new/{category}` with `status_type` NEW / STARTED, `PUT /jobs/{uid}/status` and
`…/status/rollback`, note / service-task / appointment bodies, `POST /customers/{uid}/recover` and
`POST /jobs/{uid}/recover`, whether PUT replaces or merges, the web link to a job, and the webhook
payload and whether Zuper can send a custom header.

### Not built

Organizations and properties (the company name goes on the customer); Zuper "Requests"; pushing
CompanyCam photos into Zuper (the link only, as decided); recovering deleted Zuper notes / tasks /
visits by a recover endpoint (none documented — they are re-created); any automatic outcome on money
documents (dropped; the hook is off); a `ghl` CLI for the sync. **No request has ever been sent to
Zuper from this server.**

### Migration `f5c1e9a3d742`, on `e1b4d7c96a05` — ONE revision

Eight CREATE TABLEs (`zuper_settings`, `zuper_mappings`, `zuper_sync_state`, `zuper_webhook_inbox`,
`zuper_conflict_log`, `zuper_delete_snapshots`, `zuper_documents`, `zuper_digests`) with plain
`op.create_index`es, and three nullable ADD COLUMNs on `opportunities` (`lead_outcome` VARCHAR(40),
`lead_outcome_note` TEXT, `lead_outcome_set_at` TIMESTAMP). No ALTER of an existing column, no DROP,
no UPDATE, no backfill, no batch mode in `upgrade()`; `server_default` on every non-nullable column
with a default. Every reference to a CRM record is a plain integer, so no existing delete path gains
a foreign key. `tests/test_zuper_migration.py` stands a SQLite up at `e1b4d7c96a05` with
production-shaped rows, upgrades, and asserts every existing table's columns and rows unchanged (the
three added columns at the end of `opportunities`, NULL), exactly the eight new tables, empty, their
server defaults, a clean downgrade / upgrade round trip, and an `upgrade()` that only creates tables,
creates indexes and adds columns.

### Amendment 2026-09-17 (owner, via the go-live supervisor): register the webhook and switch on

Replaces runbook steps 7-8's "the operator registers the webhook by hand" and the supervisor brief's
"do not register the webhook, do not enable the sync": once the day-one load is verified the sync
is switched ON. `python -m app.zuper.webhook` (dry run; `--commit`) registers one Zuper webhook per
(module, event) for job, customer, appointment, note, service task, estimate and invoice, posting
JSON to `/api/zuper/webhook` with the `X-Webhook-Token` header (or `?token=` if Zuper keeps no
header). Idempotent by module + event + URL (query ignored); never edits or deletes a Zuper webhook;
the first refusal stops it with Zuper's message. Event names and the header field are UNVERIFIED
constants. Zuper did not list webhooks on the first live read (both candidate list endpoints are
tried), so each registered webhook is also recorded as a `zuper_mappings` row (crm_type "webhook",
committed "creating" before the POST; a refused create drops it; an unknown outcome is never
retried) — that record keeps it idempotent and lets the setup check pass. The first live
`setup --commit` was refused "Category Name Missing" for `{"job_category": {...}}`: category and
status creates now try candidate body shapes (flat first) ONLY while Zuper refuses, never after an
answer that may have created something. Checklist item B3 now reads "do NOT delete the key" (the
sync uses the owner's own key). Also: the initial load now STOPS on the first refused record of a kind (as the runbook
already promised) and keeps the reason for later per-record refusals.
