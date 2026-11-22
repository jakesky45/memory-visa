"""The core RBAC gate in front of AgentCore Memory.

Every read and write passes through here first. Allowed namespaces are
computed from verified claims plus any currently-active cross-domain grant -
never from anything the caller asserts about itself beyond its own identity.
Anything outside that computed set raises PermissionError before the request
reaches AgentCore Memory.

The memory backend is a generic protocol, not a hard dependency on a
specific client library: `put` maps to AgentCore's CreateEvent (short-term,
raw interaction), `retrieve` maps to RetrieveMemoryRecords (long-term,
semantic search over extracted facts/preferences/summaries).
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Protocol

from .grants import GrantStore

AWS_ACCESS_KEY_PATTERN = re.compile(r"AKIA[0-9A-Z]{16}")


class PermissionError(Exception):
    pass


@dataclass
class UserClaims:
    """What a verified identity token actually asserts about the caller."""

    email: str
    org: str
    departments: tuple[str, ...] = field(default_factory=tuple)
    projects: tuple[str, ...] = field(default_factory=tuple)


def requester_namespace(claims: UserClaims) -> str:
    return f"org_{claims.org}_user_{claims.email}"


def static_namespaces(claims: UserClaims) -> set[str]:
    """Namespaces this identity is allowed into with no grant involved."""
    namespaces = {requester_namespace(claims)}
    namespaces |= {f"org_{claims.org}_dept_{d}" for d in claims.departments}
    namespaces |= {f"org_{claims.org}_proj_{p}" for p in claims.projects}
    return namespaces


class MemoryBackend(Protocol):
    """What a real AgentCore Memory client (or a test fake) needs to expose."""

    def put(self, namespace: str, text: str) -> dict: ...

    def retrieve(self, namespace: str, query: str) -> list[dict]: ...


class GuardedMemory:
    def __init__(self, memory_client: MemoryBackend, apply_guardrail, grant_store: GrantStore):
        self._memory = memory_client
        self._apply_guardrail = apply_guardrail
        self._grants = grant_store

    def allowed_namespaces(self, claims: UserClaims, now: dt.datetime) -> set[str]:
        allowed = static_namespaces(claims)
        for grant in self._grants.active_grants_for(requester_namespace(claims), now):
            allowed.add(grant.target_namespace)
        return allowed

    def _check(self, claims: UserClaims, namespace: str, now: dt.datetime) -> None:
        if namespace not in self.allowed_namespaces(claims, now):
            raise PermissionError(
                f"{claims.email} has no access to namespace '{namespace}' "
                "(no static claim, and no active grant)"
            )

    def put(self, claims: UserClaims, namespace: str, text: str, now: dt.datetime) -> dict:
        self._check(claims, namespace, now)
        sanitized = self._apply_guardrail(text)
        return self._memory.put(namespace, sanitized)

    def retrieve(self, claims: UserClaims, namespace: str, query: str, now: dt.datetime) -> list[dict]:
        self._check(claims, namespace, now)
        return self._memory.retrieve(namespace, query)
