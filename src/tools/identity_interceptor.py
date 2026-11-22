"""Request interceptor on the Gateway: resolves the caller's already-verified
access token to their real identity via Cognito's own GetUser API, and
injects it into the tool call's arguments, overwriting whatever the caller
tried to assert - this is what actually closes the "anyone can call
approve_access as anyone" gap, not just a documented limitation anymore.

Resolves email, `custom:org`, and `custom:department` together from the same
GetUser call - not just email. The five original AccessRequestTools only
ever needed email; `whoami` (below) is the first tool that needs the full
identity to report a caller's own effective access, so this got extended
rather than duplicating a second Cognito call elsewhere.

Why GetUser instead of decoding the token's own claims: the Gateway's
CUSTOM_JWT authorizer only admits a token carrying a `scope` claim, which
Cognito only puts on *access* tokens, not ID tokens - and access tokens don't
carry `email` or custom attributes, only `sub`/`username`. GetUser takes the
same access token the Gateway already accepted and returns that token's
owner's real attributes directly from Cognito - the verification already
happened (an invalid/expired token simply fails this call), so this isn't
re-trusting anything the caller asserted.

Runs gateway-wide (every target, every call), not just in front of
AccessRequestTools, so this only touches `params.arguments` when the request
is an MCP tool call - anything else (tools/list, HTTP-passthrough requests)
passes through completely unchanged.
"""
from __future__ import annotations

import boto3

_cognito = boto3.client("cognito-idp")


def _resolve_identity(bearer_header: str) -> dict:
    token = bearer_header.removeprefix("Bearer ").removeprefix("bearer ")
    if not token:
        return {}
    try:
        attributes = _cognito.get_user(AccessToken=token)["UserAttributes"]
    except Exception:
        return {}
    by_name = {a["Name"]: a["Value"] for a in attributes}
    return {
        "email": by_name.get("email"),
        "org": by_name.get("custom:org"),
        "department": by_name.get("custom:department"),
    }


def handler(event, context):
    gateway_request = event.get("mcp", {}).get("gatewayRequest")
    if gateway_request is None:
        # Not an MCP tool call (e.g. an HTTP-passthrough target) - nothing of
        # this interceptor's business, pass it through untouched.
        return {"interceptorOutputVersion": "1.0"}

    headers = gateway_request.get("headers", {}) or {}
    body = gateway_request.get("body", {}) or {}

    identity = _resolve_identity(headers.get("authorization", ""))

    arguments = body.get("params", {}).get("arguments")
    if isinstance(arguments, dict):
        # Overwrite, never merge - a caller-supplied value must never survive.
        arguments["_verified_email"] = identity.get("email")
        arguments["_verified_org"] = identity.get("org")
        arguments["_verified_department"] = identity.get("department")

    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {"transformedGatewayRequest": {"headers": headers, "body": body}},
    }
