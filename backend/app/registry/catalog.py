"""What every registry entry IS — its scope of work. PURE: no database, no I/O.

A person decides only four things about an entry (agent_registry) and the level of each
EDITABLE action (agent_permissions). Everything else — purpose, what it covers, its channels,
where it runs, its never-list — is written here, reviewed like code, and changed in a commit.

The ten roles are the boss's list (DECISIONS.md, 2026-10-21). An entry's role is a default a
person may change; an AI Agents row (`ai_agent:<id>`) is listed by the service, not here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

NEVER, SHADOW, APPROVE, AUTO = "never", "shadow", "approve", "auto"
LEVELS = (NEVER, SHADOW, APPROVE, AUTO)
LEVEL_LABEL = {NEVER: "Never", SHADOW: "Shadow", APPROVE: "Approve each", AUTO: "Automatic"}
LEVEL_RANK = {lv: i for i, lv in enumerate(LEVELS)}

ROLES: dict[int, str] = {
    1: "Phone answering & speed to lead",
    2: "Customer service & follow-up",
    3: "Scheduling & dispatch",
    4: "Inspection documentation",
    5: "AHS submission & authorization",
    6: "Post-inspection explanation & sales",
    7: "Production & field technician assistant",
    8: "Payments & collections",
    9: "Quality control, callbacks & reviews",
    10: "Management & daily reporting",
}

# What an entry can do. "customer" marks the ones a customer sees or hears.
ACTIONS: dict[str, dict] = {
    "answer_calls": {"label": "Answer phone calls", "customer": True},
    "place_calls": {"label": "Call customers", "customer": True},
    "send_text": {"label": "Text customers", "customer": True},
    "zuper_note": {"label": "Add a private note on a Zuper job", "customer": False},
    "zuper_create": {"label": "Create a customer and job in Zuper", "customer": False},
    "zuper_lines": {"label": "Set a job's line items in Zuper", "customer": False},
    "zuper_change": {"label": "Change a job in Zuper (visit, column, field, address)",
                     "customer": False},
    "zuper_tasks": {"label": "Create and update tasks in Zuper", "customer": False},
    "zuper_files": {"label": "Attach files to proposals in Zuper", "customer": False},
    "zuper_routes": {"label": "Build technician routes in Zuper", "customer": False},
    "crm_write": {"label": "Change CRM records", "customer": False},
    "suggest": {"label": "Suggest changes to the office", "customer": False},
    "alert_staff": {"label": "Ring the bell for the office", "customer": False},
    "report": {"label": "Write reports", "customer": False},
    "text_staff": {"label": "Text the technician", "customer": False},
    "call_staff": {"label": "Call the technician", "customer": False},
    "ai_read": {"label": "Read notes and replies with AI", "customer": False},
    "ahs_portal": {"label": "Read and submit in the AHS portal (Frontdoor)", "customer": False},
    "ai_vision": {"label": "Look at the pictures with AI and write the draft", "customer": False},
    "send_customer": {"label": "Send the report to the customer", "customer": True},
    "ai_suggest": {"label": "Suggest tools & materials with AI", "customer": False},
    "ai_chat": {"label": "Answer questions in a chat with AI", "customer": False},
}


@dataclass(frozen=True)
class Action:
    action: str
    default: str
    # The levels a person may choose. Empty = not editable here: the level is a fact about
    # where it runs (a Zuper workflow, an owen-main container) or is set by its own switch.
    choices: tuple[str, ...] = ()
    note: str | None = None


@dataclass(frozen=True)
class Entry:
    key: str
    name: str
    kind: str                       # agent | automation
    role: int
    purpose: str
    covers: str
    channels: tuple[str, ...]
    runs_on: str                    # where its code runs
    provider: str | None
    hours: str
    actions: tuple[Action, ...]
    never: tuple[str, ...] = ()
    owner_hint: str | None = None
    tags: tuple[str, ...] = field(default=())
    # Not built yet: shown Off with this sentence, every action Never and not switchable.
    planned: str | None = None

    def action(self, name: str) -> Action | None:
        return next((a for a in self.actions if a.action == name), None)


OUTSIDE = "Runs outside the CRM. Its state is not visible here."

ENTRIES: tuple[Entry, ...] = (
    # ------------------------------------------------------------------ agents (AI)
    Entry(
        key="autoreply", name="Auto-reply", kind="agent", role=1,
        purpose="Answers a text or missed call to the office line (954) 420-7373 that nobody "
                "answered in 5 minutes: one polite text per unanswered stretch.",
        covers="Customers who text or call the Zuper line. Test customers only until an admin "
               "switches it to everyone.",
        channels=("Zuper text from (954) 420-7373",),
        runs_on="CRM worker", provider="DeepSeek",
        hours="Around the clock; outside Mon-Sat 8-6 it says the office is closed.",
        actions=(
            Action("send_text", AUTO, (NEVER, SHADOW, AUTO),
                   "Owner lift 2026-10-19. Shadow writes the reply and records it without "
                   "sending; there is no approve step for it yet."),
            Action("zuper_note", AUTO, (), "Goes with every text it sends."),
        ),
        never=("Prices or money", "Saying a claim is approved", "Promising a date",
               "AHS claim details", "Internal notes"),
        owner_hint="Office",
    ),
    Entry(
        key="dispatch_assistant", name="Dispatch assistant", kind="agent", role=3,
        purpose="Answers the office's questions about jobs, calls and the schedule, explains "
                "Dispatch items, offers booking slots and suggests Zuper changes.",
        covers="Staff only: admins and office dispatchers.",
        channels=("Dispatch chat (staff)",),
        runs_on="CRM", provider="DeepSeek", hours="When staff ask",
        actions=(
            Action("suggest", AUTO, (), "Suggestions are only shown to staff."),
            Action("zuper_change", APPROVE, (NEVER, APPROVE),
                   "A person presses Approve for each change, and only while the server gate "
                   "and the Dispatch → Settings switches are on."),
        ),
        never=("Talk to customers", "Close or cancel a job", "Change assignees",
               "Delete anything", "Touch job photos"),
        owner_hint="Luis",
    ),
    Entry(
        key="availability_reader", name="Availability reader", kind="agent", role=3,
        purpose="Reads customers' calls and texts and saves when they can and cannot be "
                "visited, for booking.",
        covers="Jobs waiting for a visit.", channels=("Internal",),
        runs_on="CRM worker", provider="DeepSeek",
        hours="With the Dispatch assistant's switch",
        actions=(Action("crm_write", AUTO, (), "Saves availability; the office can correct it."),),
        never=("Talk to customers",),
    ),
    Entry(
        key="call_followup", name="Call follow-up", kind="agent", role=2,
        purpose="When the team calls a customer and nobody answers, it texts them a couple of "
                "minutes later, and if they still have not replied or called back, Anisa calls "
                "them.",
        covers="Every unanswered team call (Quo, the CRM line, Zuper Connect). Test customers "
               "only until an admin switches test-only off in its settings.",
        channels=("Zuper text from (954) 420-7373", "Anisa's call from the CRM line"),
        runs_on="CRM worker -> owen-main (Retell)", provider="Retell (Anisa)",
        hours="Texts any time after the team's call; calls 8 AM-8 PM Mon-Sat, at most 2 a day "
              "per number",
        actions=(
            Action("send_text", SHADOW, (NEVER, SHADOW, AUTO),
                   "The text after an unanswered team call."),
            Action("place_calls", SHADOW, (NEVER, SHADOW, AUTO),
                   "Anisa's call when the text got no answer. Needs AGENT_OUTBOUND_ENABLED on "
                   "owen-main to really dial."),
            Action("zuper_note", AUTO, (), "Goes with every text and call it makes."),
        ),
        never=("Prices or money", "Saying a claim is approved", "Promising a date",
               "Calling outside 8 AM-8 PM Mon-Sat", "More than 2 calls a day to one number"),
        owner_hint="Office", tags=("settings",),
    ),
    Entry(
        key="inspection_docs", name="Inspection documentation", kind="agent", role=4,
        purpose="After a technician finishes an inspection or repair, checks the job has where "
                "the leak is, tools & materials, time to complete, photos and a video - reading "
                "fields, checklist answers and notes - and flags what is missing.",
        covers="Finished visits on AHS - Inspection, Retail and AHS - Repair & Review. Test "
               "jobs only until test-only is switched off in its settings.",
        channels=("Dispatch item + bell", "Text to the technician from the CRM line",
                  "Technician call agent"),
        runs_on="CRM worker (reads Zuper only)", provider="DeepSeek (notes), Retell (calls)",
        hours="Checks 30 minutes after a visit; calls 8 AM-8 PM Mon-Sat",
        actions=(
            Action("alert_staff", AUTO, (), "A Dispatch item and the bell for the office."),
            Action("ai_read", AUTO, (NEVER, AUTO),
                   "Looks in notes and replies for details the fields do not hold. Every value "
                   "must quote the words it came from."),
            Action("text_staff", SHADOW, (NEVER, SHADOW, AUTO),
                   "Texts the technician what is missing."),
            Action("call_staff", NEVER, (NEVER, SHADOW, AUTO),
                   "The technician call agent asks for what is still missing after the wait. "
                   "Needs that agent on owen-main and AGENT_OUTBOUND_ENABLED."),
        ),
        never=("Write anything in Zuper", "Touch job photos", "Text or call customers"),
        owner_hint="Luis", tags=("settings",),
    ),
    Entry(
        key="ahs_submission", name="AHS Submission & Authorization", kind="agent", role=5,
        purpose="Prepare the inspection package for the office, track the AHS authorization, "
                "missing information, decisions and deadlines, record the approved scope, "
                "covered amounts and the customer's share, and alert Marianne and Luis when a "
                "claim stalls.",
        covers="AHS jobs.", channels=("AHS portal (Frontdoor)", "Dispatch item + bell"),
        runs_on="Not built", provider=None, hours="Off",
        actions=(
            Action("ahs_portal", NEVER, (), "Not built."),
            Action("alert_staff", NEVER, (), "Not built."),
            Action("report", NEVER, (), "Not built."),
        ),
        never=("Submit anything to AHS without a person", "Change a claim"),
        owner_hint="Marianne",
        planned="Off - not built. Waiting to confirm access to AHS's contractor portal, "
                "Frontdoor: an API key if AHS offers one; if not, a logged-in browser session "
                "(Playwright) that only reads. Until then AHS approvals arrive by email "
                "(AHS approvals into Dispatch).",
    ),
    Entry(
        key="inspection_report", name="Post-inspection report", kind="agent", role=6,
        purpose="When an inspection's pictures have stopped arriving for 30 minutes, builds a "
                "draft report for the customer - the pictures that best show the problem, with "
                "captions, and the findings in plain words - and leaves it for the office to "
                "review and approve.",
        covers="Inspections on AHS - Inspection and Retail. Test jobs only until test-only is "
               "switched off in its settings.",
        channels=("Dispatch -> Reports (draft)", "Dispatch item + bell"),
        runs_on="CRM worker (reads Zuper only)", provider="OpenAI or Anthropic (vision)",
        hours="Around the clock",
        actions=(
            Action("ai_vision", AUTO, (NEVER, AUTO),
                   "Picks the pictures and writes the draft. Needs a vision connection in its "
                   "settings."),
            Action("crm_write", AUTO, (), "The draft, kept in the CRM."),
            Action("alert_staff", AUTO, (), "A Dispatch item and the bell when a draft is ready."),
            Action("send_customer", NEVER, (), "Not built yet - a person reviews every report."),
        ),
        never=("Send anything to the customer", "Mention prices or AHS approval",
               "Promise a date", "Write anything in Zuper"),
        owner_hint="Luis", tags=("settings",),
    ),
    Entry(
        key="tech_briefing", name="Field technician briefing", kind="agent", role=7,
        purpose="At 6 PM the day before, texts each technician their jobs for tomorrow: when, "
                "where, what the inspection found (leak, roof, what could not be inspected), "
                "the time it takes and what to bring - suggested by AI only when nothing was "
                "recorded, and marked so.",
        covers="Every visit tomorrow on AHS - Inspection, AHS - Repair & Review and Retail. "
               "Test technicians only until test-only is switched off in its settings.",
        channels=("Text to the technician from the CRM line",),
        runs_on="CRM worker (reads Zuper's copy only)", provider="DeepSeek (suggestions)",
        hours="Once a day, at its send time (6 PM by default)",
        actions=(
            Action("text_staff", SHADOW, (NEVER, SHADOW, AUTO),
                   "One text per technician a day. Phones come from the Inspection "
                   "documentation settings."),
            Action("ai_suggest", AUTO, (NEVER, AUTO),
                   "Only for jobs whose inspection recorded no tools or materials."),
        ),
        never=("Text customers", "Write anything in Zuper", "Mention prices"),
        owner_hint="Luis", tags=("settings",),
    ),
    Entry(
        key="payments", name="Payments & collections", kind="agent", role=8,
        purpose="Tracks what each customer agreed to pay and when (e.g. 50% before the job, 50% "
                "after), shows what is due and overdue, keeps the proof, suggests payments it "
                "sees in Zuper or in a customer's screenshot for a person to confirm, and lets "
                "the office update everything by chat.",
        covers="Every agreement entered in Collections.",
        channels=("Collections page + chat", "Dispatch item + bell",
                  "Customer reminder through Zuper (off)"),
        runs_on="CRM (reads Zuper only)", provider="DeepSeek (chat), OpenAI/Anthropic (vision)",
        hours="Reminders 8 AM-8 PM Mon-Sat",
        actions=(
            Action("crm_write", AUTO, (), "The page and the chat; every change can be undone."),
            Action("alert_staff", AUTO, (), "A Dispatch item when a part is due, the bell when "
                                            "it is overdue or a screenshot arrives."),
            Action("ai_read", AUTO, (NEVER, AUTO), "The chat understands what you write."),
            Action("ai_vision", AUTO, (NEVER, AUTO),
                   "Checks customers' pictures for payment screenshots. Needs a vision "
                   "connection in its settings."),
            Action("send_customer", NEVER, (NEVER, SHADOW, AUTO),
                   "Payment reminders to customers. Also needs PAYMENTS_TEXTS_ENABLED."),
        ),
        never=("Mark anything paid without a person", "Write anything in Zuper",
               "Text a customer while its reminders are at Never"),
        owner_hint="Luis", tags=("settings",),
    ),
    Entry(
        key="insights", name="Daily report & insights", kind="agent", role=10,
        purpose="Reads every call and text once and tags what matters - still leaking, asked "
                "us to come back, complaints, interest in more work, payment problems, praise - "
                "each with the customer's exact words; measures leads, inspections, AHS "
                "approvals, repairs, callbacks per technician, upsell money and collections; "
                "raises customer issues; answers \"ask for a KPI\" questions.",
        covers="Every Zuper job and every Quo, CRM-line and Zuper Connect call and text.",
        channels=("Reporting -> Insights (KPIs, charts, tables, Excel, CSV)",
                  "Dispatch item + bell (Customer issues)"),
        runs_on="CRM (reads Zuper only)", provider="DeepSeek",
        hours="Every hour",
        actions=(
            Action("ai_read", AUTO, (NEVER, AUTO), "Each call and text is read once."),
            Action("alert_staff", AUTO, (), "A Dispatch item and the bell for still leaking, "
                                            "come back and complaints."),
            Action("ai_chat", AUTO, (NEVER, AUTO), "\"Ask for a KPI\" on the Insights tab."),
        ),
        never=("Text or call anyone", "Write anything in Zuper",
               "Report a number that is not in the data"),
        owner_hint="Owner", tags=("settings",),
    ),
    Entry(
        key="reviews", name="Reviews & QC", kind="agent", role=9,
        purpose="Two days after a repair is complete, if the customer said nothing unhappy, asks "
                "how we did from 1 to 5. A 5 gets a thank-you and the Google review link; 1-4 "
                "or a leak gets 'what could we do better?', the office is alerted at once, and "
                "the link is offered after they answer - never only to happy customers "
                "(Google's rule).",
        covers="Jobs that reach Repair Complete on every board.",
        channels=("Text from the Zuper line (954-420-7373)", "Anisa call if no reply (off)",
                  "Dispatch item + bell (Customer issues)", "Private note on the Zuper job"),
        runs_on="CRM (texts through Zuper)", provider="DeepSeek",
        hours="Texts and calls 8 AM-8 PM Mon-Sat",
        actions=(
            Action("send_text", SHADOW, (NEVER, SHADOW, AUTO),
                   "The rating text and the answers to it. Also needs REVIEWS_TEXTS_ENABLED."),
            Action("place_calls", NEVER, (NEVER, SHADOW, AUTO),
                   "Anisa calls when the rating text had no answer."),
            Action("ai_read", AUTO, (NEVER, AUTO), "Reads a reply that is not a plain 1-5."),
            Action("alert_staff", AUTO, (), "A Dispatch item and the bell for 1-4, a leak or "
                                            "an unclear answer."),
            Action("zuper_note", AUTO, (), "A private note on the job for every step."),
        ),
        never=("Send the review link only to happy customers", "Move a Zuper column",
               "Ask a customer who said it still leaks or complained", "Mention prices"),
        owner_hint="Luis", tags=("settings",),
    ),
    Entry(
        key="weather", name="Weather", kind="agent", role=3,
        purpose="Reads the National Weather Service forecast at every job's house every 3 hours, "
                "7 days ahead, and flags booked visits with a 50%+ chance of rain in their hours, "
                "heavy rain coming over customers with an open leak, and roofs repaired recently "
                "under a storm that just passed. Gives the booking options the dry days.",
        covers="Every open job with a map position in Zuper.",
        channels=("Dispatch item + bell (Weather)", "Texts to the numbers in its settings",
                  "Booking options and the Dispatch assistant"),
        runs_on="CRM (reads the NWS only)", provider=None,
        hours="Every 3 hours; the morning summary at 7 AM",
        actions=(
            Action("alert_staff", AUTO, (), "Dispatch items and the bell for the next 48 h."),
            Action("text_staff", SHADOW, (NEVER, SHADOW, AUTO),
                   "Texts to the numbers typed in its settings - nobody while it has none."),
        ),
        never=("Text or call a customer", "Write anything in Zuper",
               "Send an address or a name to the weather service"),
        owner_hint="Luis", tags=("settings",),
    ),
    Entry(
        key="dropped_call_text", name="Dropped-call text", kind="agent", role=1,
        purpose="When a caller leaves Anisa's call before being helped - hung up right away "
                "or mid-conversation, said nothing, or a transfer nobody answered - texts them "
                "\"Sorry we couldn't take your call, we will call you back as soon as "
                "possible.\" The AI decides from the call's summary.",
        covers="Every inbound call an AI agent answered.",
        channels=("Text from the Zuper line (954) 420-7373 (Zuper customers)",
                  "Text from the CRM line (callers Zuper does not know)"),
        runs_on="CRM", provider="DeepSeek", hours="8 AM-8 PM Mon-Sat (night calls: 8 AM)",
        actions=(
            Action("send_text", AUTO, (NEVER, SHADOW, AUTO),
                   "Live for everyone (the owner, 2026-10-22). Also needs "
                   "DROPPED_CALL_TEXTS_ENABLED."),
            Action("ai_read", AUTO, (), "Decides from the call's summary whether the caller "
                                        "left before being helped."),
        ),
        never=("Text a call a person took", "Text twice in a day", "Text after STOP",
               "Text someone the team already called back"),
        owner_hint="Luis",
    ),
    # ------------------------------------------------------------------ automations
    Entry(
        key="dispatch_flags", name="Dispatch items and bell", kind="automation", role=10,
        purpose="Every 2.5 minutes, finds what the office must do next — new jobs nobody "
                "called, finished visits, missed calls, visits to book, jobs gone quiet — "
                "and rings the bell once per event.",
        covers="Zuper's AHS and Retail boards and every call and text.",
        channels=("Dispatch page, bell",), runs_on="CRM worker", provider=None,
        hours="Around the clock",
        actions=(Action("alert_staff", AUTO),),
    ),
    Entry(
        key="ahs_authorizations", name="AHS approvals into Dispatch", kind="automation", role=5,
        purpose="When owen-main reads an AHS note approving a repair, it becomes a Dispatch "
                "item and rings the bell.",
        covers="AHS jobs.", channels=("Dispatch page, bell",),
        runs_on="owen-main → CRM", provider=None,
        hours="Switched on at owen-main (CRM_LINK_AHS_AUTHORIZATIONS_ENABLED)",
        actions=(Action("alert_staff", AUTO),),
    ),
    Entry(
        key="ahs_email_jobs", name="AHS work orders create Zuper jobs", kind="automation",
        role=5,
        purpose="An AHS work-order email becomes a CRM card and, through three allowed "
                "requests, a customer, job and note in Zuper's AHS - Inspection board.",
        covers="AHS work orders.", channels=("Zuper",), runs_on="CRM", provider=None,
        hours="Around the clock",
        actions=(Action("zuper_create", AUTO, (), "Owner lift 2026-09-28."),),
    ),
    Entry(
        key="proposal_lines", name="Signed proposal → job line items", kind="automation",
        role=6,
        purpose="Copies a signed proposal's chosen option onto its job's line items.",
        covers="Jobs with a signed proposal.", channels=("Zuper",), runs_on="CRM",
        provider=None, hours="Around the clock",
        actions=(Action("zuper_lines", AUTO, (), "Owner lift 2026-10-02."),),
    ),
    Entry(
        key="crm_reminders", name="CRM appointment reminders", kind="automation", role=3,
        purpose="Day-before and same-day reminder texts from the CRM. Switched off "
                "2026-10-12; Zuper's workflows send reminders now.",
        covers="Retail Scheduled and AHS - Inspection visits.",
        channels=("CRM text from +1 786-920-0331",), runs_on="CRM", provider=None,
        hours="Off",
        actions=(Action("send_text", NEVER, (), "Off by the user's decision of 2026-10-12."),),
    ),
    Entry(
        key="kpi_report", name="Zuper history and nightly KPI Excel", kind="automation",
        role=10,
        purpose="Keeps an append-only copy of every Zuper column move and record change, and "
                "writes one KPI Excel a night on the server.",
        covers="Every Zuper job.", channels=("Server files",), runs_on="CRM worker",
        provider=None, hours="Nightly after 2 AM",
        actions=(Action("report", AUTO),),
    ),
    Entry(
        key="zuper_workflows", name="Zuper customer-text workflows", kind="automation", role=2,
        purpose="Six Zuper workflows: AHS reminders a day and 2 hours before a visit, texts at "
                "Inspection Completed, Awaiting AHS Decision and AHS Approved, and thank-you "
                "replies.",
        covers="AHS - Inspection and AHS - Repair & Review customers.",
        channels=("Zuper text from (954) 420-7373",), runs_on="Zuper", provider=None,
        hours="8 AM-8 PM", tags=("outside",),
        actions=(Action("send_text", AUTO, (), "Live since 2026-10-13. Switched in Zuper."),),
    ),
    Entry(
        key="zuper_tasks", name="Checklist tasks on AHS jobs", kind="automation", role=4,
        purpose="Keeps a task per AHS stage on every live AHS job, ticked from the checklist "
                "answers.",
        covers="Live AHS jobs.", channels=("Zuper",), runs_on="owen-main container zuper_tasks",
        provider=None, hours="Every 2 minutes", tags=("outside",),
        actions=(Action("zuper_tasks", AUTO, (), "Predates the read-only rule; owner said "
                                                 "leave it (2026-09-28)."),),
    ),
    Entry(
        key="zuper_proposals", name="Proposal PDFs", kind="automation", role=6,
        purpose="Builds the Good / Better / Best proposal PDF and attaches it to every draft "
                "proposal.",
        covers="Draft proposals.", channels=("Zuper",),
        runs_on="owen-main container zuper_proposals", provider=None, hours="Every minute",
        tags=("outside",),
        actions=(Action("zuper_files", AUTO, (), "Predates the read-only rule."),),
    ),
    Entry(
        key="zuper_routes", name="Technician routes", kind="automation", role=3,
        purpose="Builds each technician's daily route in Zuper from the Technician field.",
        covers="Scheduled visits.", channels=("Zuper",), runs_on="dispatch server cron",
        provider=None, hours="Every 10 minutes", tags=("outside",),
        actions=(Action("zuper_routes", AUTO, (), "Predates the read-only rule."),),
    ),
)

BY_KEY = {e.key: e for e in ENTRIES}

# AI Agents rows (`ai_agent:<id>`): what their actions are and where their level comes from.
# Not editable here: each has its own switch in the agent's builder, which stays the one place
# to change it, so two controls never disagree.
AI_AGENT_PREFIX = "ai_agent:"


def ai_agent_key(agent_id: int) -> str:
    return "%s%d" % (AI_AGENT_PREFIX, agent_id)


def ai_agent_id(key: str) -> int | None:
    if not key.startswith(AI_AGENT_PREFIX):
        return None
    try:
        return int(key[len(AI_AGENT_PREFIX):])
    except ValueError:
        return None


def mode_level(mode: str) -> str:
    """An AI agent's CRM-writes mode as a ladder level."""
    return {"off": NEVER, "suggest": APPROVE, "auto": AUTO}.get(mode, NEVER)


