"""CompanyCam in the browser (2026-09-14): executed where it can be.

**Executed under node.** `lib/companycam.ts` is import-free, so the shipped code runs
here: the order photos are drawn in across pages, where previous / next land in the
viewer (and when "next" has to load another page), the date written under a photo in
the ACCOUNT's zone whatever the laptop's zone is, and the sentence a tab shows instead
of photos.

**Asserted against source.** The tab, the viewer, the contact panel's section and the
Settings page are React, which this project has no runner for. Each assertion names
the failure it would catch.
"""
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
NODE = shutil.which("node")

node = pytest.mark.skipif(
    NODE is None,
    reason="node is not on PATH, so lib/companycam.ts cannot be executed. CI's backend "
           "job installs it so this file is never skipped there.")


def _read(*parts: str) -> str:
    return FRONTEND.joinpath(*parts).read_text(encoding="utf-8")


def run_js(body: str, tz: str = "Europe/Berlin"):
    script = textwrap.dedent("""
        import * as m from %s
        const out = (v) => console.log('@@' + JSON.stringify(v))
    """) % json.dumps((FRONTEND / "lib" / "companycam.ts").as_posix()) + textwrap.dedent(body)
    proc = subprocess.run(
        [NODE, "--experimental-strip-types", "--input-type=module", "-"],
        input=script, capture_output=True, text=True, env={**os.environ, "TZ": tz})
    assert proc.returncode == 0, proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@")]
    assert line, "the script printed no result:\n" + proc.stdout + proc.stderr
    return json.loads(line[-1][2:])


@node
def test_pages_merge_into_one_list_newest_first_without_repeats():
    got = run_js("""
        const p = (id, at) => ({ id, captured_at: at })
        out(m.mergePages([
          { photos: [p('b', '2026-09-01T10:00:00Z'), p('a', '2026-09-03T10:00:00Z')] },
          // CompanyCam's page order is not measured: a newer photo on page 2 still
          // comes first, and a photo repeated across pages is drawn once.
          { photos: [p('c', '2026-09-05T10:00:00Z'), p('a', '2026-09-03T10:00:00Z'),
                     p('d', null)] },
        ]).map((x) => x.id))
    """)
    assert got == ["c", "a", "b", "d"]


@node
def test_previous_and_next_stop_at_the_ends_and_next_loads_the_next_page():
    got = run_js("""
        out([m.neighbours(0, 3, false), m.neighbours(1, 3, false),
             m.neighbours(2, 3, false), m.neighbours(2, 3, true)])
    """)
    assert got == [
        {"prev": None, "next": 1, "needsMore": False},
        {"prev": 0, "next": 2, "needsMore": False},
        {"prev": 1, "next": None, "needsMore": False},      # no wrap to the oldest
        {"prev": 1, "next": 3, "needsMore": True},          # past the loaded page
    ]


@node
@pytest.mark.parametrize("tz", ["Europe/Berlin", "Asia/Tokyo", "America/Los_Angeles"])
def test_the_photo_date_is_written_in_the_accounts_zone_on_any_laptop(tz):
    got = run_js("""
        out([m.photoStamp('2026-09-13T13:17:00Z'), m.photoStamp('2026-01-13T13:17:00Z'),
             m.photoStamp(null), m.photoStamp('garbage')])
    """, tz=tz)
    assert got == ["Sep 13 2026, 9:17 AM (EDT)", "Jan 13 2026, 8:17 AM (EST)",
                   "Date unknown", "Date unknown"]


@node
def test_the_tab_says_why_there_are_no_photos():
    got = run_js("""
        out([m.tabMessage('unavailable', 0), m.tabMessage('off', 0), m.tabMessage('ok', 0),
             m.tabMessage('ok', 2), m.tabMessage(undefined, 0, true), m.tabMessage(undefined, 0),
             m.photoCount(1), m.photoCount(12), m.photoCount(null)])
    """)
    assert got == ["Photos are unavailable right now",
                   "CompanyCam is not connected on this deployment",
                   "No CompanyCam project is linked to this opportunity yet.",
                   None, "Photos are unavailable right now", None,
                   "1 photo", "12 photos", ""]


def test_the_sentences_match_the_backend():
    """Two copies of one sentence drift; this pins them together."""
    from app import companycam
    src = _read("lib", "companycam.ts")
    assert "export const UNAVAILABLE = '%s'" % companycam.UNAVAILABLE in src
    assert "export const OFF = '%s'" % companycam.OFF in src


