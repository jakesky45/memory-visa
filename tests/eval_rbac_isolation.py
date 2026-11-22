"""The security proof: run with `make eval`.

Six properties, in order of how much they'd embarrass us if they failed:
cross-org isolation (the thing a multi-org client actually cares about),
within-org silo enforcement, legitimate cross-team flow still working,
credential scrubbing, controlled cross-domain switching for an employee, and
the same mechanism for a contractor whose default access is narrower still.
"""
from __future__ import annotations

import datetime as dt

import pytest

from agents.domain_config import allowed_tools
from memory.grants import Grant, GrantStore
from memory.guarded_memory import GuardedMemory, PermissionError, UserClaims

from .fakes import FakeMemoryBackend, fake_apply_guardrail

T0 = dt.datetime(2026, 11, 21, 9, 0, tzinfo=dt.timezone.utc)


def _memory() -> GuardedMemory:
    return GuardedMemory(FakeMemoryBackend(), fake_apply_guardrail, GrantStore())


def test_eval0_cross_org_isolation():
    """Same department label, different org - must never see each other."""
    memory = _memory()
    maya = UserClaims(email="maya@robotinc.example", org="robotinc", departments=("engineering",))
    sam = UserClaims(email="sam@helios.example", org="helios-biotech", departments=("engineering",))

    memory.put(maya, "org_robotinc_dept_engineering", "Atlas-v2 firmware uses a custom bootloader.", T0)

    with pytest.raises(PermissionError):
        memory.retrieve(sam, "org_robotinc_dept_engineering", "bootloader", T0)

    # Sam can read his own org's identically-named department with no issue -
    # the isolation is on org, never on the department label matching.
    memory.put(sam, "org_helios-biotech_dept_engineering", "Bioreactor firmware uses a custom bootloader.", T0)
    results = memory.retrieve(sam, "org_helios-biotech_dept_engineering", "bootloader", T0)
    assert any("Bioreactor" in r["text"] for r in results)


def test_eval1_silo_enforcement_within_org():
    memory = _memory()
    maya = UserClaims(email="maya@robotinc.example", org="robotinc", departments=("engineering",))
    priya = UserClaims(email="priya@robotinc.example", org="robotinc", departments=("finance",))

    memory.put(priya, "org_robotinc_dept_finance", "Q4 M&A target: shortlist of two battery suppliers.", T0)

    with pytest.raises(PermissionError):
        memory.retrieve(maya, "org_robotinc_dept_finance", "M&A target", T0)


def test_eval2_cross_team_flow_within_org():
    """Legal and Engineering share a project namespace - that's not a leak, it's the design."""
    memory = _memory()
    liam = UserClaims(email="liam@robotinc.example", org="robotinc", departments=("legal",), projects=("atlas-v2",))
    maya = UserClaims(email="maya@robotinc.example", org="robotinc", departments=("engineering",), projects=("atlas-v2",))

    memory.put(liam, "org_robotinc_proj_atlas-v2", "Supplier X is banned under export control 7A994.", T0)

    results = memory.retrieve(maya, "org_robotinc_proj_atlas-v2", "export control", T0)
    assert any("7A994" in r["text"] for r in results)


def test_eval3_credential_scrubbing():
    memory = _memory()
    maya = UserClaims(email="maya@robotinc.example", org="robotinc", departments=("engineering",))

    memory.put(maya, "org_robotinc_dept_engineering", "Here's the deploy key: AKIAABCDEFGHIJKLMNOP, don't lose it.", T0)

    results = memory.retrieve(maya, "org_robotinc_dept_engineering", "deploy key", T0)
    stored_text = results[0]["text"]
    assert "AKIAABCDEFGHIJKLMNOP" not in stored_text
    assert "[REDACTED]" in stored_text


