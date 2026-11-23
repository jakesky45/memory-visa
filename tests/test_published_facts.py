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

OLEG = UserClaims(email="oleg@robotinc.example", org="robotinc", departments=("legal",))  # owns legal
MAYA = UserClaims(email="maya@robotinc.example", org="robotinc", departments=("engineering",))  # owns engineering
NOOR = UserClaims(email="noor@robotinc.example", org="robotinc", departments=("legal",))  # in legal, not its owner
SAM = UserClaims(email="sam@helios.example", org="helios-biotech", departments=("engineering",))


@pytest.fixture
def setup():
    grants = GrantStore()
    return GuardedMemory(FakeMemoryBackend(), fake_apply_guardrail, grants), grants


def test_the_owner_publishes_and_everyone_in_the_org_reads_it_with_no_grant(setup):
    memory, grants = setup
    publish_fact(memory, OLEG, "legal", FACT, T0)

    result = memory.retrieve(MAYA, PUBLISHED, "X-7", T0)[0]
    assert result["text"] == FACT, "stored as written: no model reworded it"
    assert result["access"] == {"via": "published"}
    assert grants.pending() == [], "no grant was asked for or needed"


def test_a_published_fact_carries_its_source(setup):
    memory, _ = setup
    publish_fact(memory, OLEG, "legal", FACT, T0)
    metadata = memory.retrieve(MAYA, PUBLISHED, "X-7", T0)[0]["metadata"]
    assert metadata == {"published_by": "oleg@robotinc.example", "department": "legal", "published_at": T0.isoformat()}


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
    for person in (OLEG, MAYA):
        with pytest.raises(PermissionError):
            memory.put(person, PUBLISHED, "The X-7 is EAR99.", T0)


def test_another_org_cannot_read_it(setup):
    memory, grants = setup
    publish_fact(memory, OLEG, "legal", FACT, T0)
    with pytest.raises(PermissionError):
        memory.retrieve(SAM, PUBLISHED, "X-7", T0)
    assert memory.retrieve(SAM, published_namespace("helios-biotech"), "X-7", T0) == []
    assert grants.audit_log[-2]["event"] == "read_refused"


def test_a_published_secret_is_masked_first(setup):
    memory, _ = setup
    publish_fact(memory, OLEG, "legal", "Use key AKIAABCDEFGHIJKLMNOP for the export portal.", T0)
    assert "AKIA" not in memory.retrieve(MAYA, PUBLISHED, "key", T0)[0]["text"]


def test_publishing_and_reading_are_in_the_audit_trail(setup):
    memory, grants = setup
    publish_fact(memory, OLEG, "legal", FACT, T0)
    memory.retrieve(MAYA, PUBLISHED, "X-7", T0)
    trail = grants.audit_trail(f"namespace#{PUBLISHED}")
    assert [(e["event"], e["actor"]) for e in trail] == [("publish", "oleg@robotinc.example"), ("read", "maya@robotinc.example")]
    assert trail[0]["record_ids"] == ["mem-pub-1"] and trail[1]["record_ids"] == ["mem-pub-1"]


def test_an_empty_fact_is_refused(setup):
    memory, _ = setup
    with pytest.raises(InvalidRequestError):
        publish_fact(memory, OLEG, "legal", "   ", T0)


# --- search across every namespace the caller may read (N7) --------------------

from memory.grants import Grant  # noqa: E402

ENG = "org_robotinc_dept_engineering"
LEGAL = "org_robotinc_dept_legal"


def test_one_search_covers_own_department_published_and_nothing_else(setup):
    memory, _ = setup
    memory.put(MAYA, ENG, "The X-7 firmware is version 4.2.", T0)
    memory.put(OLEG, LEGAL, "The X-7 contract with Supplier X is under review.", T0)
    publish_fact(memory, OLEG, "legal", FACT, T0)

    found = memory.retrieve_all(MAYA, "X-7", T0)
    assert {r["namespace"] for r in found["records"]} == {ENG, PUBLISHED}
    assert LEGAL not in found["searched"], "Legal's own namespace is not searched: no grant"
    assert all("under review" not in r["text"] for r in found["records"])
    assert found["failed"] == []


def test_an_approved_grant_adds_its_namespace_and_the_end_of_the_grant_removes_it(setup):
    memory, grants = setup
    memory.put(OLEG, LEGAL, "The X-7 contract with Supplier X is under review.", T0)
    grants.request(
        Grant(
            grant_id="g-1",
            requester_namespace="org_robotinc_user_maya@robotinc.example",
            target_namespace=LEGAL,
            reason="Export review.",
            requested_at=T0,
            duration=dt.timedelta(hours=1),
        )
    )
    assert LEGAL not in memory.retrieve_all(MAYA, "X-7", T0)["searched"], "pending is not approved"
    grants.approve("g-1", approved_by="oleg@robotinc.example", now=T0)

    found = memory.retrieve_all(MAYA, "X-7", T0 + dt.timedelta(minutes=5))
    record = next(r for r in found["records"] if r["namespace"] == LEGAL)
    assert record["access"]["via"] == "grant" and record["access"]["grant_id"] == "g-1"

    assert LEGAL not in memory.retrieve_all(MAYA, "X-7", T0 + dt.timedelta(hours=2))["searched"]


def test_each_namespace_searched_is_its_own_audit_entry(setup):
    memory, grants = setup
    found = memory.retrieve_all(MAYA, "X-7", T0)
    reads = [e["target_namespace"] for e in grants.audit_log if e["event"] == "read"]
    assert reads == found["searched"] and len(reads) == 3  # her own, her department, published


def test_the_best_match_comes_first_whatever_its_namespace(setup):
    memory, _ = setup
    scores = {ENG: 0.4, PUBLISHED: 0.9}
    memory._memory.retrieve = lambda namespace, query: (
        [{"text": namespace, "score": scores[namespace]}] if namespace in scores else []
    )
    assert [r["namespace"] for r in memory.retrieve_all(MAYA, "X-7", T0)["records"]] == [PUBLISHED, ENG]


def test_one_namespace_failing_does_not_hide_the_others(setup):
    memory, _ = setup
    publish_fact(memory, OLEG, "legal", FACT, T0)
    real = memory._memory.retrieve

    def flaky(namespace, query):
        if namespace == ENG:
            raise RuntimeError("ThrottledException")
        return real(namespace, query)

    memory._memory.retrieve = flaky
    found = memory.retrieve_all(MAYA, "X-7", T0)
    assert found["failed"] == [{"namespace": ENG, "error": "RuntimeError"}]
    assert any(r["namespace"] == PUBLISHED for r in found["records"])
