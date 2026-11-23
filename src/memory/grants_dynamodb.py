"""DynamoDB-backed GrantStore, same method surface as grants.GrantStore.

Needed because separate Lambda invocations backing separate MCP tools can't
share the in-process Python state the orchestrator's GrantStore relies on -
each tools/call is potentially a cold, separate Lambda instance. This is
what lets Claude/Cursor call request_access/approve_access as real,
individually discoverable MCP tools rather than only through the
orchestrator's single bespoke endpoint.

Grant current-state lives in one table (one item per grant, overwritten on
every transition - fast to read). The audit trail lives in a second table,
append-only (partition key grant_id, sort key the event timestamp), mirroring
the in-memory GrantStore's audit_log list so the live deployment has the same
proof property the eval suite already has offline.
"""
from __future__ import annotations

import datetime as dt

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from .grants import Grant, GrantNotFoundError, GrantStatus, InvalidTransitionError, access_entry, check_transition


def _to_item(grant: Grant) -> dict:
    return {
        "grant_id": grant.grant_id,
        "requester_namespace": grant.requester_namespace,
        "target_namespace": grant.target_namespace,
        "reason": grant.reason,
        "requested_at": grant.requested_at.isoformat(),
        "duration_seconds": int(grant.duration.total_seconds()),
        "status": grant.status.value,
        "approved_by": grant.approved_by or "",
        "approved_at": grant.approved_at.isoformat() if grant.approved_at else "",
        "include_tools": grant.include_tools,
    }


def _from_item(item: dict) -> Grant:
    return Grant(
        grant_id=item["grant_id"],
        requester_namespace=item["requester_namespace"],
        target_namespace=item["target_namespace"],
        reason=item["reason"],
        requested_at=dt.datetime.fromisoformat(item["requested_at"]),
        duration=dt.timedelta(seconds=int(item["duration_seconds"])),
        status=GrantStatus(item["status"]),
        approved_by=item["approved_by"] or None,
        approved_at=dt.datetime.fromisoformat(item["approved_at"]) if item["approved_at"] else None,
        include_tools=bool(item.get("include_tools", False)),  # absent on grants made before the field existed
    )


class DynamoGrantStore:
    def __init__(self, table_name: str, audit_table_name: str, region: str = "eu-west-2") -> None:
        resource = boto3.resource("dynamodb", region_name=region)
        self._table = resource.Table(table_name)
        self._audit_table = resource.Table(audit_table_name)

    def _record(self, event: str, grant: Grant, now: dt.datetime) -> None:
        self._audit_table.put_item(
            Item={
                "grant_id": grant.grant_id,
                "at": now.isoformat(),
                "event": event,
                "requester_namespace": grant.requester_namespace,
                "target_namespace": grant.target_namespace,
                "status": grant.status.value,
                "actor": grant.approved_by or "",
            }
        )

    def record_access(
        self,
        *,
        actor: str,
        namespace: str,
        event: str,
        at: dt.datetime,
        grant_id: str | None = None,
        query: str | None = None,
        record_ids: tuple[str, ...] = (),
        detail: str = "",
    ) -> None:
        """One use of memory, allowed or refused, in the same trail as the grant's own
        transitions (REVIEW.md N2, M10). A read under a grant is filed under that grant,
        so the owner can see what a visitor did with it. Anything else is filed under
        the namespace."""
        self._audit_table.put_item(Item=access_entry(actor, namespace, event, at, grant_id, query, record_ids, detail))

    def _transition(self, grant: Grant, was: GrantStatus) -> None:
        """Write the grant only if its status is still what this caller read. Two calls
        that race (an approve and a deny, or two approves) cannot both succeed."""
        try:
            self._table.put_item(Item=_to_item(grant), ConditionExpression=Attr("status").eq(was.value))
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise InvalidTransitionError(f"grant {grant.grant_id} changed while this call was in progress") from None
            raise

    def request(self, grant: Grant) -> Grant:
        self._table.put_item(Item=_to_item(grant))
        self._record("requested", grant, grant.requested_at)
        return grant

    def approve(self, grant_id: str, approved_by: str, now: dt.datetime) -> Grant:
        grant = self.get(grant_id)
        check_transition(grant, "approve", now)
        was = grant.status
        grant.status = GrantStatus.APPROVED
        grant.approved_by = approved_by
        grant.approved_at = now
        self._transition(grant, was)
        self._record("approved", grant, now)
        return grant

    def deny(self, grant_id: str, denied_by: str, now: dt.datetime) -> Grant:
        grant = self.get(grant_id)
        check_transition(grant, "deny", now)
        was = grant.status
        grant.status = GrantStatus.DENIED
        grant.approved_by = denied_by
        self._transition(grant, was)
        self._record("denied", grant, now)
        return grant

    def revoke(self, grant_id: str, revoked_by: str, now: dt.datetime) -> Grant:
        grant = self.get(grant_id)
        check_transition(grant, "revoke", now)
        was = grant.status
        grant.status = GrantStatus.REVOKED
        grant.approved_by = revoked_by
        self._transition(grant, was)
        self._record("revoked", grant, now)
        return grant

    def audit_trail(self, grant_id: str) -> list[dict]:
        response = self._audit_table.query(KeyConditionExpression=Key("grant_id").eq(grant_id))
        return sorted(response.get("Items", []), key=lambda item: item["at"])

    def get(self, grant_id: str) -> Grant:
        response = self._table.get_item(Key={"grant_id": grant_id})
        item = response.get("Item")
        if item is None:
            raise GrantNotFoundError(grant_id)
        return _from_item(item)

    def pending(self) -> list[Grant]:
        response = self._table.scan()
        return [_from_item(i) for i in response.get("Items", []) if i["status"] == GrantStatus.PENDING.value]

    def active_grants_for(self, requester_namespace: str, now: dt.datetime) -> list[Grant]:
        response = self._table.scan()
        active = []
        for item in response.get("Items", []):
            grant = _from_item(item)
            if grant.requester_namespace != requester_namespace:
                continue
            if grant.is_active(now):
                active.append(grant)
            elif grant.is_expired(now) and grant.status == GrantStatus.APPROVED:
                grant.status = GrantStatus.EXPIRED
                self._table.put_item(Item=_to_item(grant))
                self._record("expired", grant, now)
        return active
