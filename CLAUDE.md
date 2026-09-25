# ghl-clone — working notes

Read **`DECISIONS.md` first each session.** It records locked decisions and the
measurements behind them. Do not re-litigate them without being asked.

A GoHighLevel replacement for one roofing company (Dream Team Roofing, Bradenton FL).
Single-tenant. Scope is Contacts · Conversations · Opportunities · Calendars ·
Reporting. Everything marketing-related, Payments, and the whole agency layer are
deliberately out.

## Safety contract

- **The live GHL account is read-only.** The `capture/` harness drives a real logged-in
  browser. Never click anything that writes, deletes or sends there. Full rules in
  `DECISIONS.md`.
- **No message leaves the building until somebody arms it.** There are two
  `MessageTransport` implementations. `get_transport()` returns `CrmLinkTransport` —
  which hands the message to owen-main for delivery over the real BulkVS DID
  **`+19547758492`** (it replaced `+19544829099` on 2026-09-16; that number is now fully
  unbound. ONE definition: `crmlink.DEFAULT_FROM_NUMBER` / `CRM_LINK_FROM_NUMBER`, reported
  to the browser as `our_line` on `GET /api/connection-status`) — only when **both** `CRM_LINK_BASE_URL` and `CRM_LINK_API_KEY` are
  set; otherwise `LoggingTransport` records `delivery_status = LOGGED_ONLY` and transmits
  nothing. `GET /api/health` reports which one is live (`"crm_link": true|false`) — ask
  it rather than assume. **With the link armed and SMS switched on in owen-main (10DLC is
  approved, 2026-09-15), every send is a REAL text** — that is what the `--yes` guard has
  always been for. owen-main still refuses opted-out (STOP), blocked and switched-off
  sends with a sentence, recorded REFUSED on the thread.
- **No automatic texts at all — only texts a person sends** (2026-09-15). Rules 1, 3 and
  4 in `app/automations.py` (missed-call text-back, appointment reminders, stage-change
  texts) are OFF by module flag: the hook answers with a sentence, enqueues nothing, and
  the worker refuses a job of that type left in a queue. Settings → Automations shows them
  Off with the reason (`GET /api/automations`). Tests that pin the kept mechanics ask for
  `rule_3_armed` / `rule_4_armed` by name. **No test reaches owen-main**:
  `tests/owen_guard.py` strips `CRM_LINK_*` / `OWEN_*` and refuses any connection to an
  owen-main host or whatever the link is pointed at, failing the test that tried.
- **Never run `python -m app.seed` against the working database.** It calls
  `drop_all()`. It is in the `deny` list in `.claude/settings.json`.
- **No AI agent acts by itself, and no test reaches a model provider.** Every agent is
  created Off, Settings → AI Connections has "Pause all AI agents", and an agent's text goes
  through the same `get_transport()` as a staff text. `tests/ai_guard.py` refuses any lookup of
  api.anthropic.com / api.openai.com during pytest and fails the test that tried.

## Running it

```bash
uv sync --all-groups                          # repo root; also installs the `ghl` CLI
uv run alembic upgrade head                   # from backend/ — the schema lives here now

cd backend
uv run uvicorn app.main:app --port 8000       # API
uv run python -m app.worker                   # queue drainer, separate terminal
cd ../frontend && npm run dev                 # UI on :5173
```

**Restarting the backend on Windows:** free the port from PowerShell. `pkill` silently
does nothing here and leaves stale code serving, which has already nearly caused a false
verification once (`DECISIONS.md`).

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen |
  Where-Object { (Get-Process -Id $_.OwningProcess).ProcessName -eq 'python' } |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

## Auth

Every `/api` route requires a credential. The only exceptions are `/api/health`,
`/api/auth/login`, `/api/auth/refresh`, `/api/auth/logout` and `/api/zuper/webhook` (its own
shared token in `X-Webhook-Token`; 503 while the Zuper sync is off — 2026-09-16).

- **Browser** — JWT in an httpOnly cookie, 15 min access + 7 day refresh, plus a
  double-submit CSRF token on cookie-authenticated writes.
- **CLI and machines** — `Authorization: Bearer ghl_pat_...`, revocable from Settings.
  Bearer requests skip CSRF; an `Authorization` header cannot be sent ambiently.

Enforcement is one app-level dependency (`app/auth.py:require_auth`), so **a route added
later is protected by default**. `test_auth.py` enumerates `app.routes` and fails if any
route answers without a credential — if you add an endpoint and that test fails, that is
the system working.

Note `/docs` and `/openapi.json` are *not* covered by the gate: FastAPI registers them as
plain Starlette routes with no dependant. Verified, deliberate, and pinned by a test.

Roles are `ADMIN` / `DISPATCHER` / `TECH`. Broadly: everyone reads, staff write, admin
deletes and manages users. A TECH can move an opportunity between stages and send a
message, but cannot edit records. **Internal notes (`NOTE`, `INTERNAL_COMMENT`) are
STAFF-only** as of 2026-09-10, on every path — the thread view, `/api/messages`,
`/api/search` and the composer. Asking for them by name gets 403; an unfiltered read
is just narrower. See `DECISIONS.md`. The same rule, through the same predicate
(`auth.sees_internal`), covers an opportunity's own notes (2026-09-13): the modal's Notes
tab, `/api/opportunities/{id}/notes` and the board card's note count.
An appointment's `notes` are the Book appointment modal's "Internal notes" and follow
the same rule (2026-09-13): a TECH gets `notes: null, notes_visible: false` and reads
`description` / `location` instead.

