""""Only assigned data" and My Staff in the browser (2026-09-15): executed where it can be.

**Executed under node.** `lib/access.ts` is import-free, so the shipped code that decides
what a restricted user is shown — the sidebar, the landing view, the Opportunities tabs,
the technician's powers on their own job, and My Staff's self / last-admin locks — runs
here for real.

**Asserted against source.** The pages are React, which this project has no runner for;
each assertion names the failure it would catch. Both were also driven in a real
headless Chromium against a throwaway database — see `.qa/state/techaccess-done`.
"""
import json

from tests.test_opportunity_card_ui import _read, node, run_js

js = json.dumps

TESS = {"id": 3, "role": "TECH", "only_assigned_data": True}
TERRY = {"id": 4, "role": "TECH", "only_assigned_data": False}
RITA = {"id": 5, "role": "DISPATCHER", "only_assigned_data": True}
OWEN = {"id": 1, "role": "ADMIN", "only_assigned_data": True}  # ignored for an admin
NAV = ["dashboard", "conversations", "calendars", "contacts", "opportunities", "reporting"]


@node
def test_who_is_restricted_and_who_gets_the_technician_powers():
    got = run_js("access.ts", """
        const us = %s
        out(us.map((u) => [m.isRestricted(u), m.techOnOwnJob(u)]))
    """ % js([TESS, TERRY, RITA, OWEN, None]))
    assert got == [[True, True], [False, False], [True, False], [False, False], [False, False]]


@node
def test_dashboard_and_reporting_are_not_drawn_and_never_opened_for_a_restricted_user():
    got = run_js("access.ts", """
        const nav = %s
        out({
          tess: m.navKeys(%s, nav), terry: m.navKeys(%s, nav), owen: m.navKeys(%s, nav),
          land: [m.viewFor(%s, 'dashboard'), m.viewFor(%s, 'reporting'),
                 m.viewFor(%s, 'contacts'), m.viewFor(%s, 'dashboard')],
          tabs: [m.opportunityTabs(%s, ['Opportunities', 'Forecast', 'Pipelines', 'Bulk Actions']),
                 m.opportunityTabs(%s, ['Opportunities', 'Forecast'])],
        })
    """ % (js(NAV), js(TESS), js(TERRY), js(OWEN), js(TESS), js(RITA), js(TESS), js(TERRY),
           js(TESS), js(TERRY)))
    assert got["tess"] == ["conversations", "calendars", "contacts", "opportunities"]
    assert got["terry"] == NAV and got["owen"] == NAV
    assert got["land"] == ["opportunities", "opportunities", "contacts", "dashboard"]
    assert got["tabs"][0] == ["Opportunities", "Pipelines", "Bulk Actions"]
    assert got["tabs"][1] == ["Opportunities", "Forecast"]


@node
def test_my_staff_helpers_names_roles_defaults_and_paging():
    got = run_js("access.ts", """
        out({
          initials: [m.initials('Antonio Brown'), m.initials('owen'), m.initials('  ')],
          split: [m.splitName('Santiago Villa Hermosa'), m.splitName('Luis')],
          join: m.joinName(' Dana ', ' '),
          labels: ['ADMIN', 'DISPATCHER', 'TECH'].map(m.roleLabel),
          defaults: ['TECH', 'DISPATCHER', 'ADMIN'].map(m.defaultOnlyAssigned),
          page: m.pageOf([1,2,3,4,5,6,7,8,9,10,11,12], 2, 10),
          clamp: m.pageOf([1,2,3], 9, 10),
        })
    """)
    assert got["initials"] == ["AB", "OW", "?"]
    assert got["split"] == [{"first": "Santiago", "last": "Villa Hermosa"},
                            {"first": "Luis", "last": ""}]
    assert got["join"] == "Dana"
    assert got["labels"] == ["Admin", "Dispatcher", "Technician"]
    assert got["defaults"] == [True, False, False]
    assert got["page"] == {"rows": [11, 12], "pages": 2}
    assert got["clamp"] == {"rows": [1, 2, 3], "pages": 1}


@node
def test_my_staff_self_and_last_admin_locks_match_the_server():
    rows = [
        {"id": 1, "name": "Owen", "email": "o@x", "role": "ADMIN", "is_active": True},
        {"id": 2, "name": "Feed", "email": "f@x", "role": "ADMIN", "is_active": True,
         "machine": True},
        {"id": 3, "name": "Gone", "email": "g@x", "role": "ADMIN", "is_active": False},
        {"id": 4, "name": "Luis", "email": "l@x", "role": "TECH", "is_active": True},
    ]
    got = run_js("access.ts", """
        const rows = %s
        const two = rows.concat([
          { id: 5, name: 'Second', email: 's@x', role: 'ADMIN', is_active: true }])
        out({
          selfDeactivate: m.deactivateBlock(rows[0], 1, rows),
          lastAdmin: m.deactivateBlock(rows[0], 4, rows),
          lastAdminRole: m.roleBlock(rows[0], 4, rows),
          selfRole: m.roleBlock(rows[0], 1, two),
          tech: m.deactivateBlock(rows[3], 1, rows),
          machine: m.deactivateBlock(rows[1], 1, rows),
          withTwo: [m.deactivateBlock(two[4], 1, two), m.roleBlock(two[4], 1, two)],
        })
    """ % js(rows))
    assert got["selfDeactivate"] == "You cannot deactivate your own account"
    assert "last active admin" in got["lastAdmin"]
    assert "last active admin" in got["lastAdminRole"]
    assert got["selfRole"] == "You cannot change your own role away from admin"
    assert got["tech"] is None and got["machine"] is None
    assert got["withTwo"] == [None, None]


