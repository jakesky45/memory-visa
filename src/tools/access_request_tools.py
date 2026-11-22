"""Lambda behind the AccessRequestTools Gateway Target - the tier-two MCP
tools: request_access, list_pending, approve_access, deny_access,
revoke_access, whoami.

This is what lets an approver act through Claude/Cursor directly ("approve
Maya's pending request") instead of a bespoke UI or a Slack app neither of
us has admin rights to stand up. One Lambda backs all six tools; the
Gateway puts the called tool's name on `context.client_context.custom`
under `bedrockAgentCoreToolName`, prefixed `{target}___{tool}` - the tool
*arguments* are the Lambda `event` itself, nothing more.

`whoami` is the odd one out: the other five act ON a grant; whoami reports
a caller's own effective access right now - static Memory namespaces, any
currently-active granted namespace, and the domain toolset that grant
carries (`agents.domain_config.allowed_tools`, the same grant-aware
function the eval suite already proves). It also states the one real
Gateway tool's static Cedar gating for contrast - the project's own
grant-aware tool model has nothing to do with whether that one actually
responds.

Grants live in DynamoDB here, not in-process, because each tools/call can
land on a separate, stateless Lambda invocation - unlike the orchestrator's
one long-running Runtime container, there's no shared Python process for an
in-memory GrantStore to survive in between calls.

Identity comes from `_verified_email`, injected by the IdentityInterceptor
Lambda ahead of this one (it decodes the caller's verified JWT and overwrites
whatever `_verified_email` the caller tried to assert) - never from a plain
`requester_email`/`approver_email`/`revoker_email`/`owner_email` argument.
A client-credentials (shared service) token carries no such claim, so calling
any of these tools with one now fails `UnverifiedCallerError` by design - the
whole point is that these tools only work for a real, individual person.
"""
from __future__ import annotations

import datetime as dt
import json
import os

from agents.access_requests import (
    NotDomainOwnerError,
    approve_request,
    deny_request,
    pending_requests_for_owner,
    request_cross_domain_access,
    revoke_request,
)
from agents.domain_config import allowed_tools
from memory.grants import GrantNotFoundError
from memory.grants_dynamodb import DynamoGrantStore
from memory.guarded_memory import UserClaims, requester_namespace, static_namespaces

_GRANTS_TABLE = os.environ["GRANTS_TABLE_NAME"]
_GRANTS_AUDIT_TABLE = os.environ["GRANTS_AUDIT_TABLE_NAME"]
_REGION = os.environ.get("AWS_REGION", "eu-west-2")

_grants = DynamoGrantStore(_GRANTS_TABLE, _GRANTS_AUDIT_TABLE, region=_REGION)


class UnverifiedCallerError(Exception):
    pass


def _tool_name(context) -> str:
    raw = context.client_context.custom["bedrockAgentCoreToolName"]
    # Auto-generated as "{TargetName}___{tool}" - only the part after the
    # separator is the actual tool name any of the dispatch below knows about.
    return raw.split("___", 1)[-1]


def _verified_email(args: dict) -> str:
    """The caller's own identity, as extracted from their verified JWT by the
    IdentityInterceptor - never the plain argument a caller could type anything into."""
    email = args.get("_verified_email")
    if not email:
        raise UnverifiedCallerError(
            "no verified identity on this call - request_access/list_pending/approve_access/"
            "deny_access/revoke_access need a real per-user token, not the shared service credential"
        )
    return email


def _grant_to_dict(grant) -> dict:
    return {
        "grant_id": grant.grant_id,
        "requester_namespace": grant.requester_namespace,
        "target_namespace": grant.target_namespace,
        "reason": grant.reason,
        "status": grant.status.value,
        "requested_at": grant.requested_at.isoformat(),
        "duration_hours": grant.duration.total_seconds() / 3600,
        "approved_by": grant.approved_by,
        "approved_at": grant.approved_at.isoformat() if grant.approved_at else None,
    }