def test_eval4_controlled_cross_domain_switching():
    """The headline feature, full lifecycle: deny, request, approve, use, expire, deny again.

    Robotinc ships the Atlas-v2. Its battery pack has an export-control
    classification - Legal's domain, not Engineering's. Maya needs it before
    the packaging manifest can be signed off and the shipment goes out.
    """
    grants = GrantStore()
    memory = GuardedMemory(FakeMemoryBackend(), fake_apply_guardrail, grants)
    maya = UserClaims(email="maya@robotinc.example", org="robotinc", departments=("engineering",))
    liam_namespace = "org_robotinc_dept_legal"

    memory.put(
        UserClaims(email="liam@robotinc.example", org="robotinc", departments=("legal",)),
        liam_namespace,
        "Export control 7A994 blocks Supplier X for battery cells.",
        T0,
    )

    # 1. No access yet - static claims don't cover Legal, and no grant exists.
    with pytest.raises(PermissionError):
        memory.retrieve(maya, liam_namespace, "export control", T0)
    assert "check_shipping_classification" in allowed_tools(maya, grants, T0)  # her own domain's tool, for contrast
    assert "search_contracts" not in allowed_tools(maya, grants, T0)  # Legal's tool, not available

    # 2. She requests access. Still pending - still no access.
    grant = grants.request(
        Grant(
            grant_id="g-1",
            requester_namespace="org_robotinc_user_maya@robotinc.example",
            target_namespace=liam_namespace,
            reason="Need export-control classification for the X-7 battery pack before the Q4 shipment.",
            requested_at=T0,
            duration=dt.timedelta(hours=2),
        )
    )
    with pytest.raises(PermissionError):
        memory.retrieve(maya, liam_namespace, "export control", T0)

    # 3. Liam approves it.
    t_approved = T0 + dt.timedelta(minutes=5)
    grants.approve(grant.grant_id, approved_by="liam@robotinc.example", now=t_approved)

    # 4. Within the window, both the memory namespace and Legal's toolset open up.
    t_in_window = t_approved + dt.timedelta(hours=1)
    results = memory.retrieve(maya, liam_namespace, "export control", t_in_window)
    assert any("7A994" in r["text"] for r in results)
    assert "search_contracts" in allowed_tools(maya, grants, t_in_window)

    # 5. Past the window, it reverts with no manual cleanup - same denial as step 1.
    t_expired = t_approved + dt.timedelta(hours=3)
    with pytest.raises(PermissionError):
        memory.retrieve(maya, liam_namespace, "export control", t_expired)
    assert "search_contracts" not in allowed_tools(maya, grants, t_expired)

    # 6. Every transition is in the audit log - nothing happened silently.
    events = [e["event"] for e in grants.audit_log]
    assert events == ["requested", "approved", "expired"]


def test_eval5_contractor_has_a_narrower_default_than_an_employee():
    """Jordan, an external contractor, joins Robotinc to help integrate a new
    arm module for the Atlas-v2. Scoped to just that one project - no
    department claim at all, which is deliberately narrower than Maya's
    default. Contractors should have the tightest defaults and the most
    audited exceptions, not the same baseline as full-time staff.
    """
    grants = GrantStore()
    memory = GuardedMemory(FakeMemoryBackend(), fake_apply_guardrail, grants)
    jordan = UserClaims(email="jordan@contractor.example", org="robotinc", projects=("atlas-v2-arm-firmware",))
    engineering_namespace = "org_robotinc_dept_engineering"

    memory.put(
        UserClaims(email="maya@robotinc.example", org="robotinc", departments=("engineering",)),
        engineering_namespace,
        "Atlas-v1 firmware exposes a legacy PWM interface later models dropped.",
        T0,
    )

    # 1. No department claim at all - not even Engineering's own tools by default.
    assert allowed_tools(jordan, grants, T0) == set()
    with pytest.raises(PermissionError):
        memory.retrieve(jordan, engineering_namespace, "legacy PWM interface", T0)

    # 2. Jordan requests access, scoped and reasoned, same as any employee would.
    grant = grants.request(
        Grant(
            grant_id="g-2",
            requester_namespace="org_robotinc_user_jordan@contractor.example",
            target_namespace=engineering_namespace,
            reason="Need Atlas-v1 firmware history for a backward-compatibility check on the new arm module.",
            requested_at=T0,
            duration=dt.timedelta(hours=4),
        )
    )

    # 3. Maya, Engineering's domain owner, approves it.
    t_approved = T0 + dt.timedelta(minutes=10)
    grants.approve(grant.grant_id, approved_by="maya@robotinc.example", now=t_approved)

    # 4. Within the window, Jordan gets Engineering's namespace and toolset -
    # a full visa for the domain, not a partial one, same as the employee case.
    t_in_window = t_approved + dt.timedelta(hours=2)
    results = memory.retrieve(jordan, engineering_namespace, "legacy PWM interface", t_in_window)
    assert any("PWM" in r["text"] for r in results)
    assert "check_shipping_classification" in allowed_tools(jordan, grants, t_in_window)

    # 5. Past the window, Jordan is back to having nothing by default.
    t_expired = t_approved + dt.timedelta(hours=5)
    with pytest.raises(PermissionError):
        memory.retrieve(jordan, engineering_namespace, "legacy PWM interface", t_expired)
    assert allowed_tools(jordan, grants, t_expired) == set()
