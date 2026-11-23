"""The conversational request/approve/deny surface.

Exposed as MCP tools so approval happens the same way everything else does -
a normal turn in whatever AI client the approver is already using, not a
separate app or Slack integration to stand up before a demo.
"""
from __future__ import annotations

import datetime as dt
import uuid

from memory.grants import Grant, GrantStore
from memory.guarded_memory import UserClaims, requester_namespace

from .domain_owners import owner_of


class NotDomainOwnerError(Exception):
    pass


class InvalidRequestError(Exception):
    pass


# The longest window a grant may ask for. A visa is for a task, not a posting: anything
# longer is a change to someone's standing access, which belongs in the identity provider.
MAX_GRANT_DURATION = dt.timedelta(hours=24)


def request_cross_domain_access(
    grants: GrantStore,
    requester: UserClaims,
    target_org: str,
    target_department: str,
    reason: str,
    duration: dt.timedelta,
    now: dt.datetime,
) -> Grant:
    """Called by the requester's agent after a PermissionError on that namespace."""
    if not reason or not reason.strip():
        raise InvalidRequestError("a request needs a reason: the owner approves the reason, not just the name")
    if duration <= dt.timedelta(0) or duration > MAX_GRANT_DURATION:
        hours = MAX_GRANT_DURATION.total_seconds() / 3600
        raise InvalidRequestError(f"duration must be more than zero and at most {hours:g} hours")
    target_namespace = f"org_{target_org}_dept_{target_department}"
    grant = Grant(
        grant_id=str(uuid.uuid4()),
        requester_namespace=requester_namespace(requester),
        target_namespace=target_namespace,
        reason=reason,
        requested_at=now,
        duration=duration,
    )
    return grants.request(grant)


def pending_requests_for_owner(grants: GrantStore, owner_email: str) -> list[Grant]:
    """What an owner's agent surfaces when they ask "what's waiting on me?"."""
    return [grant for grant in grants.pending() if _owner_matches(grant, owner_email)]


def _owner_matches(grant: Grant, owner_email: str) -> bool:
    # target_namespace is "org_{org}_dept_{dept}" - pull both segments back out
    # rather than assuming a format the caller already knows.
    try:
        _, org, _, department = grant.target_namespace.split("_", 3)
    except ValueError:
        return False
    return owner_of(org, department) == owner_email


def approve_request(grants: GrantStore, grant_id: str, approver_email: str, now: dt.datetime) -> Grant:
    grant = grants.get(grant_id)
    _, org, _, department = grant.target_namespace.split("_", 3)
    if owner_of(org, department) != approver_email:
        raise NotDomainOwnerError(
            f"{approver_email} does not own domain '{department}' in org '{org}'"
        )
    return grants.approve(grant_id, approved_by=approver_email, now=now)


def deny_request(grants: GrantStore, grant_id: str, approver_email: str, now: dt.datetime) -> Grant:
    grant = grants.get(grant_id)
    _, org, _, department = grant.target_namespace.split("_", 3)
    if owner_of(org, department) != approver_email:
        raise NotDomainOwnerError(
            f"{approver_email} does not own domain '{department}' in org '{org}'"
        )
    return grants.deny(grant_id, denied_by=approver_email, now=now)


def revoke_request(grants: GrantStore, grant_id: str, revoker_email: str, now: dt.datetime) -> Grant:
    """Pull back a grant before it would otherwise expire - the break-glass path for an
    active grant. Not a substitute for disabling someone's IdP account entirely when they
    leave; that's Cognito's job, not GrantStore's."""
    grant = grants.get(grant_id)
    _, org, _, department = grant.target_namespace.split("_", 3)
    if owner_of(org, department) != revoker_email:
        raise NotDomainOwnerError(
            f"{revoker_email} does not own domain '{department}' in org '{org}'"
        )
    return grants.revoke(grant_id, revoked_by=revoker_email, now=now)