Machine tokens can be scoped (`events:write` for the telephony feed) — a scoped token
can never exceed its owner's role.

**Pipelines can be restricted to named users** (Opportunities → Pipelines → ⋮ → Manage
permissions; nobody selected = everyone, ADMIN always). A user without access must not
see that pipeline or any deal in it anywhere, so **every route that reads opportunities
or pipelines goes through `app/pipeline_access.py`** and answers 404, never 403, for a
hidden id. `test_pipeline_permissions.py` enumerates the routes: add one that touches
deals without recording how it enforces access and that test fails. See the
2026-09-13 amendment in `DECISIONS.md`, which also records that deleting a stage or
pipeline now MOVES its deals (no automation, one transaction) instead of refusing.

**Locked out?** `uv run python -m app.bootstrap set-password --email you@example.com`
(from `backend/`), then `list-users` to check who can sign in.

## Use the `ghl` CLI, not ad-hoc curl or psql

```bash
uv run ghl --help                 # every group, self-describing
uv run ghl exit-codes             # what a non-zero exit means
```

Conventions worth knowing before you start:

- **`--json` works anywhere on the line** and prints the raw API payload and nothing
  else, so `... --json | jq` is always safe. Errors go to stderr as JSON.
- **Names work wherever ids do**: `--contact "jane doe"`, `--stage "Inspection"`.
  An ambiguous name exits **5** and lists every candidate with its id — it never
  guesses. This matters: the real pipeline has two distinct stages both called
  "Call Back".
- **Exit codes**: `0` ok · `2` needs `--yes` · `3` not authenticated · `4` not found ·
  `5` ambiguous · `6` backend down · `7` forbidden · `8` rejected.
- **`--yes` is required** for anything customer-facing or destructive (`msg send`,
  `contacts delete`, `opps delete`, `appts cancel`). Without it the command refuses
  with exit 2 and makes no network call.

There is also a **ctrl+K palette** in the browser (cmd+K on a Mac), backed by one
endpoint, `GET /api/search?q=...&limit=5`. It returns contacts, opportunities and
message bodies grouped and capped, reporting each group's true `total`. It does
**not** search call transcripts — use `ghl calls list --q` for those.

```bash
uv run ghl contacts list --q smith --json
uv run ghl calls list --since 7d --direction INBOUND --status no-answer
uv run ghl calls list --q skylight            # searches call TRANSCRIPTS
uv run ghl convos show "jane doe"
uv run ghl msg send -c "jane doe" -b "We can be there Tuesday" --yes
uv run ghl opps create -t "Jane roof" -c "jane doe" --stage "New Lead" --value 9500
uv run ghl opps move 42 --stage "Inspection"
uv run ghl jobs list --status pending         # did the automation fire?
```

## Texting is live: manual texts only, and New message (2026-09-15)

Conversations → the compose icon beside "Call a number" opens `components/NewMessageDialog.tsx`:
number (formats as you type, `textProblem` in `lib/dialPad.ts` = `text_problem` in `main.py`),
message with a character / segment count (`lib/smsSegments.ts`), "Sending from …" — the
CONFIGURED line, from `useOurLine()` (`lib/ourLine.ts`), never a constant —
Send. `POST /api/messages/new`: a number a contact holds is a send on the contact's conversation
(DND suppresses); anyone else's goes on the number-only thread (found or created, never a
contact). A restricted technician may text only their own jobs' customers. Quo is never a sender
— no route takes a `from_number`. Under a bubble: owen-main's REFUSED sentence; FAILED says it
can be retried and has Retry. An inbound MMS arrives as text plus `[N attachments — view in
OWEN]`; the note is stripped and, since 2026-09-16, the pictures themselves are shown — see
the next section. A message whose pictures the CRM does not hold still shows the count
(`lib/mmsNote.ts`).
`uv run python -m tests.browser_sms` (from `backend/`) drives it in headless Chromium with
owen-main replaced by a recorder; it is not collected by pytest. See DECISIONS.md.

## Pictures in text messages, both directions (2026-09-16)

An inbound MMS shows its **pictures** in the thread — thumbnails in the bubble, click for a
viewer with next/previous, sender and time — and the composer and New message accept images
(drag, paste or pick), preview them and send them with the text.

- **The CRM keeps its own copy**, because carrier media links expire. The bytes are on disk
  under `MEDIA_ROOT`, content-addressed (`app/attachments.py`), one `message_attachments`
  row each (`app/message_media.py`). In production that is the named `media` volume at
  `/var/lib/ghl-clone/media`, set in `Dockerfile.api` — **do not set `MEDIA_ROOT` in
  `.env.prod`**. `GET /api/health` reports `"media_writable"`; check it after a deploy.
- **Inbound is a queued job, not part of the ingest.** `POST /api/events` takes `num_media`,
  writes PENDING rows and enqueues `fetch_message_media`; the worker asks owen-main
  (`GET /api/crm-link/messages/{id}/media/{i}`), sniffs the bytes and stores them. **A
  failed fetch never loses the text** — the bubble says "Picture unavailable" with a Retry.
- **Nothing public.** `GET /api/attachments/{id}` is the only way a picture reaches a
  browser; it follows the THREAD's rule through `message_media.visible` and answers 404,
  never 403. The browser never sees a carrier URL or an owen-main token.
