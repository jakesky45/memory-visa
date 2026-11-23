"""Per-domain tool capability, gated the same way as memory namespaces.

A domain's toolset is never just "available but the model is told not to use
it" - tools outside the current allowed set are never registered on the
agent for this invocation at all, so the model has nothing to be talked into
misusing.

A grant into a domain opens that domain's memory, to read. It carries the
domain's toolset only when the request asked for it (`include_tools`) and the
owner approved that request - reading what Legal knows and acting as Legal are
different things, and the owner should see which one they are approving.
"""
from __future__ import annotations

import datetime as dt

from memory.grants import GrantStore
from memory.guarded_memory import UserClaims, requester_namespace

# Keyed by department name alone, not (org, department) - unlike domain_owners.py's
# DOMAIN_OWNERS. This is deliberate: a role's toolset is a template applied the same
# way across every org tenant, whereas who personally approves a request is always a
# specific person scoped to one org. If a client ever needs a different toolset per
# org for the "same" department, this dict is the place that assumption breaks.
DOMAIN_TOOLS: dict[str, set[str]] = {
    "engineering": {"check_shipping_classification", "generate_packaging_manifest", "query_firmware_specs"},
    "legal": {"search_contracts", "check_export_control", "log_compliance_note"},
    "finance": {"query_budget", "query_ma_targets"},
}


def _namespace_domain(namespace: str) -> str | None:
    """Extracts the department segment from an `org_{org}_dept_{dept}` namespace.

    Project namespaces (`org_{org}_proj_{proj}`) and user namespaces carry no
    domain toolset of their own - only a department grant unlocks tools.
    """
    marker = "_dept_"
    if marker not in namespace:
        return None
    return namespace.split(marker, 1)[1]


def allowed_tools(claims: UserClaims, grant_store: GrantStore, now: dt.datetime) -> set[str]:
    tools: set[str] = set()
    for department in claims.departments:
        tools |= DOMAIN_TOOLS.get(department, set())

    for grant in grant_store.active_grants_for(requester_namespace(claims), now):
        if not grant.include_tools:
            continue
        domain = _namespace_domain(grant.target_namespace)
        if domain is not None:
            tools |= DOMAIN_TOOLS.get(domain, set())

    return tools
