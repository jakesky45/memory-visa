"""Cognito pre-token-generation trigger (V2): puts the caller's org and department
into the ACCESS token.

Why: the Gateway's authorizer only ever sees access tokens, and a Cognito access
token carries no custom attributes. So the Cedar rule that gates an Engineering tool
by department, principal.hasTag("department"), could never be true, and making it
active denied everyone (REVIEW.md C1, N10). AgentCore builds the Cedar principal's
tags from the token's claims, so the claims have to be in the access token.

The values come from the user's own Cognito attributes, which only an administrator
can set. Nothing here reads anything the caller sent.
"""
from __future__ import annotations

CLAIMS = {"custom:org": "org", "custom:department": "department"}


def handler(event, context):
    attributes = (event.get("request") or {}).get("userAttributes") or {}
    claims = {claim: attributes[attribute] for attribute, claim in CLAIMS.items() if attributes.get(attribute)}
    event["response"] = {
        "claimsAndScopeOverrideDetails": {
            "accessTokenGeneration": {"claimsToAddOrOverride": claims},
        }
    }
    return event
