"""Who can approve a cross-domain access request into each department.

Kept separate from DOMAIN_TOOLS (domain_config.py) deliberately: who owns a
domain's approval authority and what that domain's toolset contains are
different concerns that happen to both be configured per-department.

Scoped by (org, department), not department alone - two orgs can both have a
"legal" department with a different, unrelated owner, and conflating them
would be exactly the cross-org leak the memory layer exists to prevent.
"""
from __future__ import annotations

DOMAIN_OWNERS: dict[tuple[str, str], str] = {
    ("robotinc", "engineering"): "maya@robotinc.example",
    ("robotinc", "legal"): "liam@robotinc.example",
    ("robotinc", "finance"): "priya@robotinc.example",
    ("helios-biotech", "engineering"): "sam@helios.example",
}


def owner_of(org: str, department: str) -> str | None:
    return DOMAIN_OWNERS.get((org, department))
