"""The Forecast tab: projected revenue by stage.

The risk this file exists to close is not "does the endpoint answer 200" — it is
that a forecast is trivially easy to ship as a *second*, contradictory set of
numbers. The Dashboard already publishes a conversion rate, a total value and a
won value for the same pipeline, and the Dashboard funnel already publishes a
count and a value per stage (out of `GET /api/pipelines`). If the forecast were
computed its own way it would look right and disagree, and nobody would know which
screen to believe.

So every figure below is hand-computed from the fixture AND asserted to equal what
the other two endpoints say on the same data.

The fixture (one pipeline, five stages) — every amount deliberately different:

  | # | stage           | open                     | won                | other        |
  |---|-----------------|--------------------------|--------------------|--------------|
  | 0 | New Lead        | 100000 + 250000 + 30000  | —                  | —            |
  | 1 | Inspection      | 500000                   | 900000             | lost 70000   |
  | 2 | Call Back       | —                        | —                  | —            |
  | 3 | Call Back       | 2                        | —                  | —            |
  | 4 | Submit Invoices | —                        | 1200000 + 40000    | abandoned 55000 |

  won 3, lost 1  ->  conversion 3/4 = 75.00%

Two stages are called "Call Back" on purpose: that is the measured shape of the
real pipeline (DECISIONS.md) and the forecast has to report them as two rows, not
merge them by name. The 2-cent deal is the half-cent rounding case — 2 x 75% is
1.5 cents, which must round to 2 and not to 1.

A second pipeline holds one 999999-cent deal that must never appear in the first
pipeline's forecast.
"""
import pytest
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Contact, Opportunity, Pipeline, Role, Stage, User
from fastapi.testclient import TestClient

# The fixture, restated as data so the assertions below read as arithmetic.
STAGES = [
    ("New Lead", [("open", 100000), ("open", 250000), ("open", 30000)]),
    ("Inspection", [("open", 500000), ("won", 900000), ("lost", 70000)]),
    ("Call Back", []),
    ("Call Back", [("open", 2)]),
    ("Submit Invoices", [("won", 1200000), ("won", 40000), ("abandoned", 55000)]),
]

RATE = 75.0                      # won 3 / decided 4
OPEN_VALUE = 380000 + 500000 + 2
WON_VALUE = 900000 + 1200000 + 40000
TOTAL_VALUE = sum(cents for _, rows in STAGES for _, cents in rows)
TOTAL_COUNT = sum(len(rows) for _, rows in STAGES)


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    admin = User(email="a@x.test", name="Owen", role=Role.ADMIN)
    tech = User(email="t@x.test", name="Tech", role=Role.TECH)
    db.add_all([admin, tech])
    db.flush()

    person = Contact(first_name="Fore", last_name="Cast", phone="(941) 555-0001")
    db.add(person)
    db.flush()

    pipe = Pipeline(name="Dream Team Roofing AHS")
    other = Pipeline(name="Retail")
    db.add_all([pipe, other])
    db.flush()

    stage_ids = []
    for position, (name, rows) in enumerate(STAGES):
        s = Stage(pipeline_id=pipe.id, name=name, position=position)
        db.add(s)
        db.flush()
        stage_ids.append(s.id)
        for i, (status, cents) in enumerate(rows):
            db.add(Opportunity(title="%s %d" % (name, i), contact_id=person.id,
                               pipeline_id=pipe.id, stage_id=s.id,
                               value_cents=cents, status=status, position=i))

    # A whole second pipeline the forecast must not see.
    os = Stage(pipeline_id=other.id, name="Only stage", position=0)
    db.add(os)
    db.flush()
    db.add(Opportunity(title="ELSEWHERE", pipeline_id=other.id, stage_id=os.id,
                       value_cents=999999, status="open"))

    tokens = {}
    for key, u in (("admin", admin), ("tech", tech)):
        plain, token = mint_api_token(u, name="test-%s" % key)
        db.add(token)
        tokens[key] = plain
    db.commit()
    ids = {"pipeline": pipe.id, "other": other.id, "stages": stage_ids}
    db.close()

    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + tokens["admin"]
        c.ids = ids
        c.tokens = tokens
        yield c


def _forecast(client, pipeline_id=None):
    pid = pipeline_id or client.ids["pipeline"]
    r = client.get("/api/forecast", params={"pipeline_id": pid})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------- the numbers are the numbers ----------------

def test_the_forecast_rate_is_the_dashboards_rate(client):
    """A second conversion rate on the same pipeline would be a second answer."""
    f = _forecast(client)
    d = client.get("/api/dashboard",
                   params={"pipeline_id": client.ids["pipeline"]}).json()
    assert f["conversion_rate"] == RATE
    assert f["conversion_rate"] == d["conversion_rate"]
    assert f["status"] == d["status"] == {"won": 3, "open": 5, "lost": 1,
                                          "abandoned": 1}


