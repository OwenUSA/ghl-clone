You are test worker {{SLICE_ID}} of 3, running in parallel with two others. Test **only your
slice** against the live production deployment, and write findings to `{{OUT}}`.

## Your slice

**{{SLICE_NAME}}** — {{SLICE_SURFACES}}

Two other workers are covering the other surfaces right now. Do not test outside your
slice: you will duplicate their work and collide with their data.

## Target and credentials

- Base URL: the `GHL_QA_BASE_URL` environment variable. **This is production.**
- Sign in as the **{{ROLE}}** account: email in `{{EMAIL_VAR}}`, password in
  `{{PASSWORD_VAR}}`. Read them from the environment. Never print them, never write them to
  a file, never put them in a script you save.
- Persist your Playwright session to `{{STATE_FILE}}` and nowhere else. The other two
  workers have their own files; sharing one would make all three sign each other out.
- `capture/login.py` already knows how to sign into this app (set `GHL_APP_URL`,
  `GHL_APP_EMAIL`, `GHL_APP_PASSWORD`). Reuse it rather than writing a fourth login flow.
  There is deliberately no auth-bypass switch in the backend.

Setup: `uv sync --all-groups` then `uv run playwright install chromium` if needed. Drive it
with `uv run python`.

## Production rules — these are not negotiable

- **You may create and edit records. You must clean up.** Keep a list of every id you
  create or field you change, and restore or delete it before you finish.
- **Never delete an opportunity.** `Opportunity.custom_fields.owen_call_id` is a live join
  key to a separate telephony project. Deleting one damages another system.
- **Never touch a record you did not create.** Do not edit, rename or move anything that
  was already there.
- **Never revoke, delete or modify an API token you did not mint yourself, and never
  target a token by a guessed or enumerated id.** This is not hypothetical: a probe named
  `DELETE token 1 (not mine)` assumed the call would be refused, an ADMIN was allowed to
  make it, and token id 1 -- the live `callmon` credential belonging to the telephony
  project -- was permanently destroyed. Revocation is irreversible; the backend stores
  only a sha256. To test token authorisation, mint your own token, act on that one, and
  revoke it before you exit.
- **A destructive call you expect to be REFUSED may be accepted.** Never probe one against
  a real record to find out. Create the target first, or do not run the probe.
- **No direct database access.** No psql, no `ssh`. Everything through the UI or the API.
- **A cleanup that fails is a FINDING**, reported at the top of your file with severity
  `critical`. Do not silently swallow it. An earlier harness in this repo silently renamed
  three real contacts because a restore failed quietly.
- Do not run `python -m app.seed` (it calls `drop_all()`), do not deploy, do not `ssh`, do
  not push, do not commit.

## What is NOT a bug

The production database is **empty of real data**. Empty lists, empty boards, empty
reporting charts and "no results" states are **expected**. Do not report them as bugs.

`/api/health` reports `LoggingTransport`: outbound messages are recorded with
`delivery_status = LOGGED_ONLY` and nothing is transmitted. A message that does not arrive
on a real phone is correct behaviour, not a bug.

You are one of three concurrent sessions. If you are signed out unexpectedly, suspect
session collision before you suspect a bug.

## Findings format

Write `{{OUT}}` as a list of entries, each exactly:

```
### F{{SLICE_ID}}-<n> — <one-line title>

- classification: bug | environment | uncertain
- severity: critical | high | medium | low
- surface: <route or view>
- repro:
  1. <step>
  2. <step>
- expected: <what should happen, per the inventory>
- actual: <what happened>
- evidence: <console error, failed request + status, screenshot path under orchestrate/logs/>
- reproduces-locally: yes | no | untested
- status: open
```

`classification` decides whether a human ever looks at it:

- **bug** — a real defect in the application.
- **environment** — an artefact of how this run is set up: empty data, concurrent-session
  sign-out, your own cleanup failure, a Playwright timing issue.
- **uncertain** — you could not tell. Say why in `actual`.

Be honest in `classification`. A false `bug` causes an unattended worker to rewrite working
code. If you are not sure, `uncertain` is the correct answer, not `bug`.

`evidence` must be real. Do not describe a console error you did not capture.

## Order of work

1. Read `orchestrate/findings/inventory.md` and extract the elements belonging to your
   slice. That list is your test plan.
2. Sign in. Confirm you are the {{ROLE}} role.
3. Work the plan: render, then interact, then adversarial input, then role boundaries.
4. Clean up. Verify each cleanup actually landed.
5. Write `{{OUT}}`. Put cleanup failures first.

Report what happened, including tests you could not run and elements you did not reach. An
honest gap is useful; a silent one is not.
