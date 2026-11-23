"""Lambda behind the AccessRequestTools Gateway Target - the tier-two MCP
tools: request_access, list_pending, approve_access, deny_access,
revoke_access, whoami, memory_search, memory_write, grant_activity,
publish_fact.

This is what lets an approver act through Claude/Cursor directly ("approve
Maya's pending request") instead of a bespoke UI or a Slack app neither of
us has admin rights to stand up. One Lambda backs all ten tools; the
Gateway puts the called tool's name on `context.client_context.custom`
under `bedrockAgentCoreToolName`, prefixed `{target}___{tool}` - the tool
*arguments* are the Lambda `event` itself, nothing more.

memory_search/memory_write replace the orchestrator's old claims-body
`retrieve`/`put` actions (src/server.py, pre-M1): that path trusted an
explicit `claims` dict in the request body, so any caller could assert any
email/org/department and read or write anyone's namespace. These two tools
use the same verified-identity rule as the other six - `_verified_email`/
`_verified_org`/`_verified_department`, injected by IdentityInterceptor,
never a plain argument.

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
import decimal
import json
import os

import boto3

from agents.access_requests import (
    InvalidRequestError,
    NotDomainOwnerError,
    approve_request,
    deny_request,
    grant_activity,
    pending_requests_for_owner,
    publish_fact,
    request_cross_domain_access,
    revoke_request,
)
from agents.domain_config import allowed_tools
from memory.agentcore_backend import AgentCoreMemoryBackend
from memory.grants import GrantNotFoundError, InvalidTransitionError
from memory.grants_dynamodb import DynamoGrantStore
from memory.guardrail import ContentBlockedError, apply_guardrail
from memory.guarded_memory import (
    GuardedMemory,
    PermissionError as GuardedMemoryPermissionError,
    UserClaims,
    published_namespace,
    requester_namespace,
    static_namespaces,
)
from memory.visa_credentials import VisaIssuer

_GRANTS_TABLE = os.environ["GRANTS_TABLE_NAME"]
_GRANTS_AUDIT_TABLE = os.environ["GRANTS_AUDIT_TABLE_NAME"]
_REGION = os.environ.get("AWS_REGION", "eu-west-2")
_MEMORY_ID = os.environ.get("MEMORY_ID", "")
_MEMORY_ARN = os.environ.get("MEMORY_ARN", "")
_VISA_ROLE_ARN = os.environ.get("VISA_ROLE_ARN", "")
_GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID", "")
_GUARDRAIL_VERSION = os.environ.get("GUARDRAIL_VERSION", "DRAFT")
_MEMORY_STRATEGY_ID = os.environ.get("MEMORY_STRATEGY_ID") or None

_grants = DynamoGrantStore(_GRANTS_TABLE, _GRANTS_AUDIT_TABLE, region=_REGION)
_bedrock_runtime = boto3.client("bedrock-runtime", region_name=_REGION) if _GUARDRAIL_ID else None


def _apply_guardrail(text: str) -> str:
    """Every memory_write passes the Bedrock Guardrail first (memory/guardrail.py).
    A masked secret is stored masked; blocked content, or content that cannot be
    checked, is refused with ContentBlockedError."""
    return apply_guardrail(_bedrock_runtime, _GUARDRAIL_ID, _GUARDRAIL_VERSION, text)


# Reads go through a visa: credentials AWS limits to one namespace until the
# grant ends (memory/visa_credentials.py). Writes use this Lambda's own role
# (bedrock-agentcore:CreateEvent) - a grant never allows a write, so a write
# never needs the visa's narrower, namespace-scoped credentials.
_visa_issuer = VisaIssuer(_VISA_ROLE_ARN, _MEMORY_ARN) if _VISA_ROLE_ARN and _MEMORY_ARN else None
_memory = (
    GuardedMemory(
        AgentCoreMemoryBackend(_MEMORY_ID, _REGION, visa_issuer=_visa_issuer, strategy_id=_MEMORY_STRATEGY_ID),
        _apply_guardrail,
        _grants,
    )
    if _MEMORY_ID
    else None
)


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
        "include_tools": grant.include_tools,
    }


def _request_access(args: dict) -> dict:
    # The org is the caller's own, from the verified token - never an argument. A
    # requester_org argument, if a client still sends one, is ignored.
    org = args.get("_verified_org")
    if not org:
        raise UnverifiedCallerError("no verified org on this call - the caller's account has no org attribute")
    requester = UserClaims(email=_verified_email(args), org=org)
    grant = request_cross_domain_access(
        _grants,
        requester,
        target_org=args["target_org"],
        target_department=args["target_department"],
        reason=args["reason"],
        duration=dt.timedelta(hours=float(args["duration_hours"])),
        now=dt.datetime.now(dt.timezone.utc),
        include_tools=bool(args.get("include_tools", False)),
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
            # Read-only for every member of the org, no grant: facts a domain owner published.
            "published_namespace": published_namespace(claims.org),
            "granted_namespaces": [
                {"namespace": g.target_namespace, "expires_at": (g.approved_at + g.duration).isoformat()}
                for g in granted
            ],
        },
        "domain_tools": sorted(allowed_tools(claims, _grants, now)),
        "live_gateway_tools": _live_gateway_tools(),
    }


def _claims_from(args: dict) -> UserClaims:
    """UserClaims for memory_search/memory_write, built only from the
    caller's verified JWT attributes - same rule as every other tool here."""
    org = args.get("_verified_org")
    if not org:
        raise UnverifiedCallerError("no verified org on this call - the caller's account has no org attribute")
    department = args.get("_verified_department")
    return UserClaims(email=_verified_email(args), org=org, departments=(department,) if department else ())