# ---------------- asserted against source ----------------

def test_the_shell_never_renders_the_app_behind_a_pending_password_change():
    """Catches: the change-password screen drawn as an overlay, with the app (and its
    requests) mounted underneath."""
    src = _read("App.tsx")
    gate = src.index("if (user.must_change_password)")
    assert gate < src.index("<SoftphoneProvider>"), "the gate must return before the shell"
    assert "<ChangePasswordScreen" in src[gate:gate + 200]
    assert "const view = viewFor(user, active)" in src
    assert "active={view}" in src and "view === 'dashboard'" in src


def test_the_sidebar_and_the_opportunities_tabs_use_the_shared_rule():
    assert "navKeys(user, PRIMARY.map((p) => p.key))" in _read("components", "Sidebar.tsx")
    assert "opportunityTabs(user, TABS)" in _read("pages", "OpportunitiesPage.tsx")


def test_the_modal_opens_answers_and_stage_to_a_technician_on_their_own_job_only():
    """Catches: the Checklist and Stage left dead for a technician (they could not do what
    the owner allowed), or the title/value/owner left live (a form that 403s)."""
    src = _read("components", "OpportunityDetail.tsx")
    assert "const techJob = techOnOwnJob(user)" in src
    assert "disabled={!canStage}" in src
    assert src.count("disabled={!canAnswer}") == 3, "fieldset + both answer lists"
    assert "<fieldset disabled={!canAnswer}" in src
    for field in ('aria-label="Opportunity name"', 'aria-label="Value" placeholder="0"',
                  'aria-label="Business name"', 'aria-label="Source"'):
        at = src.index(field)
        assert "disabled={!canEdit}" in src[at - 120:at + 200], field
    assert 'ariaLabel="Owner" disabled={!canEdit}' in src
    assert 'ariaLabel="Pipeline" disabled={!canEdit}' in src
    assert 'ariaLabel="Status" disabled={!canEdit}' in src
    assert "canDelete = user.role === 'ADMIN'" in src
    assert "A technician can tick this, not change the contact\u2019s email" in src
    assert "<NotesTab opportunityId={o.id} user={user} />" in src


def test_notes_and_tasks_offer_a_technician_add_and_complete_but_never_edit_or_delete():
    notes = _read("components", "opportunity", "NotesTab.tsx")
    assert "const canChange = user.role !== 'TECH'" in notes
    assert "row.kind === 'deal' && canChange ? () =>" in notes
    tasks = _read("components", "opportunity", "TasksTab.tsx")
    assert "const canAdd = canWrite || techJob" in tasks
    assert "t.assigned_user_id === user.id || t.created_by_id === user.id" in tasks
    assert "{canWrite && (\n            <KebabMenu" in tasks, "edit/delete stay STAFF"


def test_my_staff_is_admin_only_and_its_trash_icon_deactivates():
    settings = _read("pages", "SettingsPage.tsx")
    assert "section === 'my-staff' && user.role === 'ADMIN' ? <MyStaffPanel" in settings
    assert "!ADMIN_SECTIONS.includes(s.key) || user.role === 'ADMIN'" in settings
    sections = _read("lib", "settingsSections.ts")
    assert "'my-staff'" in sections and "ADMIN_SECTIONS" in sections
    panel = _read("components", "MyStaffPanel.tsx")
    assert "patchStaffUser(row.id, { is_active: active })" in panel
    assert "deleteUser" not in panel and "'DELETE'" not in panel, "never a hard delete"
    assert 'placeholder="name, email, phone, ids"' in panel
    assert "listStaff({ q, role })" in panel
    modal = _read("components", "StaffUserModal.tsx")
    assert "if (!switchTouched) setRestricted(defaultOnlyAssigned(e.target.value))" in modal
    assert 'role="switch"' in modal and 'aria-label="Only assigned data"' in modal


def test_the_api_helpers_were_appended_at_the_end_of_api_ts():
    api = _read("lib", "api.ts")
    tail = api[api.index("// ---------------- My Staff (2026-09-15) ----------------"):]
    for name in ("listStaff", "createStaffUser", "patchStaffUser"):
        assert "export const %s" % name in tail
    assert "export const unlinkCompanyCamProject" not in tail, "My Staff is the LAST block"
