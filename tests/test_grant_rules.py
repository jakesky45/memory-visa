"""The rules a grant follows: run with `make eval`.

Each test is something that worked before these rules existed and should not have.
"""
from __future__ import annotations

import datetime as dt

import pytest

from agents.access_requests import (
    MAX_GRANT_DURATION,
    InvalidRequestError,
    approve_request,
    deny_request,
    request_cross_domain_access,
    revoke_request,
)
from memory.grants import GrantStatus, GrantStore, InvalidTransitionError
from memory.guarded_memory import GuardedMemory, PermissionError, UserClaims

from .fakes import FakeMemoryBackend, fake_apply_guardrail

T0 = dt.datetime(2026, 11, 21, 9, 0, tzinfo=dt.timezone.utc)
LEGAL = "org_robotinc_dept_legal"
LIAM = "liam@robotinc.example"
MAYA = UserClaims(email="maya@robotinc.example", org="robotinc", departments=("engineering",))


def _request(grants: GrantStore, hours: float = 2, reason: str = "Need the export-control classification."):
    return request_cross_domain_access(grants, MAYA, "robotinc", "legal", reason, dt.timedelta(hours=hours), T0)


def test_an_expired_grant_cannot_be_approved_again():
    """A new window needs a new request, with its own reason and its own audit trail."""
    grants = GrantStore()
    grant = _request(grants)
    approve_request(grants, grant.grant_id, LIAM, T0)
    after = T0 + dt.timedelta(hours=3)
    assert grants.active_grants_for(grant.requester_namespace, after) == []  # notices the expiry
    assert grants.get(grant.grant_id).status == GrantStatus.EXPIRED

    with pytest.raises(InvalidTransitionError):
        approve_request(grants, grant.grant_id, LIAM, after)
    assert grants.active_grants_for(grant.requester_namespace, after) == []


def test_a_denied_grant_cannot_be_approved():
    grants = GrantStore()
    grant = _request(grants)
    deny_request(grants, grant.grant_id, LIAM, T0)

    with pytest.raises(InvalidTransitionError):
        approve_request(grants, grant.grant_id, LIAM, T0)
    assert grants.get(grant.grant_id).status == GrantStatus.DENIED


def test_approving_twice_does_not_move_the_window():
    grants = GrantStore()
    grant = _request(grants)
    approve_request(grants, grant.grant_id, LIAM, T0)

    with pytest.raises(InvalidTransitionError):
        approve_request(grants, grant.grant_id, LIAM, T0 + dt.timedelta(hours=1))
    assert grants.get(grant.grant_id).approved_at == T0


def test_a_revoked_grant_stays_revoked():
    grants = GrantStore()
    grant = _request(grants)
    approve_request(grants, grant.grant_id, LIAM, T0)
    revoke_request(grants, grant.grant_id, LIAM, T0 + dt.timedelta(minutes=10))

    with pytest.raises(InvalidTransitionError):
        approve_request(grants, grant.grant_id, LIAM, T0 + dt.timedelta(minutes=20))
    assert grants.active_grants_for(grant.requester_namespace, T0 + dt.timedelta(minutes=20)) == []


def test_a_closed_window_cannot_be_revoked():
    """Nothing is left to pull back, and the audit trail should say expired, not revoked."""
    grants = GrantStore()
    grant = _request(grants)
    approve_request(grants, grant.grant_id, LIAM, T0)

    with pytest.raises(InvalidTransitionError):
        revoke_request(grants, grant.grant_id, LIAM, T0 + dt.timedelta(hours=3))


def test_a_request_needs_a_reason():
    with pytest.raises(InvalidRequestError):
        _request(GrantStore(), reason="   ")


def test_a_request_cannot_ask_for_more_than_the_longest_window():
    grants = GrantStore()
    with pytest.raises(InvalidRequestError):
        request_cross_domain_access(grants, MAYA, "robotinc", "legal", "r", MAX_GRANT_DURATION + dt.timedelta(seconds=1), T0)
    with pytest.raises(InvalidRequestError):
        request_cross_domain_access(grants, MAYA, "robotinc", "legal", "r", dt.timedelta(0), T0)
    assert grants.pending() == []


def test_a_grant_reads_but_never_writes():
    """A visitor who could write would put their words into the owner's memory."""
    grants = GrantStore()
    backend = FakeMemoryBackend()
    memory = GuardedMemory(backend, fake_apply_guardrail, grants)
    liam = UserClaims(email=LIAM, org="robotinc", departments=("legal",))
    memory.put(liam, LEGAL, "Export control 7A994 blocks Supplier X for battery cells.", T0)

    grant = _request(grants)
    approve_request(grants, grant.grant_id, LIAM, T0)
    in_window = T0 + dt.timedelta(minutes=30)

    assert memory.retrieve(MAYA, LEGAL, "export control", in_window)  # the grant reads
    with pytest.raises(PermissionError):
        memory.put(MAYA, LEGAL, "Supplier X is cleared for export.", in_window)
    assert backend.records[LEGAL] == ["Export control 7A994 blocks Supplier X for battery cells."]