- **Outbound: owen-main publishes, this repo never does.** The composer uploads
  (`POST /api/attachments` → a DRAFT), the send passes `attachment_ids`, and the bytes go to
  owen-main, which mints an unguessable, signed, 30-minute, single-object URL for BulkVS.
  A picture the phone system will not take **stops the send entirely**.
- **Type is decided by the magic number** (jpeg/png/gif/webp/heic), never the header; 5 MB
  each, 10 inbound and 5 outbound per message. **Nothing attaches a picture automatically**
  — no rule and no AI agent has a draft to pass.
- `uv run python -m tests.browser_pictures` (from `backend/`) drives the thread, the viewer
  and the composer in headless Chromium; not collected by pytest. See DECISIONS.md,
  2026-09-16, for the outbound URL's security note and the operator steps.

## Delivery receipts are receipts, not messages (2026-09-16)

BulkVS posts carrier DLRs to the SAME webhook as an inbound text. owen-main stored them as
inbound messages and relayed them, so customers' threads held texts they never wrote
(`id:... stat:UNDELIV err:255 text:...`). Fixed on both sides:

- **owen-main** recognises one at `/webhooks/bulkvs/message` (`providers/bulkvs.py`
  `parse_delivery_receipt`), never stores or relays it, and applies it as a receipt
  (`services/dlr.py`). **The DLR `id` is NOT the /messageSend RefId** (4551F89F vs
  1162999967) — it correlates on our DID + recipient + text echo + submit time, and says so.
- **The CRM** refuses to file one on ingest too (`app/dlr.py`), because the two systems
  deploy separately. A carrier failure reads as a sentence: "The carrier rejected it
  (error 255)."
- **Existing junk:** `python -m app.scripts.backfill_dlrs` (owen, hides — never deletes) and
  `uv run python -m app.dlr_cleanup` (CRM, removes them from threads and repairs the unread
  badge). Both dry-run by default, counts only. See DECISIONS.md.

## The CRM's line lives in ONE place (2026-09-16)

`+19547758492` replaced `+19544829099`, which is now fully unbound. `crmlink.DEFAULT_FROM_NUMBER`
/ `CRM_LINK_FROM_NUMBER` is the only definition; `GET /api/connection-status` reports it as
`our_line`; the browser reads it through `lib/useOurLine.ts`. "Sending from", "Calling from",
the AI escalation copy and "it cannot text itself" all follow it. **History keeps its own
number** — an event's source chip reads the event's `source_number`, never the configured line.

## Unknown numbers are threads, not contacts (2026-09-13)

An inbound call or text from a number no contact holds creates **no contact, no deal and no
job**. It lands on a `number_threads` row that shows in the inbox beside contact threads
(`kind: "number"`, `key: "n<id>"` — select rows by `key`, ids collide across the two kinds).
The moment a contact exists with that number, by any path, a `before_flush` listener moves
the whole history onto the contact's thread (`app/number_threads.py`). The missed-call
auto text-back is **off**. Converting the old auto-created contacts back:
`uv run python -m app.convert_auto_contacts` (dry run; `--commit` writes). See DECISIONS.md.

## AHS work-order emails become cards (2026-09-14)

owen-main posts each American Home Shield work order it parses to `POST /api/ahs-jobs` (and
cancellations to `/api/ahs-jobs/cancellations`), with the `events:write` token. One card per
AHS job in Dream Team Roofing AHS / New Lead, the contact matched or **created**, the work order
as a staff-only note, **nothing enqueued**. `custom_fields.ahs_job_id` is the idempotency key and
the reserved read-only namespace `ahs_job_*` (not `ahs_` — "AHS claim number" is the owner's own
field). The Workiz importer finds these cards by `created_by = "AHS email"` + `ahs_job_id` + no
`workiz_id`. Off by default on owen-main (`CRM_LINK_EMAIL_JOBS_ENABLED`). See DECISIONS.md.

## The call Checklist (2026-09-14)