# ---------------- source: the modal ----------------

def test_the_modal_has_a_photos_tab_for_every_role():
    modal = _read("components", "OpportunityDetail.tsx")
    nav = modal.split('aria-label="Opportunity sections"', 1)[1].split("</nav>", 1)[0]
    photos = nav.split('label="Photos"', 1)
    assert len(photos) == 2, "the Photos nav item is missing"
    # Not wrapped in a role check, unlike Notes: every role views photos.
    before = photos[0].rsplit("\n", 3)[-3:]
    assert not any("seesNotes" in ln or "canEdit" in ln for ln in before)
    assert "{tab === 'photos' && <PhotosTab opportunityId={o.id} />}" in modal
    assert "| 'photos'" in _read("lib", "opportunityModal.ts")


def test_the_tab_refreshes_pages_and_lazy_loads():
    tab = _read("components", "CompanyCamPhotos.tsx")
    assert "getOpportunityCompanyCam(opportunityId, true)" in tab, \
        "Refresh must ask the backend past its cache, not just refetch ours"
    assert "refreshNonce]" in tab and "pageParam === 1 && refreshNonce > 0" in tab
    assert 'loading="lazy"' in tab
    assert "useInfiniteQuery" in tab and "Load more photos" in tab
    assert "Open in CompanyCam" in tab and 'rel="noopener noreferrer"' in tab


def test_the_browser_only_ever_loads_images_from_the_crm():
    tab = _read("components", "CompanyCamPhotos.tsx")
    assert "src={photo.thumbnail_url}" in tab and "src={photo.image_url}" in tab
    for src in (tab, _read("lib", "companycam.ts"), _read("components", "CompanyCamSettings.tsx")):
        assert "companycam.com" not in src.replace("Open in CompanyCam", "")
        assert "uris" not in src


def test_the_viewer_has_every_control_the_owner_asked_for():
    tab = _read("components", "CompanyCamPhotos.tsx")
    viewer = tab.split("export function PhotoViewer(", 1)[1].split("\nfunction ", 1)[0]
    for needle in ('label="Previous photo"', 'label="Next photo"', "'ArrowLeft'", "'ArrowRight'",
                   "'Escape'", "photoStamp(photo.captured_at)",
                   "Photo by {textOf(photo.creator_name)", "{textOf(photo.description)}",
                   "Open in CompanyCam", "href={projectUrl}"):
        assert needle in viewer, needle


# ---------------- source: the contact panel and Settings ----------------

def test_the_contact_panel_section_only_draws_when_there_is_a_project():
    panel = _read("components", "ContactDetailsPanel.tsx")
    assert "<CompanyCamSection contactId={c.id} />" in panel
    section = panel.split("function CompanyCamSection(", 1)[1].split("\nfunction ", 1)[0]
    assert "data.projects.length === 0) return null" in section, \
        "a contact with no project must render exactly the measured panel"
    assert "<ProjectPhotosDialog project={project}" in section


def test_settings_shows_companycam_to_an_admin_only():
    settings = _read("pages", "SettingsPage.tsx")
    # One list of admin-only sections since My Staff joined CompanyCam there (2026-09-15).
    assert "(!ADMIN_SECTIONS.includes(s.key) || user.role === 'ADMIN') && (" in settings
    assert "'companycam'" in _read("lib", "settingsSections.ts").split("ADMIN_SECTIONS", 1)[1]
    assert "section === 'companycam' && user.role === 'ADMIN' ? <CompanyCamSettings />" in settings
    admin = _read("components", "CompanyCamSettings.tsx")
    for needle in ("Last error", "Last success", "Review", "Dismiss", "linkCompanyCamReview("):
        assert needle in admin


def test_api_helpers_were_appended_at_the_end():
    api = _read("lib", "api.ts")
    tail = api.rsplit("CompanyCam job photos (2026-09-14)", 1)
    assert len(tail) == 2
    for name in ("getOpportunityCompanyCam", "getCompanyCamPhotos", "getContactCompanyCam",
                 "getCompanyCamStatus", "listCompanyCamReview", "linkCompanyCamReview",
                 "dismissCompanyCamReview", "unlinkCompanyCamProject"):
        assert "export const %s" % name in tail[1] and name not in tail[0], name
