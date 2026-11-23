"""Published facts, the second way to share (N8): run with `make eval`.

A grant is the exception: one person, one namespace, a short time, a human approval.
A published fact is the rule: a domain owner says it once, and everyone in the org
can read it, with its source on it."""
from __future__ import annotations

import datetime as dt

import pytest

from agents.access_requests import InvalidRequestError, NotDomainOwnerError, publish_fact
from memory.grants import GrantStore
from memory.guarded_memory import GuardedMemory, PermissionError, UserClaims, published_namespace

from .fakes import FakeMemoryBackend, fake_apply_guardrail

T0 = dt.datetime(2026, 11, 21, 9, 0, tzinfo=dt.timezone.utc)
PUBLISHED = "org_robotinc_published"
FACT = "The X-7 battery pack is classified 7A994. Shipments to Supplier X need Legal's sign-off."

LIAM = UserClaims(email="liam@robotinc.example", org="robotinc", departments=("legal",))  # owns legal
MAYA = UserClaims(email="maya@robotinc.example", org="robotinc", departments=("engineering",))  # owns engineering
NOOR = UserClaims(email="noor@robotinc.example", org="robotinc", departments=("legal",))  # in legal, not its owner
SAM = UserClaims(email="sam@helios.example", org="helios-biotech", departments=("engineering",))


@pytest.fixture
def setup():
    grants = GrantStore()
    return GuardedMemory(FakeMemoryBackend(), fake_apply_guardrail, grants), grants


def test_the_owner_publishes_and_everyone_in_the_org_reads_it_with_no_grant(setup):
    memory, grants = setup
    publish_fact(memory, LIAM, "legal", FACT, T0)

    result = memory.retrieve(MAYA, PUBLISHED, "X-7", T0)[0]
    assert result["text"] == FACT, "stored as written: no model reworded it"
    assert result["access"] == {"via": "published"}
    assert grants.pending() == [], "no grant was asked for or needed"


def test_a_published_fact_carries_its_source(setup):
    memory, _ = setup
    publish_fact(memory, LIAM, "legal", FACT, T0)
    metadata = memory.retrieve(MAYA, PUBLISHED, "X-7", T0)[0]["metadata"]
    assert metadata == {"published_by": "liam@robotinc.example", "department": "legal", "published_at": T0.isoformat()}


def test_only_the_departments_owner_publishes_for_it(setup):
    memory, _ = setup
    with pytest.raises(NotDomainOwnerError):
        publish_fact(memory, NOOR, "legal", FACT, T0)  # in the department, not its owner
    with pytest.raises(NotDomainOwnerError):
        publish_fact(memory, MAYA, "legal", "The X-7 is EAR99.", T0)  # an owner, of a different department
    assert memory.retrieve(MAYA, PUBLISHED, "X-7", T0) == []


def test_the_guard_itself_refuses_a_caller_outside_the_department(setup):
    """Defence in depth: GuardedMemory.publish does not rely on the owner check alone."""
    memory, grants = setup
    with pytest.raises(PermissionError):
        memory.publish(MAYA, "legal", "The X-7 is EAR99.", T0)
    assert grants.audit_log[-1]["event"] == "publish_refused"


def test_nobody_writes_to_the_published_namespace_with_memory_write(setup):
    memory, _ = setup
    for person in (LIAM, MAYA):
        with pytest.raises(PermissionError):
            memory.put(person, PUBLISHED, "The X-7 is EAR99.", T0)


def test_another_org_cannot_read_it(setup):
    memory, grants = setup
    publish_fact(memory, LIAM, "legal", FACT, T0)
    with pytest.raises(PermissionError):
        memory.retrieve(SAM, PUBLISHED, "X-7", T0)
    assert memory.retrieve(SAM, published_namespace("helios-biotech"), "X-7", T0) == []
    assert grants.audit_log[-2]["event"] == "read_refused"


def test_a_published_secret_is_masked_first(setup):
    memory, _ = setup
    publish_fact(memory, LIAM, "legal", "Use key AKIAABCDEFGHIJKLMNOP for the export portal.", T0)
    assert "AKIA" not in memory.retrieve(MAYA, PUBLISHED, "key", T0)[0]["text"]


def test_publishing_and_reading_are_in_the_audit_trail(setup):
    memory, grants = setup
    publish_fact(memory, LIAM, "legal", FACT, T0)
    memory.retrieve(MAYA, PUBLISHED, "X-7", T0)
    trail = grants.audit_trail(f"namespace#{PUBLISHED}")
    assert [(e["event"], e["actor"]) for e in trail] == [("publish", "liam@robotinc.example"), ("read", "maya@robotinc.example")]
    assert trail[0]["record_ids"] == ["mem-pub-1"] and trail[1]["record_ids"] == ["mem-pub-1"]


def test_an_empty_fact_is_refused(setup):
    memory, _ = setup
    with pytest.raises(InvalidRequestError):
        publish_fact(memory, LIAM, "legal", "   ", T0)