The Checklist is custom fields in one tab named "Checklist", with three per-question
settings on `custom_field_defs` (`script`, `linked_field`, `details_when`; a details answer
is stored under `<key>__details`). Create it with `uv run python -m app.checklist_seed`
(dry run; `--commit` writes; idempotent, never undoes the owner's edits). Add opportunity
is GoHighLevel's modal (`components/AddOpportunityModal.tsx`): contact required in the UI,
not in the API. AHS card titles are name-first. See DECISIONS.md.

## CompanyCam job photos (2026-09-14)

Photos stay in CompanyCam; the CRM stores only which project belongs to which card
(`companycam_links`), a heartbeat, a review list and creation requests. `app/companycam.py` is
the ONLY code that talks to CompanyCam, and its `_request` refuses every non-GET except
`POST /projects` — **creating a project is the one write this package can ever send**.

```bash
COMPANYCAM_API_TOKEN=...               # empty = feature off, Photos tab says so, nothing sent
COMPANYCAM_CREATE_PROJECTS=true        # DEFAULT TRUE once a token is set
COMPANYCAM_SYNC_ENABLED=false          # hourly linking check; turn on after the first run
uv run python -m app.companycam_link            # DRY RUN, counts only
uv run python -m app.companycam_link --commit   # link existing projects to cards
uv run python -m app.companycam_link --status   # the hourly check's heartbeat
```

- **Linking order:** a project named `Workiz <job #> - ...` links to the card with that
  `workiz_id` (`workiz_job`); everything else by address (card's, else contact's) to one or
  ALL matching cards; a name-only match goes on Settings → CompanyCam → Review, never linked.
- **Creation** (worker, every minute): a card made by `POST /api/opportunities` or by an AHS
  email, with an address — or given its first address — gets a project. It SEARCHES first and
  links what exists. A Workiz-imported card never creates. Named `CRM <id> - <customer>` /
  `AHS <ahs_job_id> - <customer>`.
- **The browser never gets an image URL or the token**: `/api/companycam/photos/{id}/{variant}`
  relays the bytes, and only for a project linked to a card the reader can see.
- The worker runs CompanyCam on its own thread; a CompanyCam outage is a heartbeat error, never
  a crash. See the 2026-09-14 CompanyCam amendment in `DECISIONS.md`.

## Answering a call in the browser (the softphone)

The CRM can be a ring destination. `+19544829099` already rings two mobiles in parallel
and the first to answer takes the call; a signed-in browser now rings alongside them.

**It is off unless the deployment is told where the phone system is:**

```bash
OWEN_BASE_URL=http://callmon_app:8000      # OWEN, on the internal docker network
OWEN_SOFTPHONE_KEY=owen_sk_...             # an OWEN API key with the `crm_link` scope
```

With either unset, `POST /api/softphone/credentials` answers 503 without making a
request and the status dot's phone row says browser calling is not set up. That is the default in tests and in a fresh checkout,
so nothing here can reach a live telephony service by accident.

On the OWEN side the user's email must also be listed in `CRM_LINK_SOFTPHONE_OPERATORS`
**and** have an `[operator-<slug>]` trio in `asterisk/pjsip.conf`. An email that is not
provisioned gets a 403 saying so — we never invent an operator.

- `frontend/src/lib/softphone.ts` — the SIP.js hook. Registration state comes only from
  a SIP registration callback, never from "register() did not throw".
- `frontend/src/components/Softphone.tsx` — the incoming-call card. Mounted once in
  `App.tsx`; it is deliberately not part of any page.
- **The status dot** (2026-09-14) replaced the floating bottom-right dock:
  `components/StatusIndicator.tsx`, top right on every page, the worst of three checks —
  this browser's phone, the link to owen-main, Quo sync. Hover or focus opens a card with
  Switch off/on and, during a call, Mute / Hang up. The browser polls
  `GET /api/connection-status` (`app/connection_status.py`), which asks owen-main's
  `GET /api/link-status` server-side with the `crm_link` key, 3s timeout, 30s cache, and
  answers 200 even when owen-main is down. Colours and sentences: `lib/connectionStatus.ts`.
- **Only one tab can be the phone.** The operator AOR holds one contact, so a second tab
  evicts the first. The tabs elect a holder over a BroadcastChannel and the others say so.
- Answering does **not** cancel the mobiles from here — OWEN's ring group already hangs
  up every losing leg before it bridges. Do not add a second mechanism.
- **No real call has ever been placed through it.** See the 2026-09-11 entry in
  `DECISIONS.md` and `.qa/state/softphone-done` for exactly what is unverified.
- **Dialling out** (2026-09-14): Conversations → the phone icon in the Team inbox header opens
  `components/CallNumberDialog.tsx`; every call button goes through `lib/callLauncher.ts`, which
  marks the outbound intent (`lib/outboundIntent.ts`) BEFORE the request and sends
  `ring_browser: true` only while the phone is Ready. `POST /api/calls/dial` is the server half.
  One in-call window for every browser call: `components/InCallWindow.tsx`. No hold/transfer —
  owen-main exposes nothing the CRM key can drive (DECISIONS.md lists what it would need).
  `uv run python -m tests.browser_dialer` (from `backend/`) drives it in headless Chromium with
  the SIP user faked (`frontend/e2e/`) and owen-main replaced; it is not collected by pytest.

## It is deployed

Live at **https://crm.dreamteamroofingfl.com** on the `owen-main` VPS, behind the shared
Traefik. Origin is the private `github.com/OwenUSA/ghl-clone`; the server pulls.

```bash
ssh owen-main
cd /opt/santiagoproperties/ghl-clone
./deploy.sh                     # fast-forward and rebuild
./deploy.sh --with-migrations   # ...and allow new Alembic revisions to apply
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f api
```

`deploy.sh` refuses a dirty tree or a non-fast-forward, and refuses to apply a migration
unless you ask. Migrations do **not** run on container start, so restarting is safe.

**Production is NOT empty any more** (corrected 2026-09-09; it previously said the database
stays empty until an export lands). It holds 12 contacts, 10 opportunities, 6 calls, 8
appointments and 1 pipeline. The 268 contacts you see locally are a different, synthetic
set. Anything that touches production must assume real records exist and must never modify
or delete a record it did not create itself — see the triage decisions at the end of
`DECISIONS.md`. `app.seed` appears in no container command — it calls `drop_all()`.

The host Postgres on that box is shared with the telephony project (`callmon`) and
craigslist. We have our own `dtr_ghl_clone` database on it, not a shared schema — see the
amendment in `DECISIONS.md`.

## Importing the Workiz data

The owner's real business data arrives through `backend/app/workiz_import.py`. The
exports live at `~/workiz/` (chmod 600) and hold **real customers' names, phones,
emails and home addresses** — do not copy them, do not paste rows anywhere, do not
commit them.

```bash
cd backend
uv run python -m app.workiz_import                 # DRY RUN. Writes nothing.
uv run python -m app.workiz_import --commit        # ...and this one writes.
uv run python -m app.workiz_import --json | jq     # the same numbers, for a machine
```

- **Dry run is the default.** It prints counts by pipeline and stage, contacts
  created vs updated vs merged, appointments, and every skipped row with its reason.
  The report deliberately carries **no customer data** — it is safe to paste into a
  ticket, and a test asserts that.
- **An import can never text a customer.** It does not import `app.automations` or
  `app.queue`, and it counts the `jobs` table before and after inside the
  transaction — one new row rolls the whole import back. Do not "simplify" either
  guard. **Every scheduled job gets one appointment, past or future** (2026-09-15, reversing
  "future only"): a re-import moves, retitles or cancels (status, never delete) only a visit
  still exactly as the importer left it — `workiz_appointment` on the card records that — and
  lists a visit a person changed as LEFT ALONE. Older visits with no record are adopted.
- **The `Tech` column** (2026-09-15) lands on the card as read-only `workiz_tech` and assigns
  the job's appointment, past or future (kept on the Workiz calendar), to the first technician that maps
  to an active user — `--tech-map techs.json` (`{"Workiz name": "email"}`), else an exact full
  name. Owners never change; a person's assignment is never overwritten (the card's
  `workiz_tech_assigned_user_id` says which ones are the importer's). See DECISIONS.md.
- **`workiz_id` is the idempotency key**, in the reserved `workiz_*` namespace
  alongside `owen_*`. It is read-only through the API in every direction: editing one
  by hand makes the next import create a second copy of that customer. Records made
  in the CRM by hand have no `workiz_id`, which is expected.
- **It has never been run against production.** See the 2026-09-11 entry in
  `DECISIONS.md` for what a human must check first.
- **A job whose customer is not in the clients file makes its own contact**
  (2026-09-14). It has no `workiz_id`; it carries `workiz_from_jobs` and a re-run finds
  it by Job #, then phone, then email. Opportunities the export no longer mentions are
  listed and left untouched. A new Workiz AHS job attaches to the ONE AHS email card
  (`created_by` "AHS email", `ahs_job_id` set, no `workiz_id`) for the same customer
  within 3 days — that is a contract with ahsmail. See `DECISIONS.md`.

## Schema changes go through Alembic

`create_all()` no longer owns the Postgres schema — `alembic upgrade head` does. SQLite
(tests, bootstrap) still self-creates via the `AUTO_CREATE_ALL` guard in `main.py`.

```bash
cd backend
uv run alembic revision --autogenerate -m "what changed"
uv run alembic check          # fails if models and database have drifted
uv run alembic upgrade head
```

Declare `server_default` on any new non-nullable column, or the `ALTER TABLE` fails
against existing rows.

## Tests

```bash
uv run pytest                        # backend + CLI, PENDING_COUNT tests
uv run ruff check .                  # must pass clean
uv run python capture/capture_ours.py contacts Contacts            # regenerate OUR side
uv run python capture/capture_ours.py conversations Conversations
uv run python capture/diff.py        # 37/37 landmark properties must still match
uv run python capture/diff_panel.py  # 35/35 for the contact panel
```

> **Regenerate `captures/ours/` before either diff, and pass the nav argument.**
> `captures/ours/` is gitignored, so a fresh checkout has none and both diffs print
> `missing capture(s)` — a stale tree is worse, because they compare old data and
> report a pass. And since the app now lands on **Dashboard** rather than Contacts,
> `capture_ours.py contacts` with no nav argument captures the Dashboard: every
> landmark still *matches*, but four are NOT FOUND and the score silently reads
> **24/37 with 0 differ**. Read the NOT FOUND block, not just the differ count.

**CI runs `ruff check`, `pytest`, the frontend typecheck/build and both Docker builds**
on every push to `main` and every PR (`.github/workflows/ci.yml`). Ruff is pinned exactly
and its rule set is declared in `pyproject.toml` — do not float the version, or the rule
set moves under the project and hundreds of findings appear overnight. Nothing under
`capture/` runs in CI: it needs a live GHL login, a hand-started Chrome, and
`references/`, which is gitignored.

> **`ui_check.py` WRITES to whatever database it is pointed at.** Two of its checks mutate
> real records (a contact rename and an opportunity stage move). Both now capture the
> original value and restore it in a `finally`, printing `!! RESTORE FAILED` loudly if the
> undo does not land — but that is damage control. Point `DATABASE_URL` at a disposable
> database before running it. An earlier version hardcoded its restore value and silently
> renamed real contacts to "Kimberly"; three corrupted records were recovered from their
> seed email addresses.

The scripts that drive a browser (`ui_check.py`, `audit_ours.py`, `capture_ours.py`,
`components.py`, `deep.py`, `shot_ours.py`, `shot_contact_panel.py`) now sign in first
via `capture/login.py`, which needs:

```bash
export GHL_APP_PASSWORD=...          # the app password for GHL_APP_EMAIL
```

`ui_check.py` additionally attaches to a Chrome you started yourself with
`--remote-debugging-port=9222` (see `capture/attach.py`) — it does not launch one.

Tests assert behaviour, not status codes: a filter must actually narrow the set, a 403
must not have mutated anything, a suppressed send must write no row. Please keep that
standard — a test that only checks a status code would have missed three of the bugs
found while building this.

## Things that have bitten us

- An ISO timestamp's `+00:00` decodes as a space if concatenated into a URL, and the
  date filter then silently fails. Always pass query params properly encoded.
- Reminder jobs dedupe on `dedupe_key`. Anything that reschedules must cancel the old
  jobs and use a key that includes the new time, or the customer gets no reminder.
  **Cancelling by status is not enough:** `enqueue()` refuses a key that exists
  whatever its status, so a retired job must also *release* its key
  (`dedupe_key = None`) — otherwise moving a booking away and back again hits the
  first key and the customer silently gets nothing. See the 2026-09-10 amendment in
  `DECISIONS.md`. `_drop_pending_reminders()` in `main.py` is the one place that does
  this; use it rather than writing the loop again.
- `Conversation`, `Opportunity` and `Appointment` are **not** cascade-deleted from
  `Contact`, and `conversations.contact_id` is NOT NULL. Deleting a contact has to be
  explicit about all three: conversations are deleted, opportunities and appointments are
  **detached** (`contact_id = None`), and the delete is refused with a 409 naming them
  until `force=true`. Missing the appointments is what returned a raw 500 on production —
  see the 2026-09-11 amendment in `DECISIONS.md`. `DELETE /api/appointments/{id}` is a
  cancel, not a delete: the row survives and the body says `deleted: false`.
- **The Day/Week grid lays overlapping visits side by side** (2026-09-15):
  `calendarGrid.layoutDay` decides lanes and `bucketByOverlap` puts a multi-day job on every day
  it touches — both executed under node in `test_calendar_layout.py`; the page must not
  re-derive either. It opens on 7 AM–7 PM (`hourPxFor`). `uv run python -m
  tests.browser_calendar_week` (from `backend/`) draws the measured Monday in headless Chromium.
- **Appointment times are picked in the ACCOUNT's timezone** (America/New_York), not the
  browser's — `frontend/src/lib/accountTime.ts`, executed under node in
  `test_account_time.py`. Never format or build a booking's time with
  `toLocaleString()` / `new Date(y, m, d, h)`: that is the laptop's zone. Incoming
  times are normalised to UTC before storage, because SQLite drops an offset.
- **Blocked off time** (`/api/blocked-times`) is its own table, sends nothing, and makes
  `POST`/`PATCH /api/appointments` answer **409** until `allow_blocked_time: true` is sent.
  A booking's `{{contact.name}}` title and "Calendar default" location are resolved on
  save and stored as text. See the 2026-09-13 Book appointment amendment in `DECISIONS.md`.
- `Opportunity.custom_fields.owen_call_id` is a live join key to the telephony project.
  Never delete opportunities as a side effect of tidying something else.

## "Only assigned data" and My Staff (2026-09-15)

A per-user switch (`users.only_assigned_data`, ON by default for a new TECH, never for an ADMIN)
limits a user to THEIR JOBS — deals they own, or with a non-cancelled visit on their calendar or
assigned to them — and to those jobs' contacts, threads (no number-only threads), calendar entries,
blocked time, tasks, notes and photos; Dashboard, Reporting and Forecast answer 403. **Every route
that reads a customer record goes through `app/assigned_access.py`** (by id: 404, never 403), and
`test_only_assigned_data.py` enumerates EVERY `/api` route — add one without recording how it
enforces this and that test fails. A restricted TECH may answer questions, move the stage, add notes
(opportunity notes only — thread notes stay STAFF) and tasks, and complete their own tasks, on their
own job. Settings → **My Staff** (ADMIN) manages users: deactivate never deletes, an admin-set
password forces a change at next sign-in (`auth.PASSWORD_CHANGE_PATHS`, server-enforced), a new
technician gets a calendar, and the last active admin cannot be removed. See DECISIONS.md.

## AI Agents, phase 1 — the foundation (2026-09-15)

`backend/app/ai/` (read `__init__.py` first) and the AI Agents page. Text/Chat agents, and since
2026-09-25 Voice agents (next section). **Every agent is created Off**; Suggest turns every write into a
pending suggestion staff approve (`POST /api/ai/suggestions/{id}/approve`, exactly once); Auto-pilot
(ADMIN, confirmed) executes. Try-it runs the draft with real reads and never writes.

- **Providers:** ONE interface (`providers.py`), official `anthropic` and `openai` SDKs (both on
  `httpx2`). Tests mock at the HTTP boundary with `providers.HTTP_CLIENT_FACTORY` — see
  `tests/ai_support.py`. Never send `temperature`/`top_p`/thinking budgets to Claude.
- **Keys:** Fernet under `AI_SECRETS_KEY` (unset = connections cannot be saved, with a sentence).
  Only "•••• last4" is ever returned; the key is never logged or put in a run's transcript.
- **Every write goes through the staff services** — `main.move_to_stage`, `main.book_appointment`,
  `main.edit_appointment`, `main.cancel_appointment_record`, `main.answer_questions`,
  `opportunity_workspace.add_note` / `add_task`, `automations.send_outbound` — as a DISPATCHER-level
  system principal with no user id. `actions.py` refuses anything outside the run's own contact /
  opportunity, and the records it writes carry `ai_agent_id` ("AI: <agent name>").
- **Triggers** (`triggers.py`) enqueue `ai_agent_run` jobs through `app.queue` from the contact branch
  of `POST /api/events`, `automations.on_opportunity_stage_changed` and the appointment services. A
  number no contact holds never triggers. A staff text or call (`_send_to_contact`, `_place_call`)
  puts sleeping agents to sleep on that customer and cancels their queued runs, RELEASING the job's
  dedupe key like `_drop_pending_reminders`. A hook never breaks the request that called it.
- **Access:** `/api/ai/*` is ADMIN + DISPATCHER and 403 for a TECH or any restricted user; logs,
  metrics, gaps, connections and settings are ADMIN. `test_ai_permissions.py` enumerates the router.
- The worker runs agent runs as ordinary jobs; a failed run is logged as `error` and never retried
  (a retry could text a customer twice). See the 2026-09-15 AI Agents amendment in `DECISIONS.md`.

## Voice agents are edited here and published to owen-main (2026-09-25)

A Voice agent is created and edited in AI Agents; owen-main runs the call. **`app/ai/voice.py` is
the ONE place** a CRM config meets owen-main's `agent_versions.config` (its docstring is the key
table). A voice agent may have only `transfer_call`, `end_call`, `capture_lead` — **never
`send_text`** (owen-main refuses `send_sms` on owen_voice) — and no triggers, schedule, AI
connection, knowledge bases or escalation users: the API refuses them (`voice.clean`), not just
the UI. **Knowledge over 6,000 characters refuses to publish**, with the number.

