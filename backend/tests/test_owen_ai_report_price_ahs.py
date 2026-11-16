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


def test_price_factors_start_with_the_distance_then_claudes_factors():
    html = report.price_factors_html(spec(("Two-story home", "Ladders and safety lines.")),
                                     facts(geo=[26.2036594, -80.2189528]))
    assert "What goes into your price" in html
    assert html.index("About 29 miles from downtown Miami") < html.index("Two-story home")
    near = report.price_factors_html(spec(("Concrete tile", "Lifted by hand.")),
                                     facts(geo=[25.78, -80.20]))
    assert "miles from downtown" not in near and "Concrete tile" in near
    assert report.price_factors_html(spec(), facts()) == ""


def test_the_ahs_section_only_on_ahs_jobs_with_their_own_words():
    assert report.ahs_html(facts(ahs_job=False)) == ""
    html = report.ahs_html(facts(ahs_job=True, ahs_authorized=1125.0, ahs_dispatch="99913749"))
    assert "Why AHS doesn&#39;t pay for all of it" in html
    assert html.count("data:image/png;base64,") == 4
    assert report.AHS_ROOF_URL in html and report.AHS_PLANS_URL in html
    assert "AHS authorized <b>$1,125</b>" in html and "dispatch 99913749" in html
    assert "not affiliated with American Home Shield" in html
    assert "authorized" not in report.ahs_html(facts(ahs_job=True)).split("<h2")[1].split(
        "replace roofs")[0].replace("authorized_to", "")


def test_the_new_facts_survive_a_save():
    f = facts(geo=[26.2, -80.2], ahs_job=True, ahs_authorized=1100.0, ahs_dispatch="1")
    back = report.facts_from_dict(report.facts_to_dict(f))
    assert (back.geo, back.ahs_job, back.ahs_authorized, back.ahs_dispatch) == \
        ([26.2, -80.2], True, 1100.0, "1")