def ai_agent_actions(channel: str, mode: str, answering: bool) -> tuple[Action, ...]:
    write = mode_level(mode)
    if channel == "voice":
        return (Action("answer_calls", AUTO if answering else NEVER, (),
                       "Its “Answering calls” switch."),
                Action("crm_write", write, (), "Its mode (“What it may write here”)."))
    return (Action("send_text", write, (), "Its mode: Suggest = approve each."),
            Action("crm_write", write, (), "Its mode."))


def validate() -> list[str]:
    """Problems in the catalog itself; a test asserts this is empty."""
    out = []
    seen = set()
    for e in ENTRIES:
        if e.key in seen:
            out.append("duplicate key %s" % e.key)
        seen.add(e.key)
        if e.role not in ROLES:
            out.append("%s: unknown role %s" % (e.key, e.role))
        if e.kind not in ("agent", "automation"):
            out.append("%s: unknown kind %s" % (e.key, e.kind))
        if e.key.startswith(AI_AGENT_PREFIX):
            out.append("%s: reserved prefix" % e.key)
        for a in e.actions:
            if a.action not in ACTIONS:
                out.append("%s: unknown action %s" % (e.key, a.action))
            if a.default not in LEVELS:
                out.append("%s.%s: unknown level %s" % (e.key, a.action, a.default))
            if a.choices and a.default not in a.choices:
                out.append("%s.%s: default not among its choices" % (e.key, a.action))
            if any(c not in LEVELS for c in a.choices):
                out.append("%s.%s: unknown choice" % (e.key, a.action))
        if e.planned and any(a.choices or a.default != NEVER for a in e.actions):
            out.append("%s: a planned entry has every action at Never, not switchable" % e.key)
        if e.kind == "automation" and any(a.choices for a in e.actions):
            out.append("%s: an automation's levels are not editable here" % e.key)
    return out