def test_the_forecast_totals_are_the_dashboards_totals(client):
    f = _forecast(client)["totals"]
    d = client.get("/api/dashboard",
                   params={"pipeline_id": client.ids["pipeline"]}).json()
    assert f["count"] == TOTAL_COUNT == d["total"]
    assert f["value_cents"] == TOTAL_VALUE == d["total_value_cents"]
    assert f["won_value_cents"] == WON_VALUE == d["won_value_cents"]
    assert f["open_value_cents"] == OPEN_VALUE


def test_each_stage_row_matches_what_the_funnel_is_fed(client):
    """The Dashboard funnel draws `count` and `value_cents` from /api/pipelines.

    The forecast repeats those two columns, so they have to be the same numbers —
    the funnel counts EVERY status, not just the open ones, and a forecast that
    quietly counted only open deals would draw a shorter bar for the same stage.
    """
    rows = {s["stage_id"]: s for s in _forecast(client)["stages"]}
    pipe = next(p for p in client.get("/api/pipelines").json()
                if p["id"] == client.ids["pipeline"])
    assert len(rows) == len(pipe["stages"]) == 5
    for s in pipe["stages"]:
        assert rows[s["id"]]["count"] == s["count"]
        assert rows[s["id"]]["value_cents"] == s["value_cents"]
        assert rows[s["id"]]["name"] == s["name"]


@pytest.mark.parametrize("index,name,count,open_value,won_value,weighted", [
    # weighted = open value x 75%, half up, in whole cents
    (0, "New Lead", 3, 380000, 0, 285000),
    (1, "Inspection", 3, 500000, 900000, 375000),
    (2, "Call Back", 0, 0, 0, 0),
    (3, "Call Back", 1, 2, 0, 2),          # 1.5 cents rounds UP, not down
    (4, "Submit Invoices", 3, 0, 1240000, 0),
])
def test_a_stage_projects_its_open_money_at_the_published_rate(
        client, index, name, count, open_value, won_value, weighted):
    row = _forecast(client)["stages"][index]
    assert (row["name"], row["count"]) == (name, count)
    assert row["open_value_cents"] == open_value
    assert row["won_value_cents"] == won_value
    assert row["weighted_value_cents"] == weighted
    # ...and the projection is money already won plus the weighted open money.
    assert row["projected_value_cents"] == won_value + weighted


def test_the_stage_rows_add_up_to_the_totals_row(client):
    """A total that is recomputed rather than summed can disagree with its column."""
    f = _forecast(client)
    for key in ("open_count", "open_value_cents", "won_count",
                "weighted_value_cents", "projected_value_cents", "count",
                "value_cents"):
        assert f["totals"][key] == sum(s[key] for s in f["stages"]), key
    assert f["totals"]["weighted_value_cents"] == 285000 + 375000 + 2
    assert f["totals"]["projected_value_cents"] == 285000 + 1275000 + 2 + 1240000


def test_two_stages_of_the_same_name_stay_two_rows(client):
    """The measured pipeline has two distinct stages both called "Call Back"."""
    rows = [s for s in _forecast(client)["stages"] if s["name"] == "Call Back"]
    assert len(rows) == 2
    assert rows[0]["stage_id"] != rows[1]["stage_id"]
    assert [r["count"] for r in rows] == [0, 1]


# ---------------- it is scoped, and it is read-only ----------------

def test_the_forecast_is_one_pipeline_and_not_the_others(client):
    f = _forecast(client)
    assert f["pipeline_id"] == client.ids["pipeline"]
    assert 999999 not in [s["open_value_cents"] for s in f["stages"]]
    assert f["totals"]["value_cents"] == TOTAL_VALUE   # excludes the 999999 deal

    other = _forecast(client, client.ids["other"])
    assert other["totals"]["value_cents"] == 999999
    assert [s["name"] for s in other["stages"]] == ["Only stage"]


def test_an_unknown_pipeline_is_not_an_empty_forecast(client):
    """Zeros for a pipeline that does not exist read as "no deals", not "no such
    pipeline" — the same confusion the Dashboard error cards exist to prevent."""
    assert client.get("/api/forecast", params={"pipeline_id": 999999}).status_code == 404


def test_the_forecast_writes_nothing(client):
    """Read-only by construction; asserted so it stays that way."""
    before = client.get("/api/opportunities", params={
        "pipeline_id": client.ids["pipeline"], "status": "all"}).json()
    _forecast(client)
    after = client.get("/api/opportunities", params={
        "pipeline_id": client.ids["pipeline"], "status": "all"}).json()
    assert before == after


def test_a_tech_cannot_read_the_forecast(client):
    """Same gate as /api/dashboard, whose aggregates this repeats.

    Recorded in DECISIONS.md: per-stage money is already TECH-visible through
    /api/pipelines, so this gate withholds an aggregate rather than a secret. It
    matches the Dashboard deliberately — the browser hides the tab for a TECH
    instead of letting them open it and read an error.
    """
    tech = TestClient(app)
    tech.headers["Authorization"] = "Bearer " + client.tokens["tech"]
    r = tech.get("/api/forecast", params={"pipeline_id": client.ids["pipeline"]})
    assert r.status_code == 403
    assert client.get("/api/dashboard", params={
        "pipeline_id": client.ids["pipeline"]}).status_code == 200
