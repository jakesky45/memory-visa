"""The calls the backend makes to AgentCore Memory, with a fake client: run with
`make eval`. Needs boto3 to import the module."""
from __future__ import annotations

import datetime as dt

import pytest

pytest.importorskip("boto3")

from memory.agentcore_backend import AgentCoreMemoryBackend, _record  # noqa: E402


class FakeClient:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def batch_create_memory_records(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


def _backend(response, strategy_id=None):
    backend = object.__new__(AgentCoreMemoryBackend)  # no boto3 client
    backend._memory_id, backend._region, backend._visa_issuer = "mem-1", "eu-west-2", None
    backend._strategy_id = strategy_id
    backend._client = FakeClient(response)
    return backend


def test_a_published_record_is_sent_as_written_with_its_metadata():
    backend = _backend({"successfulRecords": [{"memoryRecordId": "mem-abc", "status": "SUCCEEDED"}], "failedRecords": []})
    result = backend.put_record("org_robotinc_published", "The X-7 is 7A994.", {"published_by": "oleg@robotinc.example"})

    sent = backend._client.calls[0]
    record = sent["records"][0]
    assert sent["memoryId"] == "mem-1" and len(sent["records"]) == 1
    assert record["namespaces"] == ["org_robotinc_published"]
    assert record["content"] == {"text": "The X-7 is 7A994."}
    assert record["metadata"] == {"published_by": {"stringValue": "oleg@robotinc.example"}}
    assert isinstance(record["timestamp"], dt.datetime) and 1 <= len(record["requestIdentifier"]) <= 80
    assert "memoryStrategyId" not in record
    assert result == {"namespace": "org_robotinc_published", "recordId": "mem-abc"}


def test_the_strategy_is_sent_only_when_set():
    backend = _backend({"successfulRecords": [{"memoryRecordId": "m"}], "failedRecords": []}, strategy_id="domain_facts-1")
    backend.put_record("org_robotinc_published", "x", {})
    assert backend._client.calls[0]["records"][0]["memoryStrategyId"] == "domain_facts-1"


def test_a_record_aws_fails_is_an_error_not_a_silent_success():
    backend = _backend({"successfulRecords": [], "failedRecords": [{"errorCode": 400, "errorMessage": "bad namespace"}]})
    with pytest.raises(RuntimeError, match="bad namespace"):
        backend.put_record("org_robotinc_published", "x", {})


def test_metadata_on_a_result_is_made_plain():
    summary = {
        "memoryRecordId": "mem-abc",
        "content": {"text": "The X-7 is 7A994."},
        "metadata": {"published_by": {"stringValue": "oleg@robotinc.example"}, "department": {"stringValue": "legal"}},
    }
    assert _record(summary)["metadata"] == {"published_by": "oleg@robotinc.example", "department": "legal"}
    assert "metadata" not in _record({"content": {"text": "x"}})


def test_agentcores_own_metadata_is_not_shown_as_the_source():
    summary = {
        "content": {"text": "x"},
        "metadata": {"x-amz-agentcore-memory-recordType": {"stringValue": "BASE"}, "department": {"stringValue": "legal"}},
    }
    assert _record(summary)["metadata"] == {"department": "legal"}
