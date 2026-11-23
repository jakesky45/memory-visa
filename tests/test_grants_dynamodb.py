"""The DynamoDB grant store: run with `make eval`.

Needs boto3 to import the module. No AWS call is made: the two tables are replaced
with a small in-memory table that understands the one condition the store uses.
"""
from __future__ import annotations

import copy
import datetime as dt

import pytest

pytest.importorskip("boto3")

from botocore.exceptions import ClientError  # noqa: E402

from memory.grants import Grant, GrantNotFoundError, GrantStatus, InvalidTransitionError  # noqa: E402
from memory.grants_dynamodb import DynamoGrantStore  # noqa: E402

T0 = dt.datetime(2026, 11, 21, 9, 0, tzinfo=dt.timezone.utc)
MAYA_NS = "org_robotinc_user_maya@robotinc.example"
LEGAL = "org_robotinc_dept_legal"


class FakeTable:
    """put_item / get_item / scan / query, with `Attr(name).eq(value)` as a write condition."""

    def __init__(self, key: str, sort_key: str | None = None) -> None:
        self._key, self._sort_key = key, sort_key
        self.items: dict[tuple, dict] = {}

    def _id(self, item: dict) -> tuple:
        return (item[self._key], item.get(self._sort_key) if self._sort_key else None)

    def put_item(self, Item: dict, ConditionExpression=None) -> None:
        if ConditionExpression is not None:
            attribute, expected = ConditionExpression.get_expression()["values"]
            current = self.items.get(self._id(Item))
            if current is None or current.get(attribute.name) != expected:
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "failed"}}, "PutItem")
        self.items[self._id(Item)] = copy.deepcopy(Item)

    def get_item(self, Key: dict) -> dict:
        item = self.items.get((Key[self._key], None))
        return {"Item": copy.deepcopy(item)} if item else {}

    def scan(self) -> dict:
        return {"Items": [copy.deepcopy(i) for i in self.items.values()]}

    def query(self, KeyConditionExpression) -> dict:
        attribute, value = KeyConditionExpression.get_expression()["values"]
        return {"Items": [copy.deepcopy(i) for i in self.items.values() if i[attribute.name] == value]}


@pytest.fixture
def store() -> DynamoGrantStore:
    store = object.__new__(DynamoGrantStore)  # no boto3 resource: the tables are fakes
    store._table = FakeTable("grant_id")
    store._audit_table = FakeTable("grant_id", "at")
    return store


def a_grant(grant_id: str = "g-1", hours: float = 2, include_tools: bool = False) -> Grant:
    return Grant(
        grant_id=grant_id,
        requester_namespace=MAYA_NS,
        target_namespace=LEGAL,
        reason="Need the classification.",
        requested_at=T0,
        duration=dt.timedelta(hours=hours),
        include_tools=include_tools,
    )


def test_a_grant_survives_the_round_trip_to_the_table(store):
    store.request(a_grant(include_tools=True))
    store.approve("g-1", "liam@robotinc.example", T0 + dt.timedelta(minutes=5))

    grant = store.get("g-1")
    assert grant.status == GrantStatus.APPROVED
    assert grant.approved_at == T0 + dt.timedelta(minutes=5)
    assert grant.duration == dt.timedelta(hours=2)
    assert grant.include_tools is True


def test_a_grant_written_before_the_tools_field_existed_carries_no_tools(store):
    store.request(a_grant())
    del store._table.items[("g-1", None)]["include_tools"]
    assert store.get("g-1").include_tools is False


def test_the_lifecycle_and_its_audit_trail(store):
    store.request(a_grant())
    assert [g.grant_id for g in store.pending()] == ["g-1"]
    assert store.active_grants_for(MAYA_NS, T0) == []

    store.approve("g-1", "liam@robotinc.example", T0 + dt.timedelta(minutes=5))
    assert store.pending() == []
    assert [g.grant_id for g in store.active_grants_for(MAYA_NS, T0 + dt.timedelta(hours=1))] == ["g-1"]
    assert store.active_grants_for("org_robotinc_user_someone-else@robotinc.example", T0 + dt.timedelta(hours=1)) == []

    after = T0 + dt.timedelta(hours=3)
    assert store.active_grants_for(MAYA_NS, after) == []
    assert store.get("g-1").status == GrantStatus.EXPIRED
    assert store.active_grants_for(MAYA_NS, after + dt.timedelta(minutes=1)) == []  # noticed once, recorded once

    assert [e["event"] for e in store.audit_trail("g-1")] == ["requested", "approved", "expired"]


def test_a_finished_grant_cannot_be_decided_again(store):
    store.request(a_grant("g-denied"))
    store.deny("g-denied", "liam@robotinc.example", T0)
    with pytest.raises(InvalidTransitionError):
        store.approve("g-denied", "liam@robotinc.example", T0)

    store.request(a_grant("g-expired"))
    store.approve("g-expired", "liam@robotinc.example", T0)
    store.active_grants_for(MAYA_NS, T0 + dt.timedelta(hours=3))
    with pytest.raises(InvalidTransitionError):
        store.approve("g-expired", "liam@robotinc.example", T0 + dt.timedelta(hours=3))
    assert store.active_grants_for(MAYA_NS, T0 + dt.timedelta(hours=3)) == []


def test_two_decisions_that_race_cannot_both_win(store):
    """Liam approves while a second call, which read the grant as pending a moment
    earlier, tries to write its own decision. The write is conditional on the status
    that call read, so it fails instead of overwriting the approval."""
    store.request(a_grant())
    stale = store.get("g-1")  # the second call's view: still pending

    store.approve("g-1", "liam@robotinc.example", T0)

    stale.status = GrantStatus.DENIED
    with pytest.raises(InvalidTransitionError):
        store._transition(stale, GrantStatus.PENDING)
    assert store.get("g-1").status == GrantStatus.APPROVED


def test_a_missing_grant_is_reported_as_missing(store):
    with pytest.raises(GrantNotFoundError):
        store.approve("no-such-grant", "liam@robotinc.example", T0)