def _request_access(args: dict) -> dict:
    requester = UserClaims(email=_verified_email(args), org=args["requester_org"])
    grant = request_cross_domain_access(
        _grants,
        requester,
        target_org=args["target_org"],
        target_department=args["target_department"],
        reason=args["reason"],
        duration=dt.timedelta(hours=float(args["duration_hours"])),
        now=dt.datetime.now(dt.timezone.utc),
    )
    return _grant_to_dict(grant)


def _list_pending(args: dict) -> dict:
    pending = pending_requests_for_owner(_grants, _verified_email(args))
    return {"pending": [_grant_to_dict(g) for g in pending]}


def _approve_access(args: dict) -> dict:
    grant = approve_request(_grants, args["grant_id"], _verified_email(args), now=dt.datetime.now(dt.timezone.utc))
    return _grant_to_dict(grant)


def _deny_access(args: dict) -> dict:
    grant = deny_request(_grants, args["grant_id"], _verified_email(args), now=dt.datetime.now(dt.timezone.utc))
    return _grant_to_dict(grant)


def _revoke_access(args: dict) -> dict:
    grant = revoke_request(_grants, args["grant_id"], _verified_email(args), now=dt.datetime.now(dt.timezone.utc))
    return _grant_to_dict(grant)


# EngineeringTools___check_shipping_classification is the one real tool
# behind a live Gateway Target today. STATIC_DOMAIN_POLICY (the Cedar forbid
# meant to gate it by department) is LOG_ONLY, not ACTIVE - confirmed live
# that promoting it to ACTIVE denies EVERYONE, including engineering callers,
# not just non-engineering ones. Root cause: the Gateway's CUSTOM_JWT
# authorizer only ever sees Cognito access tokens, which carry no custom
# attributes at all - principal.hasTag("department") can never be true, so
# the policy's "unless" exception can never fire. Reported honestly here
# rather than repeating the policy's original, untested intent. Not wired to
# GrantStore either way. Everything else in this response (memory
# namespaces, domain_tools) is computed live from the same
# GrantStore/allowed_tools() the rest of the project already proves.
def _live_gateway_tools() -> dict:
    return {
        "EngineeringTools___check_shipping_classification": (
            "callable by anyone right now - its gating policy is LOG_ONLY, not ACTIVE, "
            "and would deny everyone (not just other departments) if promoted, since "
            "the department tag it checks is never visible to Cedar on this auth path"
        ),
    }


def _whoami(args: dict) -> dict:
    email = _verified_email(args)
    org = args.get("_verified_org") or "unknown"
    department = args.get("_verified_department")
    claims = UserClaims(email=email, org=org, departments=(department,) if department else ())
    now = dt.datetime.now(dt.timezone.utc)
    granted = _grants.active_grants_for(requester_namespace(claims), now)

    return {
        "email": claims.email,
        "org": claims.org,
        "department": department,
        "memory": {
            "static_namespaces": sorted(static_namespaces(claims)),
            "granted_namespaces": [
                {"namespace": g.target_namespace, "expires_at": (g.approved_at + g.duration).isoformat()}
                for g in granted
            ],
        },
        "domain_tools": sorted(allowed_tools(claims, _grants, now)),
        "live_gateway_tools": _live_gateway_tools(),
    }


_DISPATCH = {
    "request_access": _request_access,
    "list_pending": _list_pending,
    "approve_access": _approve_access,
    "deny_access": _deny_access,
    "revoke_access": _revoke_access,
    "whoami": _whoami,
}


def handler(event, context):
    tool = _tool_name(context)
    try:
        fn = _DISPATCH[tool]
    except KeyError:
        return {"statusCode": 400, "body": json.dumps({"error": f"unknown tool '{tool}'"})}

    try:
        result = fn(event)
    except KeyError as exc:
        return {"statusCode": 400, "body": json.dumps({"error": f"missing required field: {exc}"})}
    except (NotDomainOwnerError, UnverifiedCallerError) as exc:
        return {"statusCode": 403, "body": json.dumps({"error": str(exc)})}
    except GrantNotFoundError as exc:
        return {"statusCode": 404, "body": json.dumps({"error": f"no such grant: {exc}"})}

    return {"statusCode": 200, "body": json.dumps(result)}
