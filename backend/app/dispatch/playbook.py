"""How Dream Team Roofing runs its jobs in Zuper — the context the Dispatch AI reads before it
explains an item or answers a question (phase 2, 2026-09-30).

Written from the boards as they stood on 2026-09-30 (docs/ZUPER-OPERATIONS.md and the owner's
answers in the grilling session). When the team changes a board, change it here too: the AI
never invents a stage, and `stage_names()` is checked against what the reader saw.
"""
from __future__ import annotations

from . import config as c

BOARDS: dict[str, list[tuple[str, str]]] = {
    c.INSPECTION_BOARD: [
        ("Work Order Received", "A new AHS work order. Nobody has spoken to the customer yet."),
        ("Not Answering -Keep Call!", "We called and they did not answer. Keep calling."),
        ("Welcome Call!", ("Make the welcome call and book the inspection. Moving INTO this "
                           "stage opens the 18-question intake checklist; 'Intake complete?' "
                          "must be Yes.")),
        ("Inspection: Day-Before Call", "Call the day before the inspection to confirm."),
        ("Inspection: Same-Day Confirm", "Confirm on the morning of the inspection."),
        ("Antonio On the Way", "The technician is driving to the job (office sets it)."),
        ("Scheduled", "The inspection is booked: the job must have a date and technician."),
        ("Inspection In Progress", "The technician is on site."),
        ("Inspection Completed", ("The inspection is done. Moving INTO it opens the 12-question "
                                  "inspection checklist (9 written answers + 3 photo sets).")),
        ("Submit To AHS For Approval", "The report goes to AHS for authorization."),
        ("Awaiting AHS Decision", ("Waiting on AHS. Nothing to do with the customer but keep "
                                   "them informed.")),
        ("AHS Approved", ("AHS authorized the repair. Book the repair. The KPI questions (AHS "
                          "authorized $, customer chose, customer pays $) are answered here. "
                         "The job then moves to the AHS - Repair & Review board by hand "
                         "(edit the job's Category).")),
        ("Proposal Made", "A proposal was sent."),
        ("Waiting for Customer", "Waiting on the customer's answer."),
        ("Reschedule Required", "The visit did not happen as booked; book a new date."),
        ("Estimate Declined", "Closed: the customer declined."),
        ("Cancelled", "Closed."),
    ],
    c.REPAIR_BOARD: [
        ("Not Answering", "We cannot reach the customer to book the repair."),
        ("Repair Scheduling Call", "Call to book the repair."),
        ("Day-Before Call", "The repair is booked; call the day before to confirm."),
        ("On the Way to Repair", "The technician is driving to the job."),
        ("Repair In Process", "The technician is working. 'Photos - before' checklist."),
        ("Repair Complete", "The repair is done. 'Photos - during / after' checklist."),
        ("Call Satisfaction Check", "Call the customer: happy with the repair?"),
        ("Invoice Submitted to AHS", "The invoice went to AHS (customer paid $ is asked here)."),
        ("Awaiting AHS Payment", "Waiting for AHS to pay."),
        ("Paid", "Closed: paid."),
        ("Review Requested", "We asked the customer for a Google review."),
        ("Review Received", "Closed: review in."),
        ("Call Back", "The customer needs a return visit (re-work / warranty)."),
        ("Waiting for Customer", "Waiting on the customer."),
        ("Reschedule Required", "Book a new date."),
        ("Cancelled", "Closed."),
    ],
    c.RETAIL_BOARD: [
        ("New Lead", "A new retail lead. Call and book the inspection / estimate."),
        ("Contact Attempted", "We tried to reach them. Intake checklist (15 questions)."),
        ("Inspection / Estimate", "The inspection is booked or done; 12-question checklist."),
        ("Proposal Made", "The proposal (Good / Better / Best) is ready or sent."),
        ("Estimate Sent", "The estimate went to the customer."),
        ("Follow Up", "Follow up on the estimate."),
        ("Scheduled", "The customer said yes and the work is booked (counts as SOLD)."),
        ("Repair In Process", "Work in progress."),
        ("Repair Complete", "Work done."),
        ("Invoice", "Invoice the customer."),
        ("Collect Balance", "Collect what is owed."),
        ("Paid", "Closed: paid."),
        ("Waiting for Customer", "Waiting on the customer."),
        ("Reschedule Required", "Book a new date."),
        ("Estimate Declined", "Closed: declined."),
        ("Cancelled", "Closed."),
    ],
}

HOUSE_RULES = """\
- Zuper is where the team works. This CRM only reads it. YOU NEVER CHANGE ZUPER: you tell a person
  exactly what to change, where, and why.
- To move a job: open it in Zuper and change its status at the top of the job page. Moving into a
  stage that has a checklist opens it; answer every question (a "No" on a required one blocks
  the move).
- To move a job to the other AHS board: edit the job, change its Category to the other board,
  then pick the stage.
- To book a visit: set the job's scheduled date and time and assign the technician (the
  "Technician" dropdown decides whose daily route it goes on). Antonio does most repairs, Owen
  most inspections.
- Never delete a job and never touch its pictures. Never move a job backwards because of an old
  piece of information.
- To decide WHEN to book several jobs, use the plan_schedule tool: it groups close jobs on
  the same day per technician (4-5 a day, Monday to Saturday) around what is already booked,
  using how long visits were really booked before. Present it day by day; it is a draft the
  office confirms with each customer.
- Nothing is ever sent to a customer automatically. Calls and texts are made by people.
- AHS jobs: AHS - Inspection ends at AHS Approved; the repair, invoice and review happen on
  AHS - Repair & Review. Retail jobs stay on Retail from lead to paid.
"""


def board_text() -> str:
    out = []
    for board, stages in BOARDS.items():
        out.append("Board \"%s\", in order:" % board)
        out += ["  - %s: %s" % (name, what) for name, what in stages]
    return "\n".join(out)


def stage_names() -> set[str]:
    return {name for stages in BOARDS.values() for name, _ in stages}
