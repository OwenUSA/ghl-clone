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
- **No message actually leaves the building.** `LoggingTransport` is the only
  `MessageTransport` implementation, so outbound sends are recorded with
  `delivery_status = LOGGED_ONLY` and nothing is transmitted. When a real transport is
  wired in, every `ghl msg send` becomes a real text — the `--yes` guard exists for
  that day.
- **Never run `python -m app.seed` against the working database.** It calls
  `drop_all()`. It is in the `deny` list in `.claude/settings.json`.

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
`/api/auth/login`, `/api/auth/refresh` and `/api/auth/logout`.

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
message, but cannot edit records. Machine tokens can be scoped (`events:write` for the
telephony feed) — a scoped token can never exceed its owner's role.

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
uv run pytest                        # backend + CLI, 262 tests
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
- `Conversation` and `Opportunity` are **not** cascade-deleted from `Contact`, and
  `conversations.contact_id` is NOT NULL. Deleting a contact has to be explicit.
- `Opportunity.custom_fields.owen_call_id` is a live join key to the telephony project.
  Never delete opportunities as a side effect of tidying something else.