def _log_usage(claims: UserClaims, action: str, namespace: str, query: str | None = None) -> None:
    # A log line for CloudWatch. The record that counts is in the audit table:
    # GuardedMemory writes every read and write there, allowed or refused, with the
    # grant ID and the record IDs (REVIEW.md N2, M10).
    entry = {
        "email": claims.email,
        "org": claims.org,
        "departments": list(claims.departments),
        "action": action,
        "namespace": namespace,
        "at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    if query is not None:
        entry["query"] = query
    print(json.dumps(entry))


def _memory_search(args: dict) -> dict:
    if _memory is None:
        raise RuntimeError("memory backend not configured - MEMORY_ID/MEMORY_ARN not set")
    claims = _claims_from(args)
    _log_usage(claims, "memory_search", args["namespace"], query=args["query"])
    records = _memory.retrieve(claims, args["namespace"], args["query"], dt.datetime.now(dt.timezone.utc))
    return {"records": records}


def _memory_write(args: dict) -> dict:
    if _memory is None:
        raise RuntimeError("memory backend not configured - MEMORY_ID/MEMORY_ARN not set")
    claims = _claims_from(args)
    _log_usage(claims, "memory_write", args["namespace"])
    result = _memory.put(claims, args["namespace"], args["text"], dt.datetime.now(dt.timezone.utc))
    return {"result": result}


def _plain(value):
    """DynamoDB returns numbers as Decimal, which JSON cannot carry."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def _publish_fact(args: dict) -> dict:
    if _memory is None:
        raise RuntimeError("memory backend not configured - MEMORY_ID/MEMORY_ARN not set")
    claims = _claims_from(args)
    _log_usage(claims, "publish_fact", published_namespace(claims.org))
    return {"published": publish_fact(_memory, claims, args["department"], args["text"], dt.datetime.now(dt.timezone.utc))}


def _grant_activity(args: dict) -> dict:
    trail = grant_activity(_grants, args["grant_id"], _verified_email(args))
    return {"grant_id": args["grant_id"], "activity": _plain(trail)}


_DISPATCH = {
    "request_access": _request_access,
    "list_pending": _list_pending,
    "approve_access": _approve_access,
    "deny_access": _deny_access,
    "revoke_access": _revoke_access,
    "whoami": _whoami,
    "memory_search": _memory_search,
    "memory_write": _memory_write,
    "grant_activity": _grant_activity,
    "publish_fact": _publish_fact,
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
    except InvalidRequestError as exc:
        return {"statusCode": 400, "body": json.dumps({"error": str(exc)})}
    except InvalidTransitionError as exc:
        return {"statusCode": 409, "body": json.dumps({"error": str(exc)})}
    except ContentBlockedError as exc:
        return {"statusCode": 422, "body": json.dumps({"error": str(exc)})}
    except (NotDomainOwnerError, UnverifiedCallerError, GuardedMemoryPermissionError) as exc:
        return {"statusCode": 403, "body": json.dumps({"error": str(exc)})}
    except GrantNotFoundError as exc:
        return {"statusCode": 404, "body": json.dumps({"error": f"no such grant: {exc}"})}

    return {"statusCode": 200, "body": json.dumps(result)}
