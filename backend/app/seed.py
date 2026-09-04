"""Seed synthetic data shaped like the measured GHL account.

NO REAL CLIENT DATA. Names and numbers are generated. Only the *shape* is copied
from measurement, so screens can be compared like-for-like:

  268 contacts, 14 pages @ 20/page
  pipeline "Dream Team Roofing AHS" with the 5 measured stages and their
  measured counts/values:
     New Lead                   6   $2,475.00
     Inspection                 1   $0.00
     Request the Approval (AHS) 16  $23,211.98
     Approved- Repair Schedule  0   $0.00
     Repair in Process          0   $0.00
  conversation threads that interleave SMS with inbound CALL records, because
  that is what the live inbox actually contains.

Comparing against an empty list teaches nothing, which is why the counts match.
"""
import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from .db import Base, SessionLocal, engine
from .models import (
    Appointment,
    Calendar,
    Contact,
    ContactTag,
    Conversation,
    ConversationEvent,
    DeliveryStatus,
    Direction,
    EventType,
    Opportunity,
    Pipeline,
    Role,
    Stage,
    Tag,
    User,
)

random.seed(1809)

FIRST = ["James", "Maria", "Robert", "Linda", "Michael", "Patricia", "David",
         "Jennifer", "William", "Elizabeth", "Richard", "Susan", "Joseph",
         "Jessica", "Thomas", "Sarah", "Charles", "Karen", "Daniel", "Nancy",
         "Matthew", "Lisa", "Anthony", "Betty", "Mark", "Sandra", "Donald",
         "Ashley", "Steven", "Dorothy", "Andrew", "Kimberly", "Joshua", "Emily"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
        "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
        "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
        "Lee", "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark"]
SOURCES = ["Facebook Ad", "Google LSA", "Referral", "Website Form",
           "Craigslist", "Yard Sign", "AHS Warranty"]

# All 10 stages, measured by scrolling the kanban horizontally
# (captures/opportunities/stages_clean.json). Counts reconcile to the header: 26.
# Ordering is by absolute x (viewport x + scrollLeft) — viewport x alone is not
# comparable across scroll steps.
# Preserved as measured: the typo in "Approved- Repair Schedule", and the two
# distinct stages both named "Call Back".
STAGES = [
    ("New Lead", 7, 257500),
    ("Inspection", 1, 0),
    ("Request the Approval (AHS)", 16, 2321198),
    ("Approved- Repair Schedule", 0, 0),
    ("Repair in Process", 0, 0),
    ("Submit The Invoice", 0, 0),
    ("Call Back", 0, 0),
    ("Call Back", 2, 210000),
    ("AHS Upgrades", 0, 0),
    ("Submit Invoices", 0, 0),
]

SMS_IN = ["Hi, I have a leak in my roof after the storm",
          "Can someone come take a look this week?",
          "What time is the tech arriving?",
          "Thanks, that works for me",
          "Is this covered under my AHS warranty?"]
SMS_OUT = ["Thanks for reaching out - we can get someone out Thursday.",
           "Our tech is on the way, about 20 minutes out.",
           "We've submitted the claim to AHS, waiting on approval.",
           "Confirming your appointment for tomorrow at 9am."]


def now():
    return datetime.now(UTC)