- **Publish never waits on owen-main.** It writes the version, an `ai_voice_pushes` row and one
  `ai_voice_push` job (`app/ai/push.py`); the worker sends `POST /api/crm-link/agent-versions`.
  Unreachable → retried by `app.queue` (10 attempts); a 4xx → refused, not retried. Until owen-main
  answers with the version it stored the agent reads **"Published · not yet live on the phone
  system"** — never "live". Retry: `POST /api/ai/agents/{id}/push` (ADMIN). A newer publish
  supersedes a push in flight and releases its dedupe key.
- **owen-main** (`app/integrations/crm/agent_versions.py`): finds the agent by NAME and never
  creates one (404), validates as activation does (422), and is idempotent on `crm_version` —
  pushing CRM version 7 twice makes one owen-main version.
- **Import the live agent once** (from `backend/`): `uv run python -m app.ai.import_voice_agent`
  (DRY RUN; `--commit` writes; `--agent NAME`). Creates the agent ANSWERING calls with mode
  Off (phase 2c) + version 1, recorded live only when it maps back to exactly the live config;
  never overwrites an existing CRM agent; never writes to owen-main; prints no persona or
  knowledge. See DECISIONS.md.

### Answering calls is its own switch (phase 2c, 2026-09-25)

A voice agent has TWO controls and they never stand for each other: **"Answering calls"**
(`ai_agents.answering_calls`, default false; does owen-main have this agent's version ACTIVE?)
and the mode, shown as **"What it may write here"** (Off / Suggest / Auto-pilot — CRM writes
only). `POST /api/ai/agents/{id}/answering` `{"on", "confirm"}` is ADMIN only and confirmed both
ways; a text agent refuses it (400). On → the published version is activated on owen-main (no
new version there); Off → owen-main's `{"agent_name", "deactivate": true}`, and a caller then
reaches the flow's fallback (voicemail) — not a failed call. Publish sends `activate =
answering_calls` and never deactivates by itself. All of it rides the ONE queued push in
`app/ai/push.py`. The chip is the server's label ("Answering calls", "Not answering calls",
"Answering calls — not with this version", "Published · not yet live on the phone system", …);
never "Answering" unless owen-main said THIS version is active. The import creates the live
agent **answering, mode Off**. Making Off block activation was tried and rejected — the import
would have read "off" about the live receptionist. See DECISIONS.md.

### Archiving a voice agent takes it off the phone (phase 3, 2026-09-25)

Deleting (archiving) a voice agent with "Answering calls" on switches it off and sends the SAME
queued DEACTIVATE as the switch (`push.archive`; `handle_job` still sends a deactivation for an
archived agent and nothing else). The archive always succeeds; until owen-main confirms, the
agent stays in the agents list marked **Archived** with an "Archived · …" chip, the reason and a
Retry (`POST /api/ai/agents/{id}/push`) — never a screen saying it stopped when it did not.
Nothing published → nothing can be sent, the switch is left on, and the row says so. owen-main
(same phase): an agent per CAMPAIGN (`campaigns.agent_id`, filling in after a node's id and
slot), and the CRM line's no-answer agent behind `CRM_LINK_AGENT_ANSWERS` (default false). See
DECISIONS.md.

## A live AI call: Listen / Take over (2026-09-23)

While an AI agent is on a call, `components/LiveAgentCall.tsx` (mounted once in `App.tsx`, left of
the bell) shows "AI is on a call with …" with **Listen** and **Take over** (confirmed first). It
polls `GET /api/live-calls` every 5s, and only for `canOpenAiAgents`. `app/live_calls.py` relays to
owen-main's `/api/crm-link/live-calls[/{linkedid}/listen|takeover]` through `crmlink`, ADMIN /
DISPATCHER only (`ai.api.VIEW`), always with the SIGNED-IN user's email — owen-main rings that
user's `PJSIP/operator-<slug>` browser line, and refuses an email not on its softphone roster.
Unconfigured link: `{"calls": []}`, no request. Never exercised on a real call. See DECISIONS.md.

## The voice agent knows who is calling (2026-09-24)

`POST /api/agent-context` (`app/agent_context.py`) — owen-main asks it as a voice agent answers,
with the feed's `events:write` token (`auth.EVENTS_WRITE_PATHS`; a TECH or a restricted owner is
refused). Body: `{"caller_number"}` only (any other key is 422). Last ten digits, exactly ONE
contact: none, a fragment, or a household of two is `{"known": false}` and nothing else. Known:
`contact{first_name,last_name}`, the most recently updated OPEN card's `{title,stage,pipeline}`,
the next visit not cancelled `{starts_at,title}`, `last_contact_at` (call/text/email/WhatsApp,
never a note, never an unsent text). **Never** money, notes of any kind, Checklist, email,
address. Pipelines hidden from the token's owner (`pipeline_access`) are hidden here too.
owen-main renders it (`integrations/crm/caller_brief.py`, `context_provider.kind: crm_link`).
See DECISIONS.md.

## Zuper: the source of truth for jobs — built, switched OFF (2026-09-16)

`backend/app/zuper/` (read `__init__.py` first). The CRM captures customers; Zuper owns jobs,
quotes, invoices, payments and reports. Every rule: the 2026-09-16 Zuper amendment in `DECISIONS.md`.

- **What reaches Zuper:** a Retail card only when an ADMIN / unrestricted DISPATCHER presses **Send
  to Zuper** (`POST /api/opportunities/{id}/zuper/send`; name, phone and the JOB's address first);
  an AHS email card automatically; the day-one load (`python -m app.zuper.load`, dry run; all AHS +
  Retail Scheduled / Inspection / Estimate / Estimate Sent open / Invoice open+won); new qualifying
  Workiz jobs before cutover (the sweep). Leads never. An AI agent only SUGGESTS a send.
- **A sent card is a mirror — `app/zuper/locks.py`.** Stage, visits, job address, value, title,
  status, pipeline, owner, and its customer's name / phone / email / address answer **409 "Change
  this in Zuper"** on every path. A new route that edits any of them must call the lock. Notes,
  Checklist answers and tasks still sync both ways. Agents turn change requests into urgent tasks.
- **No automations on Zuper jobs:** paid → Won and declined → task were dropped;
  `app/zuper/outcomes.py` is the switched-off hook.
- **Lead outcome** (`app/lead_outcomes.py`, CRM-only): required when a never-booked card is closed
  lost / abandoned; "Other" needs a note; modal, bulk, AI action, `GET /api/reports/lead-outcomes`.
- **Off three ways:** `ZUPER_SYNC_ENABLED` (default false — zero requests, nothing queued, the
  webhook 503), the Settings → Zuper switch (refused until the setup check passes and every
  confirm-by-hand item is ticked), and `ZUPER_API_KEY` / `ZUPER_API_KEY_FILE`.
- **`app/zuper/client.py` is the only code that talks to Zuper:** a denylist (texting, calling,
  every send, portal invites, notifications, any write to money documents) and an allowlist are
  checked before a request exists. **No test reaches Zuper:** `tests/zuper_guard.py`; mock with
  `tests/zuper_fake.py` (fixtures `fake`, `zworld`, `armed`, `loaded` in conftest).
- **CRM changes are queued by a flush listener** after the ROOT commit (a SAVEPOINT commit does
  not count). A session with `info["zuper_quiet"]` queues nothing (the sync, the Workiz importer);
  the 15-minute sweep finds those by content hash. `zuper_*` jobs run on the worker's Zuper thread,
  never the main drainer.
- **Deletes are mirrored both ways** for sent jobs / customers, snapshotted first, restorable from
  Settings → Zuper; a contact with unsent cards or conversations is never deleted by Zuper.
- **Workiz cutover date** (Settings → Zuper): before it the importer's job changes are pushed; on
  and after it the importer refuses (exit 10).

**The CRM imitates Zuper (2026-09-23).** The direction is reversed: Zuper's boards are the
truth and the CRM copies them — same AHS / Retail pipelines, same stage names in Zuper's order,
same job information, every change replicated by webhook (instant) and the 15-minute sweep
(backstop). `ZUPER_PULL_ONLY=true` makes it one way: the engine drops what it would have
written, the listener queues no push, the sweep runs its Zuper half only, Send to Zuper answers
409, and `client.request` refuses every non-GET before a connection exists — except registering
the webhook from the operator's command. `python -m app.zuper.mirror` (dry run; `--commit`)
lines the two sides up: `boards` (stages become the category's statuses; a renamed stage keeps
its deals — `mirror.ALIASES`), `links` (job ↔ card by Workiz job number, customer ↔ contact),
`backfill` (pull every job). See the 2026-09-23 amendment in `DECISIONS.md`.

```bash
uv run python -m app.zuper.mirror           # DRY RUN; --commit; --phase boards|links|backfill
uv run python -m app.zuper.setup            # DRY RUN checks; --commit makes the AHS / Retail categories
uv run python -m app.zuper.load             # DRY RUN day-one load; --commit sends, resumable
uv run python -m app.zuper.webhook          # DRY RUN lists Zuper's webhooks; --commit registers ours
```
The load STOPS on the first record of a kind Zuper refuses (the bodies are unverified), with Zuper's
message; later refusals are counted with their reason. The webhook command makes one webhook per
(module, event) in `webhook.EVENTS` (UNVERIFIED names — fix that one constant on a refusal), with
the `X-Webhook-Token` header (`TOKEN_IN = "url"` puts `?token=` on the URL instead); never prints
the token. Zuper did not list webhooks on the first live check, so every webhook the CRM registers
is also a `zuper_mappings` row (crm_type "webhook", "creating" before the POST): no duplicates even
unlisted, and the setup check's webhook item passes on those rows. Category / status create bodies
are UNVERIFIED: `zapi.create_first_accepted` tries each candidate shape only while Zuper REFUSES
(live: the wrapped category body got "Category Name Missing"), and the setup report names the one
accepted.
