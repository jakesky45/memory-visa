"""The real AgentCore Memory backend - implements the MemoryBackend protocol
guarded_memory.py already expects, so swapping this in for FakeMemoryBackend
is the only change needed to go from the offline eval suite to live AWS.

Pool-tenancy: one Memory resource total (set in CDK), isolation is the
namespace string passed as actorId/namespace here - not a resource per org.
"""
from __future__ import annotations

import datetime as dt

import boto3


class AgentCoreMemoryBackend:
    def __init__(self, memory_id: str, region: str = "eu-west-2") -> None:
        self._memory_id = memory_id
        self._client = boto3.client("bedrock-agentcore", region_name=region)

    def put(self, namespace: str, text: str) -> dict:
        response = self._client.create_event(
            memoryId=self._memory_id,
            actorId=namespace,
            eventTimestamp=dt.datetime.now(dt.timezone.utc),
            payload=[{"conversational": {"content": {"text": text}, "role": "USER"}}],
        )
        return {"namespace": namespace, "eventId": response.get("event", {}).get("eventId")}

    def retrieve(self, namespace: str, query: str) -> list[dict]:
        response = self._client.retrieve_memory_records(
            memoryId=self._memory_id,
            namespace=namespace,
            searchCriteria={"searchQuery": query, "topK": 5},
        )
        return [{"text": r.get("content", {}).get("text", "")} for r in response.get("memoryRecordSummaries", [])]
