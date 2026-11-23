"""The visa as a credential: run with `make eval`.

Offline, like the eval suite: a fake STS records what was asked of it. What these
prove is the shape of the request - one namespace, one end time, the grant in the
session name. That AWS then refuses everything else is proved live, by
demo/visa_credentials_demo.py.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from memory.grants import Grant, GrantStore
from memory.guarded_memory import GuardedMemory, PermissionError, UserClaims
from memory.visa_credentials import (
    DEFAULT_STATIC_WINDOW,
    MAX_SESSION_SECONDS,
    MIN_SESSION_SECONDS,
    READ_ACTIONS,
    VisaIssuer,
    session_policy,
)

from .fakes import fake_apply_guardrail

T0 = dt.datetime(2026, 11, 21, 9, 0, tzinfo=dt.timezone.utc)
MEMORY_ARN = "arn:aws:bedrock-agentcore:eu-west-2:111111111111:memory/memory_visa-abc123"
ROLE_ARN = "arn:aws:iam::111111111111:role/memory-visa-reader"
LEGAL = "org_robotinc_dept_legal"


class FakeSts:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def assume_role(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return {"Credentials": {"AccessKeyId": "ASIAFAKE", "SecretAccessKey": "secret", "SessionToken": "token"}}


class ScopedFakeBackend:
    """A backend with retrieve_scoped: records the scope it was asked to read with."""

    def __init__(self) -> None:
        self.records: dict[str, list[str]] = {}
        self.scoped_reads: list[dict] = []

    def put(self, namespace: str, text: str) -> dict:
        self.records.setdefault(namespace, []).append(text)
        return {"namespace": namespace}

    def retrieve(self, namespace: str, query: str) -> list[dict]:
        raise AssertionError("a backend with retrieve_scoped must never be read unscoped")

    def retrieve_scoped(self, namespace, query, *, not_after, now, requester, grant_id) -> list[dict]:
        self.scoped_reads.append({"namespace": namespace, "not_after": not_after, "requester": requester, "grant_id": grant_id})
        return [{"text": t} for t in self.records.get(namespace, [])]


def test_policy_allows_one_namespace_until_one_moment():
    not_after = T0 + dt.timedelta(hours=2)
    statement = session_policy(MEMORY_ARN, LEGAL, not_after)["Statement"]

    assert len(statement) == 1, "one Allow and nothing else: a session policy can only narrow the role"
    only = statement[0]
    assert only["Effect"] == "Allow"
    assert only["Resource"] == MEMORY_ARN
    assert set(only["Action"]) == set(READ_ACTIONS)
    assert not any("Create" in a or "Delete" in a or "Update" in a for a in only["Action"]), "a visa never writes"
    assert only["Condition"]["DateLessThan"]["aws:CurrentTime"] == "2026-11-21T11:00:00Z"

    # The namespace as sent, and as AgentCore stores it. Both are the one namespace.
    assert only["Condition"]["StringEquals"]["bedrock-agentcore:namespace"] == [LEGAL, f"/{LEGAL}/"]


def test_policy_has_no_wildcards():
    text = json.dumps(session_policy(MEMORY_ARN, LEGAL, T0 + dt.timedelta(hours=1)))
    assert "*" not in text


def test_a_path_namespace_is_listed_once():
    statement = session_policy(MEMORY_ARN, "/org/robotinc/dept/legal/", T0 + dt.timedelta(hours=1))["Statement"][0]
    assert statement["Condition"]["StringEquals"]["bedrock-agentcore:namespace"] == ["/org/robotinc/dept/legal/"]


def test_the_end_time_must_carry_a_timezone():
    with pytest.raises(ValueError):
        session_policy(MEMORY_ARN, LEGAL, dt.datetime(2026, 11, 21, 11, 0))


def test_issue_asks_sts_for_a_narrowed_session_that_names_the_grant():
    sts = FakeSts()
    issuer = VisaIssuer(ROLE_ARN, MEMORY_ARN, sts_client=sts)

    visa = issuer.issue(LEGAL, T0 + dt.timedelta(hours=2), T0, requester="maya@robotinc.example", grant_id="g-1")

    call = sts.calls[0]
    assert call["RoleArn"] == ROLE_ARN
    assert call["RoleSessionName"] == "visa-g-1"
    assert json.loads(call["Policy"]) == visa.policy
    assert {t["Key"]: t["Value"] for t in call["Tags"]} == {"grant_id": "g-1", "requester": "maya@robotinc.example"}
    assert len(call["Policy"]) < 2048, "STS limits an inline session policy to 2,048 characters"


def test_a_short_grant_still_ends_on_time():
    """STS credentials live at least 15 minutes. A 2-minute visa ends at 2 minutes anyway,
    because the policy's time condition is what ends it - not the credential's own life."""
    sts = FakeSts()
    visa = VisaIssuer(ROLE_ARN, MEMORY_ARN, sts_client=sts).issue(
        LEGAL, T0 + dt.timedelta(minutes=2), T0, requester="maya@robotinc.example", grant_id="g-1"
    )

    assert sts.calls[0]["DurationSeconds"] == MIN_SESSION_SECONDS
    assert visa.policy["Statement"][0]["Condition"]["DateLessThan"]["aws:CurrentTime"] == "2026-11-21T09:02:00Z"


def test_a_long_grant_is_capped_by_sts_and_ended_by_the_policy():
    sts = FakeSts()
    VisaIssuer(ROLE_ARN, MEMORY_ARN, sts_client=sts).issue(
        LEGAL, T0 + dt.timedelta(hours=8), T0, requester="maya@robotinc.example", grant_id="g-1"
    )
    assert sts.calls[0]["DurationSeconds"] == MAX_SESSION_SECONDS


def test_no_visa_for_a_grant_that_has_ended():
    issuer = VisaIssuer(ROLE_ARN, MEMORY_ARN, sts_client=FakeSts())
    with pytest.raises(ValueError):
        issuer.issue(LEGAL, T0, T0 + dt.timedelta(seconds=1), requester="maya@robotinc.example", grant_id="g-1")


def test_a_read_under_a_grant_is_scoped_to_the_grant():
    """GuardedMemory hands the backend the grant's own end time and ID."""
    grants = GrantStore()
    backend = ScopedFakeBackend()
    memory = GuardedMemory(backend, fake_apply_guardrail, grants)
    maya = UserClaims(email="maya@robotinc.example", org="robotinc", departments=("engineering",))
    oleg = UserClaims(email="oleg@robotinc.example", org="robotinc", departments=("legal",))
    memory.put(oleg, LEGAL, "Export control 7A994 blocks Supplier X for battery cells.", T0)

    grants.request(
        Grant(
            grant_id="g-1",
            requester_namespace="org_robotinc_user_maya@robotinc.example",
            target_namespace=LEGAL,
            reason="Need the export-control classification before the shipment.",
            requested_at=T0,
            duration=dt.timedelta(hours=2),
        )
    )
    t_approved = T0 + dt.timedelta(minutes=5)
    grants.approve("g-1", approved_by="oleg@robotinc.example", now=t_approved)

    results = memory.retrieve(maya, LEGAL, "export control", t_approved + dt.timedelta(minutes=30))

    assert any("7A994" in r["text"] for r in results)
    assert backend.scoped_reads == [
        {
            "namespace": LEGAL,
            "not_after": t_approved + dt.timedelta(hours=2),
            "requester": "maya@robotinc.example",
            "grant_id": "g-1",
        }
    ]


def test_a_read_of_your_own_namespace_gets_a_short_window_and_no_grant():
    backend = ScopedFakeBackend()
    memory = GuardedMemory(backend, fake_apply_guardrail, GrantStore())
    oleg = UserClaims(email="oleg@robotinc.example", org="robotinc", departments=("legal",))

    memory.retrieve(oleg, LEGAL, "anything", T0)

    assert backend.scoped_reads[0]["grant_id"] is None
    assert backend.scoped_reads[0]["not_after"] == T0 + DEFAULT_STATIC_WINDOW


def test_no_grant_means_no_credentials_are_ever_asked_for():
    backend = ScopedFakeBackend()
    memory = GuardedMemory(backend, fake_apply_guardrail, GrantStore())
    maya = UserClaims(email="maya@robotinc.example", org="robotinc", departments=("engineering",))

    with pytest.raises(PermissionError):
        memory.retrieve(maya, LEGAL, "export control", T0)
    assert backend.scoped_reads == []
