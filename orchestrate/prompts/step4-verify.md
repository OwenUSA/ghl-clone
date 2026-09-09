The fix branch has been deployed to production by a human. Confirm each fix actually holds
there, and write `{{OUT}}`.

## Target and credentials

- Base URL: the `{{BASE_VAR}}` environment variable. **This is production.**
- Sign in as ADMIN: email in `{{EMAIL_VAR}}`, password in `{{PASSWORD_VAR}}`. Read from the
  environment; never print or persist them.
- Reuse `capture/login.py` (set `GHL_APP_URL`, `GHL_APP_EMAIL`, `GHL_APP_PASSWORD`).

## What to do

Read `{{LOG}}`. For every entry with status `fixed` or `needs-prod-verify`, reproduce the
ORIGINAL repro steps against production and record whether the reported behaviour is gone.

Same production rules as the test run:

- Create and edit only what you must, record it, restore or delete it before finishing.
- **Never delete an opportunity** (`custom_fields.owen_call_id` joins to the telephony
  project).
- Never touch a record you did not create. No direct database access. No `ssh`.
- A cleanup that fails is reported at the top of `{{OUT}}` as `critical`, never swallowed.
- Empty states are expected — the database has no real data. `LoggingTransport` means sent
  messages are logged and never transmitted; that is correct.

Do not change any application code in this step. If a fix did not hold, report it; the next
fix round is a separate step with a human gate in front of it.

## Output

Write `{{OUT}}`, one entry per verified finding:

```
### F<id> — <title>

- verdict: holds | still-broken | could-not-verify
- steps: <what you actually did>
- observed: <what happened on production>
- evidence: <console error, request + status, screenshot path under orchestrate/logs/>
```

Then a summary: how many hold, how many are still broken, how many you could not verify and
why. `could-not-verify` is a legitimate and useful answer — a `holds` you did not actually
observe is not.