def run():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    db.add_all([
        User(email="owner@example.test", name="Owen", role=Role.ADMIN),
        User(email="dispatch@example.test", name="Dispatch", role=Role.DISPATCHER),
        User(email="tech1@example.test", name="Field Tech", role=Role.TECH),
    ])

    contacts = []
    for i in range(268):
        fn, ln = random.choice(FIRST), random.choice(LAST)
        c = Contact(
            first_name=fn, last_name=ln,
            email="%s.%s%d@example.test" % (fn.lower(), ln.lower(), i),
            phone="(%d) %d-%04d" % (random.choice([941, 813, 727, 786]),
                                    random.randint(200, 999),
                                    random.randint(0, 9999)),
            business_name=("%s %s" % (ln, random.choice(
                ["Properties", "Holdings", "Residence"])))
            if random.random() < 0.25 else None,
            source=random.choice(SOURCES),
            contact_type=random.choice(["Lead", "Lead", "Lead", "Customer"]),
            created_by=random.choice(["Inbound Call", "Manual", "Website Form"]),
            created_at=now() - timedelta(days=random.randint(0, 400),
                                         hours=random.randint(0, 23)),
        )
        contacts.append(c)
    db.add_all(contacts)
    db.flush()

    user_rows = db.scalars(select(User)).all()
    for i, c in enumerate(contacts):
        if i % 3 == 0:
            c.owner_id = user_rows[i % len(user_rows)].id

    tag_names = ["Upsell Opportunity", "Warranty", "Priority-High",
                 "couldn't find caller name", "AHS"]
    tags = [Tag(name=n) for n in tag_names]
    db.add_all(tags)
    db.flush()
    for i, c in enumerate(contacts):
        if i % 4 == 0:
            db.add(ContactTag(contact_id=c.id, tag_id=tags[i % len(tags)].id))
    db.flush()

    pipeline = Pipeline(name="Dream Team Roofing AHS", position=0)
    db.add(pipeline)
    db.flush()

    opp_i = 0
    for pos, (sname, count, total_cents) in enumerate(STAGES):
        stage = Stage(pipeline_id=pipeline.id, name=sname, position=pos)
        db.add(stage)
        db.flush()
        # Split the measured stage total across its opportunities.
        remaining = total_cents
        for n in range(count):
            last = n == count - 1
            v = remaining if last else (
                min(remaining, random.choice([0, 110000, 112500, 100000, 12500]))
            )
            remaining -= v
            contact = contacts[opp_i * 7 % len(contacts)]
            db.add(Opportunity(
                title="%s - Active" % contact.name.upper(),
                contact_id=contact.id, pipeline_id=pipeline.id, stage_id=stage.id,
                value_cents=max(0, v), position=n,
                business_name=contact.business_name,
                source=contact.source,
                created_by="Opportunities details",
                custom_fields={
                    "owen_campaign": random.choice(SOURCES),
                    "owen_tracking_number": "(941) 555-%04d" % random.randint(0, 9999),
                    "owen_call_id": "owen-%06d" % random.randint(0, 999999),
                    "owen_is_new_caller": random.choice([True, False]),
                },
            ))
            opp_i += 1

    for i in range(24):
        contact = contacts[i * 11 % len(contacts)]
        started = now() - timedelta(days=random.randint(0, 21),
                                    hours=random.randint(0, 23))
        conv = Conversation(contact_id=contact.id, last_event_at=started,
                            unread_count=1 if i < 2 else 0, starred=i % 9 == 0)
        db.add(conv)
        db.flush()

        t = started - timedelta(hours=6)
        events = []
        # Threads interleave calls and SMS - measured behaviour of the live inbox.
        if i % 3 == 0:
            events.append(ConversationEvent(
                conversation_id=conv.id, type=EventType.CALL,
                direction=Direction.INBOUND, occurred_at=t,
                duration_seconds=random.randint(8, 240),
                call_status=random.choice(
                    ["completed", "completed", "no-answer", "voicemail", "busy"]),
                recording_url="/media/recordings/demo-%d.mp3" % i,
                body="Call completed"))
            t += timedelta(minutes=random.randint(5, 90))
        for n in range(random.randint(2, 6)):
            inbound = n % 2 == 0
            events.append(ConversationEvent(
                conversation_id=conv.id, type=EventType.SMS,
                direction=Direction.INBOUND if inbound else Direction.OUTBOUND,
                occurred_at=t,
                body=random.choice(SMS_IN if inbound else SMS_OUT),
                delivery_status=None if inbound else DeliveryStatus.LOGGED_ONLY))
            t += timedelta(minutes=random.randint(3, 240))

        # Activity entries render inline on the same timeline - measured from
        # GHL's "Filter messages" menu (Appointments / Opportunities / Invoice).
        if i % 4 == 0:
            events.append(ConversationEvent(
                conversation_id=conv.id, type=EventType.APPOINTMENT,
                direction=Direction.OUTBOUND, occurred_at=t,
                body="Appointment scheduled - Roof Inspection"))
            t += timedelta(hours=1)
        if i % 5 == 0:
            events.append(ConversationEvent(
                conversation_id=conv.id, type=EventType.OPPORTUNITY,
                direction=Direction.OUTBOUND, occurred_at=t,
                body="Opportunity moved to Request the Approval (AHS)"))
            t += timedelta(hours=1)

        conv.last_event_at = t
        db.add_all(events)

    # Calendars: per-user like GHL, plus one linked to the pipeline (our addition).
    cal_rows = [
        Calendar(name="%s's Personal Calendar" % u.name, user_id=u.id,
                 color=c)
        for u, c in zip(db.scalars(select(User)).all(),
                        ["#004eeb", "#12b76a", "#f79009"], strict=False)
    ]
    cal_rows.append(Calendar(name="Workiz Jobs (imported)", color="#7585bd"))
    db.add_all(cal_rows)
    db.flush()
    cal_ids = [c.id for c in cal_rows]

    user_ids = [u.id for u in db.scalars(select(User)).all()]
    for i in range(12):
        contact = contacts[i * 5 % len(contacts)]
        start = now() + timedelta(days=random.randint(-3, 14),
                                  hours=random.randint(8, 16))
        db.add(Appointment(
            title="%s - %s" % (contact.name, random.choice(
                ["Roof Inspection", "Leak Repair", "Tarp", "AHS Assessment"])),
            status=random.choice(["confirmed", "confirmed", "showed", "booked",
                                  "cancelled", "no-show", "rescheduled"]),
            contact_id=contact.id, starts_at=start,
            assigned_user_id=user_ids[i % len(user_ids)],
            calendar_id=cal_ids[i % len(cal_ids)],
            ends_at=start + timedelta(hours=1)))

    db.commit()
    print("seeded: %d contacts, %d opportunities, 24 conversations, 12 appointments"
          % (len(contacts), opp_i))
    db.close()


if __name__ == "__main__":
    run()
