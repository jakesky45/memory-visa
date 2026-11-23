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

from .grants import Grant, GrantStore
from .visa_credentials import DEFAULT_STATIC_WINDOW

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


def published_namespace(org: str) -> str:
    """Where an org's published facts live. Every member of the org may read it, with
    no grant. Nobody writes to it with memory_write: a domain owner publishes to it."""
    return f"org_{org}_published"


class MemoryBackend(Protocol):
    """What a real AgentCore Memory client (or a test fake) needs to expose."""

    def put(self, namespace: str, text: str) -> dict: ...

    def retrieve(self, namespace: str, query: str) -> list[dict]: ...

    # Optional. A backend that has it is asked to read with credentials limited to
    # this namespace until `not_after` (memory/visa_credentials.py), so AWS enforces
    # the decision GuardedMemory just made:
    #
    #   def retrieve_scoped(self, namespace, query, *, not_after, now, requester, grant_id) -> list[dict]


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
        self._authorising_grant(claims, namespace, now)

    def _authorising_grant(self, claims: UserClaims, namespace: str, now: dt.datetime) -> Grant | None:
        """Why this caller may use this namespace: None for a static claim, or the active
        grant that opens it. Raises PermissionError when neither applies."""
        if namespace in static_namespaces(claims):
            return None
        for grant in self._grants.active_grants_for(requester_namespace(claims), now):
            if grant.target_namespace == namespace:
                return grant
        raise PermissionError(
            f"{claims.email} has no access to namespace '{namespace}' "
            "(no static claim, and no active grant)"
        )

    def _audit(self, claims: UserClaims, namespace: str, event: str, now: dt.datetime, **fields) -> None:
        """Every use of memory goes to the grant store's audit trail, allowed or
        refused (N2, M10). A store with no record_access keeps none."""
        record_access = getattr(self._grants, "record_access", None)
        if record_access is not None:
            record_access(actor=claims.email, namespace=namespace, event=event, at=now, **fields)

    def put(self, claims: UserClaims, namespace: str, text: str, now: dt.datetime) -> dict:
        # A grant is read-only. Only a member writes into a namespace: a visitor who could
        # write would be putting their words into the owner's memory, for the owner's
        # own agents to read back as the owner's knowledge.
        try:
            grant = self._authorising_grant(claims, namespace, now)
        except PermissionError as error:
            self._audit(claims, namespace, "write_refused", now, detail=str(error))
            raise
        if grant is not None:
            self._audit(claims, namespace, "write_refused", now, grant_id=grant.grant_id, detail="a grant never allows a write")
            raise PermissionError(
                f"{claims.email} may read namespace '{namespace}' under a grant, but a grant never allows a write"
            )
        try:
            sanitized = self._apply_guardrail(text)
        except Exception as error:
            self._audit(claims, namespace, "write_refused", now, detail=f"guardrail: {type(error).__name__}")
            raise
        result = self._memory.put(namespace, sanitized)
        event_id = result.get("eventId")
        self._audit(claims, namespace, "write", now, record_ids=(event_id,) if event_id else ())
        return result

    def publish(self, claims: UserClaims, department: str, text: str, now: dt.datetime) -> dict:
        """Publish one fact from a department to everyone in the org (N8). The fact is
        stored as written, with who published it and for which department, so a reader
        can see its source. The caller must be in the department; whether the caller
        may speak for it (the domain owner) is checked by agents.access_requests."""
        namespace = published_namespace(claims.org)
        if department not in claims.departments:
            self._audit(claims, namespace, "publish_refused", now, detail=f"not a member of '{department}'")
            raise PermissionError(f"{claims.email} is not in department '{department}' and cannot publish for it")
        put_record = getattr(self._memory, "put_record", None)
        if put_record is None:
            raise PermissionError("this memory backend cannot store a published fact")
        try:
            sanitized = self._apply_guardrail(text)
        except Exception as error:
            self._audit(claims, namespace, "publish_refused", now, detail=f"guardrail: {type(error).__name__}")
            raise
        metadata = {"published_by": claims.email, "department": department, "published_at": now.isoformat()}
        result = put_record(namespace, sanitized, metadata)
        record_id = result.get("recordId")
        self._audit(claims, namespace, "publish", now, record_ids=(record_id,) if record_id else (), detail=f"for {department}")
        return {**result, **metadata}

    def retrieve(self, claims: UserClaims, namespace: str, query: str, now: dt.datetime) -> list[dict]:
        """Search one namespace. Each result says where it came from and why the
        caller may see it. The read is in the audit trail before the caller has the
        results: if the trail cannot be written, nothing is returned."""
        published = namespace == published_namespace(claims.org)
        try:
            grant = None if published else self._authorising_grant(claims, namespace, now)
        except PermissionError as error:
            self._audit(claims, namespace, "read_refused", now, query=query, detail=str(error))
            raise
        grant_id = grant.grant_id if grant else None

        try:
            records = self._read(claims, namespace, query, now, grant)
        except Exception as error:
            # With a visa this is AWS refusing, for example after the window closes.
            self._audit(
                claims, namespace, "read_refused", now, grant_id=grant_id, query=query, detail=f"backend: {type(error).__name__}"
            )
            raise

        if grant:
            access = {
                "via": "grant",
                "grant_id": grant.grant_id,
                "approved_by": grant.approved_by,
                "expires_at": (grant.approved_at + grant.duration).isoformat(),
            }
        elif published:
            access = {"via": "published"}
        else:
            access = {"via": "membership"}
        results = [{**record, "namespace": namespace, "access": access} for record in records]

        ids = tuple(r["record_id"] for r in results if r.get("record_id"))
        self._audit(claims, namespace, "read", now, grant_id=grant_id, query=query, record_ids=ids)
        return results

    def _read(self, claims: UserClaims, namespace: str, query: str, now: dt.datetime, grant: Grant | None) -> list[dict]:
        retrieve_scoped = getattr(self._memory, "retrieve_scoped", None)
        if retrieve_scoped is None:
            return self._memory.retrieve(namespace, query)

        # The read ends when the grant ends. A read of the caller's own namespace
        # has no grant, so it gets a short fixed window.
        not_after = grant.approved_at + grant.duration if grant else now + DEFAULT_STATIC_WINDOW
        return retrieve_scoped(
            namespace,
            query,
            not_after=not_after,
            now=now,
            requester=claims.email,
            grant_id=grant.grant_id if grant else None,
        )
