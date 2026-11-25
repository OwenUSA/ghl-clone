# Zuper backups — where they are

Every copy we have ever taken of Zuper lives in ONE folder on the **owen-main** server:

```
/opt/santiagoproperties/zuper-backup/        root-only (700), real customer data
  README.md              the guide: what each file is, how to find a job, how to re-run
  CHECKSUMS.sha256       one checksum per file — `sha256sum -c CHECKSUMS.sha256` proves nothing changed
  full/2026-10-27/       THE COMPLETE COPY OF ZUPER (use this first)
  full/2026-09-28-ahs/   an older copy of the AHS jobs only
  before-changes/YYYY-MM-DD/   a snapshot taken right before each change made to Zuper, by day
  tool/                  the script that made the full copy (no API key)
```

It sits next to the CompanyCam photo backup (`/opt/santiagoproperties/companycam-backup/`).
Moved there from the dispatch server on 2026-11-06 and verified file by file.

**Real customers' names, phones, addresses, photos, call recordings and texts are in it.** Never copy
it to a laptop, a ticket, the repo or any other server. Read it on owen-main:

```bash
ssh owen-main
cd /opt/santiagoproperties/zuper-backup
less README.md
```

## The full copy — `full/2026-10-27/`

Taken 2026-10-27, 00:07–00:55 New York time, by reading Zuper only (nothing was written to Zuper).
Every count equals the total Zuper itself reported; 0 errors:

| What | Count |
|---|---|
| Jobs — full record: line items, custom fields, checklist answers per column, status history, schedule, assignees | 480 |
| Job notes | 193 |
| Customers / properties | 894 / 422 |
| Quotes / invoices / payments / commissions | 116 / 88 / 115 / 60 |
| Workflows (with full definitions) | 48 |
| Products and services catalog | 1,724 |
| Checklist settings (questions per column) | 238 columns |
| Zuper Connect calls / text conversations / text messages | 694 / 143 / 1,474 |
| Files: job and customer pictures, documents, call recordings, pictures sent by text | 3,069 (7.8 GB) |

Not in it: saved board views and timesheets (Zuper refused the request), appointments (module off on
this account), the webhooks list.

`README.md` in the folder explains the file layout (which JSON holds what, how to find a job by its
number, how a picture maps back to its job).

## Snapshots before changes — `before-changes/`

Every time something in Zuper was changed on purpose (a column moved, a checklist question added, a
workflow edited, a job reset, a role's permissions changed…), the record as it was just before was
saved first. They are filed by day, 2026-09-17 → 2026-11-03, and the file name says what was about to
change, e.g. `ahs_repair_before_review_move_…`, `job701_before_reset_…`,
`access_role_office_manager_permissions_before_…`, `wf_<id>_before_golive_…`. The list per day is at
the end of the folder's `README.md`.

**New snapshots still start on dispatch.** The scripts that change Zuper write their "before" file
to `/root/backups/` on the dispatch server, as they always have. Move them here now and then, into
`before-changes/<date>/`, adding their lines to `CHECKSUMS.sha256`. (On 2026-11-06 every Zuper file
there was moved; what is left in that folder is not Zuper data.)

## Restoring

There is **no automatic restore** — Zuper has no import. The backup shows exactly how a job, customer
or setting looked, so it can be put back by hand in Zuper, or by a reviewed script one record at a
time (and only with the owner's OK — see the read-only rule in `DECISIONS.md`).

## Taking a new full copy

The script and the Zuper API key stay on the **dispatch** server (root only):

```bash
ssh dispatch
cd /opt/ops/zuper/zbackup && python3 full_backup.py /root/zuper-full-$(date +%Y%m%d)
```

About 50 minutes and ~8 GB. It can only read: `zget.py` refuses any request except GET and Zuper
Connect's three search requests (call history, text inbox, one conversation's messages). Then copy it
to owen-main as `full/YYYY-MM-DD/`, add its lines to `CHECKSUMS.sha256`, and update this page.

Taking one before letting anything new write to Zuper (a new integration, a test AI agent) is the
point of having it: a fresh copy is the "before" to compare with.
