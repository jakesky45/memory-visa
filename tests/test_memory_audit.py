"""Provenance on results, and every use of memory in the audit trail (N2): run with
`make eval`."""
from __future__ import annotations

import datetime as dt

import pytest

from memory.grants import Grant, GrantStore
from memory.guarded_memory import GuardedMemory, PermissionError, UserClaims

from .fakes import FakeMemoryBackend, fake_apply_guardrail

T0 = dt.datetime(2026, 11, 21, 9, 0, tzinfo=dt.timezone.utc)
LEGAL = "org_robotinc_dept_legal"
MAYA = UserClaims(email="maya@robotinc.example", org="robotinc", departments=("engineering",))
LIAM = UserClaims(email="liam@robotinc.example", org="robotinc", departments=("legal",))


class Backend(FakeMemoryBackend):
    """Like AgentCore: a write returns an event ID, a result carries a record ID."""

    def put(self, namespace, text):
        super().put(namespace, text)
        return {"namespace": namespace, "eventId": f"ev-{len(self.records[namespace])}"}

    def retrieve(self, namespace, query):
        return [{"text": t, "record_id": f"mem-{i}", "score": 0.9} for i, t in enumerate(self.records.get(namespace, []), 1)]


@pytest.fixture
def setup():
    grants = GrantStore()
    memory = GuardedMemory(Backend(), fake_apply_guardrail, grants)
    memory.put(LIAM, LEGAL, "The X-7 is classified 7A994.", T0)
    return memory, grants


def _approved_grant(grants, hours=2):
    grants.request(
        Grant(
            grant_id="g-1",
            requester_namespace="org_robotinc_user_maya@robotinc.example",
            target_namespace=LEGAL,
            reason="Export review for the X-7.",
            requested_at=T0,
            duration=dt.timedelta(hours=hours),
        )
    )
    grants.approve("g-1", approved_by="liam@robotinc.example", now=T0)


def _last(grants):
    return grants.audit_log[-1]


def test_a_members_result_says_where_it_came_from(setup):
    memory, _ = setup
    result = memory.retrieve(LIAM, LEGAL, "X-7", T0)[0]
    assert result["namespace"] == LEGAL
    assert result["record_id"] == "mem-1"
    assert result["access"] == {"via": "membership"}


def test_a_visitors_result_names_the_grant_the_approver_and_the_end(setup):
    memory, grants = setup
    _approved_grant(grants)
    result = memory.retrieve(MAYA, LEGAL, "X-7", T0 + dt.timedelta(minutes=10))[0]
    assert result["access"] == {
        "via": "grant",
        "grant_id": "g-1",
        "approved_by": "liam@robotinc.example",
        "expires_at": (T0 + dt.timedelta(hours=2)).isoformat(),
    }


def test_a_read_under_a_grant_is_filed_under_that_grant(setup):
    """The owner can see what a visitor did with the grant: who, when, the question, which records."""
    memory, grants = setup
    _approved_grant(grants)
    memory.retrieve(MAYA, LEGAL, "X-7 classification", T0 + dt.timedelta(minutes=10))
    entry = _last(grants)
    assert entry["event"] == "read" and entry["grant_id"] == "g-1"
    assert entry["actor"] == "maya@robotinc.example"
    assert entry["query"] == "X-7 classification"
    assert entry["record_ids"] == ["mem-1"] and entry["records"] == 1


def test_a_refused_read_is_recorded_too(setup):
    memory, grants = setup
    with pytest.raises(PermissionError):
        memory.retrieve(MAYA, LEGAL, "X-7", T0)
    entry = _last(grants)
    assert entry["event"] == "read_refused"
    assert entry["grant_id"] == f"namespace#{LEGAL}"
    assert entry["actor"] == "maya@robotinc.example"
    assert entry["records"] == 0


def test_a_read_the_backend_refuses_is_recorded_under_the_grant(setup):
    """With a visa, the backend is AWS: it refuses when the window has closed."""
    memory, grants = setup
    _approved_grant(grants)

    def refuse(namespace, query):
        raise RuntimeError("AccessDeniedException")

    memory._memory.retrieve = refuse
    with pytest.raises(RuntimeError):
        memory.retrieve(MAYA, LEGAL, "X-7", T0 + dt.timedelta(minutes=10))
    entry = _last(grants)
    assert entry["event"] == "read_refused" and entry["grant_id"] == "g-1"
    assert "RuntimeError" in entry["detail"]


def test_no_audit_no_answer(setup):
    """If the read cannot be recorded, the caller gets nothing."""
    memory, grants = setup

    def broken(**kwargs):
        raise RuntimeError("audit table not reachable")

    grants.record_access = broken
    with pytest.raises(RuntimeError, match="audit table"):
        memory.retrieve(LIAM, LEGAL, "X-7", T0)


def test_a_write_is_recorded_with_its_author(setup):
    """The record does not carry its author, so the trail does."""
    _, grants = setup
    entry = grants.audit_log[0]
    assert entry["event"] == "write" and entry["actor"] == "liam@robotinc.example"
    assert entry["record_ids"] == ["ev-1"]


def test_a_visitors_write_is_refused_and_recorded_under_the_grant(setup):
    memory, grants = setup
    _approved_grant(grants)
    with pytest.raises(PermissionError):
        memory.put(MAYA, LEGAL, "The X-7 is EAR99.", T0 + dt.timedelta(minutes=10))
    entry = _last(grants)
    assert entry["event"] == "write_refused" and entry["grant_id"] == "g-1"


def test_a_write_the_guardrail_blocks_is_recorded_without_the_text(setup):
    memory, grants = setup

    def block(text):
        raise ValueError("blocked")

    memory._apply_guardrail = block
    with pytest.raises(ValueError):
        memory.put(LIAM, LEGAL, "-----BEGIN RSA PRIVATE KEY-----", T0)
    entry = _last(grants)
    assert entry["event"] == "write_refused"
    assert "PRIVATE KEY" not in str(entry)
