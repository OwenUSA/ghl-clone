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
