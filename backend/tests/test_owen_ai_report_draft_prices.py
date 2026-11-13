"""A report's prices from a PENDING proposal draft (2026-11-13): `proposal: "DRAFT-<id>"`.

The draft is read exactly as a Zuper proposal would be (its totals, deposits, warranties and price
block), only for the draft's own job, and nothing is asked of Zuper for the options.
"""
from app.models import OwenAiProposalDraft
from app.owen_ai import report

UID = "0f5c2b9e-1111-4222-8333-944455556666"


def opt(name, price, credit=1125):
    member = price + credit
    scope = ("<p><b>Scope of Work</b></p><ul><li>(Retail Value: $%d | Special AHS Member Price: $%d"
             " | AHS Authorized Credit: -$%d | Customer Pays Out of Pocket: $%d | Total Savings: "
             "$1)</li><li>Targeted Leak Inspection: look at the leak.</li></ul><p>Workmanship "
             "Warranty: %s This warranty covers workmanship.</p>"
             % (member + 1000, member, credit, price,
                {"GOOD": "1 Month", "BETTER": "3 Months", "BEST": "6 Months"}[name]))
    return {"name": name, "option_description": name.title() + " repair",
            "is_recommended": name == "BEST", "price": price, "deposit": price / 2,
            "product": {"uid": "p1", "code": "LEAK", "name": "Leak repair"}, "scope_html": scope}


def draft(db, job_uid=UID, status="pending"):
    row = OwenAiProposalDraft(job_uid=job_uid, job_number="787", customer_name="Dan Test",
                              template_key="tile", template_uid="t1", title="Leak repair",
                              options=[opt("GOOD", 0), opt("BETTER", 975), opt("BEST", 2675)],
                              spec={}, status=status)
    db.add(row)
    db.commit()
    return row


def test_draft_ref():
    assert report.draft_ref("DRAFT-12") == 12 and report.draft_ref("draft12") == 12
    assert report.draft_ref("DTR-168") is None and report.draft_ref(None) is None


def test_a_pending_draft_gives_the_report_its_prices(db):
    row = draft(db)
    warnings = []
    opts, label = report.options_from_draft(row.id, UID.upper(), warnings)
    assert label == "draft %d" % row.id
    assert [o.name for o in opts] == ["GOOD", "BETTER", "BEST"]
    assert [o.total for o in opts] == [0, 975, 2675]
    best = opts[2]
    assert best.credit == -1125 and best.member == 3800 and best.recommended
    assert [o.warranty for o in opts] == ["1 month", "3 months", "6 months"]
    assert not [w for w in warnings if "could not read" in w]


def test_only_the_drafts_own_job(db):
    row = draft(db, job_uid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    warnings = []
    assert report.options_from_draft(row.id, UID, warnings) == ([], None)
    assert "no proposal draft" in warnings[0]
