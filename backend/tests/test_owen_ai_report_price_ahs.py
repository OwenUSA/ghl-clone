"""The report's "What goes into your price" and "Why AHS doesn't pay for all of it" (2026-11-16).

  * the distance from downtown Miami comes from the job's address, rounded down, never written by
    Claude; nearby jobs (under 5 miles) do not get it;
  * Claude's cost factors follow it; no factors and no address = no section;
  * the AHS section appears only on an AHS job, with AHS's own pictures, both links, and THIS
    claim's authorized amount only when Frontdoor gave one;
  * facts keep the new fields through a save (facts_to_dict / facts_from_dict).
No network: pure rendering.
"""
from types import SimpleNamespace

from app.owen_ai import report


def facts(**kw):
    return report.Facts(job_uid="u", job_number="810", **kw)


def spec(*factors):
    return SimpleNamespace(cost_factors=[report.CostFactor(title=t, why=w) for t, w in factors])


def test_distance_is_measured_from_downtown_miami_and_rounded_down():
    assert report.miles_from_downtown_miami([26.2036594, -80.2189528]) == 29   # Pompano Beach
    assert report.miles_from_downtown_miami([26.7205931, -80.1579497]) == 65   # West Palm Beach
    assert report.miles_from_downtown_miami(None) is None
    assert report._geo({"geo_cordinates": [26.2, -80.2]}) == [26.2, -80.2]
    assert report._geo({"geo_cordinates": [0, 0]}) is None and report._geo({}) is None


def test_the_jobs_own_factors_first_then_every_jobs_then_the_distance_last():
    f = facts(geo=[26.2036594, -80.2189528], license="CCC123",
              options=[report.Option(name="GOOD", warranty="1 month"),
                       report.Option(name="BEST", warranty="6 months")])
    rows = report.price_rows(spec(("Two-story home", "Ladders and safety lines.")), f)
    titles = [t for t, *_ in rows]
    assert titles[0] == "Two-story home"
    assert titles[1:4] == ["Written workmanship warranty", "Licensed and insured",
                           "Cleanup and disposal"]
    assert titles[-1] == "About 29 miles from downtown Miami"
    assert "1 month (Good), 6 months (Best)" in rows[1][1] and "CCC123" in rows[2][1]
    near = [t for t, *_ in report.price_rows(spec(), facts(geo=[25.78, -80.20]))]
    assert not [t for t in near if "miles" in t]
    photo = report.CostFactor(title="Cedar shakes", why="Cut to fit.", photo="photo 21")
    html = report.price_factors_html(SimpleNamespace(cost_factors=[photo]), facts())
    assert "(see photo 21)" in html
    assert "(see" not in report.price_factors_html(spec(("Tile", "By hand.")), facts())


def test_the_ahs_section_only_on_ahs_jobs_with_their_own_words():
    assert report.ahs_html(facts(ahs_job=False)) == ""
    html = report.ahs_html(facts(ahs_job=True, ahs_authorized=1125.0, ahs_dispatch="99913749"))
    assert "Why AHS doesn&#39;t pay for all of it" in html
    assert html.count("data:image/png;base64,") == 3
    assert report.AHS_ROOF_URL in html and report.AHS_PLANS_URL in html
    assert "not affiliated with American Home Shield" in html
    # Never this claim's amount, even when Frontdoor knows it: often it is not known yet.
    assert "1,125" not in html and "99913749" not in html and "your</b> claim" not in html


def test_the_new_facts_survive_a_save():
    f = facts(geo=[26.2, -80.2], ahs_job=True, ahs_authorized=1100.0, ahs_dispatch="1")
    back = report.facts_from_dict(report.facts_to_dict(f))
    assert (back.geo, back.ahs_job, back.ahs_authorized, back.ahs_dispatch) == \
        ([26.2, -80.2], True, 1100.0, "1")


def test_retail_leaves_the_ahs_section_out_even_on_the_ahs_board():
    """#829: on the AHS board with an AHS Job ID, but Owen says she has no AHS coverage."""
    f = facts(ahs_job=True)
    ahs_spec = spec(("Two stories", "Access needs more safety equipment."))
    ahs_spec.retail = False
    assert "Why AHS doesn&#39;t pay" in report.price_page_html(ahs_spec, f)
    ahs_spec.retail = True
    page = report.price_page_html(ahs_spec, f)
    assert "Why AHS doesn&#39;t pay" not in page and "What goes into your price" in page
    assert "retail" in report.Spec.model_fields
