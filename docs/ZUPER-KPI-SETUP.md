# Zuper setup for the KPI reports — steps for a person to click (2026-09-30)

These steps only **add** questions to Zuper. They change no job, move no job and touch no photo.
A question pops up when someone moves a job **into** that column from now on; jobs already past
it are not affected. A required question blocks the move until it is answered — that is the point.

**Type the labels exactly as written.** The nightly report finds the answers by their label; a
different spelling is a question the report cannot see.

## How to add questions to a column

1. Zuper → **Settings** (gear) → **Modules → Jobs** → **Job Category Hub**.
2. Click the board (e.g. **AHS - Inspection**). The **Job Status** tab lists its columns.
3. On the column's row, in the **Checklist** column, click **+ Create**. A form builder opens,
   titled "Job Checklist for <column> (<board>)".
4. Drag a field type from the right-hand panel onto the form, then set its label, options and
   **Required**. Repeat for each question. Save.

The Zuper Connect dialer floats over the right half of the screen and can cover the field panel —
minimise it (the "–" on its header) first.

For money questions: use a **Number** field if the panel offers one; otherwise **Single Line
Text** and type digits only (e.g. `1100`, no `$`).

## A. AHS - Inspection → column **AHS Approved**

| Label (exact) | Field type | Options | Required |
|---|---|---|---|
| `AHS authorized ($)` | Number / Single Line Text | — | Yes |

## B. AHS - Repair & Review → column **Invoice Submitted to AHS**

| Label (exact) | Field type | Options | Required |
|---|---|---|---|
| `Customer chose` | Single Selection | AHS only · Good · Better · Best · Custom | Yes |
| `Customer paid ($)` | Number / Single Line Text | — (0 when AHS only) | Yes |

## C. Retail → column **Scheduled**

| Label (exact) | Field type | Options | Required |
|---|---|---|---|
| `Option chosen` | Single Selection | Good · Better · Best · Custom | Yes |
| `Sold price ($)` | Number / Single Line Text | — | Yes |

## D. Column **Cancelled** — on all three boards (AHS - Inspection, AHS - Repair & Review, Retail)

| Label (exact) | Field type | Options | Required |
|---|---|---|---|
| `Cancel reason` | Single Selection | Price · Went with competitor · No longer needed · Timing · AHS denied · Other | Yes |
| `Cancel note` | Multi Line Text | — | No (write one when the reason is Other) |

## E. Re-work / warranty jobs — one job field, and a habit

1. Settings → Modules → Jobs → **Job Custom Fields** → add a field:
   label `Original job #`, **Single Line Text**, shown on AHS - Inspection, AHS - Repair & Review
   and Retail. Mark it required if Zuper offers that.
2. **The habit:** when a customer calls back because a repair failed, create a **new job** with
   Job Type **Callback/Warranty** and write the first job's number in `Original job #`. Do not
   reopen or move the original job.

## F. Every Zuper proposal that is sent stays as it is

When a Good / Better / Best proposal is sent from Zuper and the customer signs it online, Zuper
records the option. The report reads both that and the questions above and compares them.

## After you finish

Tell Claude which sections are done. It will read the settings back (read only) and confirm each
label and option matches before the reports rely on them.
