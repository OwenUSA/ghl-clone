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
