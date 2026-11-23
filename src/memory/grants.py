"""Just-in-time, time-boxed cross-domain access grants.

The headline idea: static org/dept/project isolation is the floor, not the
ceiling. A user can request temporary read access into another domain's
memory namespace; once approved, it's active for a fixed window and then
reverts automatically, with no manual cleanup and a full audit trail of who
requested, who approved, and when it expired.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum


class GrantStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    REVOKED = "revoked"
    EXPIRED = "expired"


@dataclass
class Grant:
    grant_id: str
    requester_namespace: str
    target_namespace: str
    reason: str
    requested_at: dt.datetime
    duration: dt.timedelta
    status: GrantStatus = GrantStatus.PENDING
    approved_by: str | None = None
    approved_at: dt.datetime | None = None
    # Whether the grant also carries the target domain's tools. Off unless the request
    # asks for it, so the owner approves "read Legal's memory" and "use Legal's tools"
    # as two different things.
    include_tools: bool = False

    def is_active(self, now: dt.datetime) -> bool:
        if self.status != GrantStatus.APPROVED or self.approved_at is None:
            return False
        return now < self.approved_at + self.duration

    def is_expired(self, now: dt.datetime) -> bool:
        return (
            self.status == GrantStatus.APPROVED
            and self.approved_at is not None
            and now >= self.approved_at + self.duration
        )


class GrantNotFoundError(Exception):
    pass


class InvalidTransitionError(Exception):
    """The grant is not in a state this action may be taken from."""


# A grant moves forward only. Each decision is made once: an expired, denied or revoked
# grant is finished, and a new window needs a new request with its own reason.
#
#   pending  -> approved | denied | revoked
#   approved -> revoked (while the window is open) | expired
_FROM = {
    "approve": {GrantStatus.PENDING},
    "deny": {GrantStatus.PENDING},
    "revoke": {GrantStatus.PENDING, GrantStatus.APPROVED},
}


def check_transition(grant: Grant, action: str, now: dt.datetime) -> None:
    """Raise InvalidTransitionError unless `action` may be taken on `grant` now."""
    if grant.status not in _FROM[action]:
        raise InvalidTransitionError(f"cannot {action} grant {grant.grant_id}: it is {grant.status.value}")
    if action == "revoke" and grant.is_expired(now):
        raise InvalidTransitionError(f"cannot revoke grant {grant.grant_id}: its window has already closed")


class GrantStore:
    """Grant storage and the audit trail of every state transition.

    In-memory for the demo; swap `_grants`/`_audit_log` for a real table
    (Postgres alongside the pgvector store, or DynamoDB) without changing
    the interface this class exposes.
    """

    def __init__(self) -> None:
        self._grants: dict[str, Grant] = {}
        self.audit_log: list[dict] = []

    def _record(self, event: str, grant: Grant, now: dt.datetime) -> None:
        self.audit_log.append(
            {
                "event": event,
                "grant_id": grant.grant_id,
                "requester_namespace": grant.requester_namespace,
                "target_namespace": grant.target_namespace,
                "status": grant.status.value,
                "at": now.isoformat(),
            }
        )

    def request(self, grant: Grant) -> Grant:
        self._grants[grant.grant_id] = grant
        self._record("requested", grant, grant.requested_at)
        return grant

    def approve(self, grant_id: str, approved_by: str, now: dt.datetime) -> Grant:
        grant = self._get(grant_id)
        check_transition(grant, "approve", now)
        grant.status = GrantStatus.APPROVED
        grant.approved_by = approved_by
        grant.approved_at = now
        self._record("approved", grant, now)
        return grant

    def deny(self, grant_id: str, denied_by: str, now: dt.datetime) -> Grant:
        grant = self._get(grant_id)
        check_transition(grant, "deny", now)
        grant.status = GrantStatus.DENIED
        grant.approved_by = denied_by
        self._record("denied", grant, now)
        return grant

    def revoke(self, grant_id: str, revoked_by: str, now: dt.datetime) -> Grant:
        grant = self._get(grant_id)
        check_transition(grant, "revoke", now)
        grant.status = GrantStatus.REVOKED
        grant.approved_by = revoked_by
        self._record("revoked", grant, now)
        return grant

    def active_grants_for(self, requester_namespace: str, now: dt.datetime) -> list[Grant]:
        active = []
        for grant in self._grants.values():
            if grant.requester_namespace != requester_namespace:
                continue
            if grant.is_active(now):
                active.append(grant)
            elif grant.is_expired(now) and grant.status == GrantStatus.APPROVED:
                # Lazily transition to EXPIRED the first time anyone checks
                # after the window closed - no cron job needed to "notice" an
                # expiry, nothing stays silently active, and this only fires
                # once per grant rather than on every subsequent check.
                grant.status = GrantStatus.EXPIRED
                self._record("expired", grant, now)

        return active

    def get(self, grant_id: str) -> Grant:
        return self._get(grant_id)

    def pending(self) -> list[Grant]:
        return [g for g in self._grants.values() if g.status == GrantStatus.PENDING]

    def _get(self, grant_id: str) -> Grant:
        try:
            return self._grants[grant_id]
        except KeyError:
            raise GrantNotFoundError(grant_id) from None
