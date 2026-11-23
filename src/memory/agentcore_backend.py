"""The real AgentCore Memory backend - implements the MemoryBackend protocol
guarded_memory.py already expects, so swapping this in for FakeMemoryBackend
is the only change needed to go from the offline eval suite to live AWS.

Pool-tenancy: one Memory resource total (set in CDK), isolation is the
namespace string passed as actorId/namespace here - not a resource per org.
"""
from __future__ import annotations

import datetime as dt
import uuid

import boto3


class AgentCoreMemoryBackend:
    def __init__(self, memory_id: str, region: str = "eu-west-2", visa_issuer=None, strategy_id: str | None = None) -> None:
        self._memory_id = memory_id
        self._region = region
        self._client = boto3.client("bedrock-agentcore", region_name=region)
        # memory.visa_credentials.VisaIssuer, or None. With an issuer, reads use
        # credentials limited to one namespace and one window, and this process's
        # own role needs no permission to read memory at all.
        self._visa_issuer = visa_issuer
        # Optional. The strategy a directly written record is grouped under.
        self._strategy_id = strategy_id

    def put_record(self, namespace: str, text: str, metadata: dict[str, str]) -> dict:
        """Write one long-term record as it is, with BatchCreateMemoryRecords. No model
        rewords it, and the metadata stays on it (REVIEW.md N8, M5). `put` is different:
        it stores an event, and a model extracts the records later."""
        record = {
            "requestIdentifier": uuid.uuid4().hex,
            "namespaces": [namespace],
            "content": {"text": text},
            "timestamp": dt.datetime.now(dt.timezone.utc),
            "metadata": {key: {"stringValue": value} for key, value in metadata.items()},
        }
        if self._strategy_id:
            record["memoryStrategyId"] = self._strategy_id
        response = self._client.batch_create_memory_records(memoryId=self._memory_id, records=[record])
        failed = response.get("failedRecords", [])
        if failed:
            raise RuntimeError(f"the record was not created: {failed[0].get('errorMessage', failed[0])}")
        created = response.get("successfulRecords", [{}])[0]
        return {"namespace": namespace, "recordId": created.get("memoryRecordId")}

    def put(self, namespace: str, text: str) -> dict:
        response = self._client.create_event(
            memoryId=self._memory_id,
            actorId=namespace,
            eventTimestamp=dt.datetime.now(dt.timezone.utc),
            payload=[{"conversational": {"content": {"text": text}, "role": "USER"}}],
        )
        return {"namespace": namespace, "eventId": response.get("event", {}).get("eventId")}

    def retrieve(self, namespace: str, query: str) -> list[dict]:
        return self._retrieve(self._client, namespace, query)

    def retrieve_scoped(self, namespace: str, query: str, *, not_after, now, requester: str, grant_id: str | None) -> list[dict]:
        """Read with credentials that AWS limits to this namespace until `not_after`."""
        if self._visa_issuer is None:
            return self.retrieve(namespace, query)
        visa = self._visa_issuer.issue(namespace, not_after, now, requester=requester, grant_id=grant_id)
        return self._retrieve(visa.client(self._region), namespace, query)

    def _retrieve(self, client, namespace: str, query: str) -> list[dict]:
        response = client.retrieve_memory_records(
            memoryId=self._memory_id,
            namespace=namespace,
            searchCriteria={"searchQuery": query, "topK": 5},
        )
        return [_record(r) for r in response.get("memoryRecordSummaries", [])]


def _record(summary: dict) -> dict:
    """One search result with where it came from (REVIEW.md N2, M4). The author is not
    here: a long-term record is extracted by a model from events, and AgentCore does
    not carry the writer onto it. The audit table has who wrote what (N9 will put the
    author on the record itself)."""
    created = summary.get("createdAt")
    record = {
        "text": summary.get("content", {}).get("text", ""),
        "record_id": summary.get("memoryRecordId"),
        "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
        "score": summary.get("score"),
        "strategy_id": summary.get("memoryStrategyId"),
        "metadata": _plain_metadata(summary.get("metadata")),
    }
    return {key: value for key, value in record.items() if value is not None}


def _plain_metadata(metadata) -> dict | None:
    """{"published_by": {"stringValue": "oleg@..."}} becomes {"published_by": "oleg@..."}."""
    if not metadata:
        return None
    plain = {}
    for key, value in metadata.items():
        if key.lower().startswith("x-amz-"):
            continue  # AgentCore's own bookkeeping, not the fact's source
        inner = next(iter(value.values()), None) if isinstance(value, dict) else value
        plain[key] = inner.isoformat() if hasattr(inner, "isoformat") else inner
    return plain or None
