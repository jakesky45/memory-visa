"""Fakes for the eval suite - no live AWS/Bedrock calls.

Mocking at the client-interface boundary is the deliberate choice here, not a
shortcut: LocalStack's Bedrock emulation doesn't cover ApplyGuardrail or
AgentCore at all (checked against its own docs), so there's no honest way to
emulate the real services locally. These fakes let the RBAC/grant logic -
the actual thing under test - run fast and deterministically; a small number
of real-sandbox integration tests are the right place to prove the guardrail
call and AgentCore wiring themselves.
"""
from __future__ import annotations

import re


class FakeMemoryBackend:
    """In-memory stand-in for AgentCore Memory's put/retrieve shape."""

    def __init__(self) -> None:
        self.records: dict[str, list[str]] = {}

    def put(self, namespace: str, text: str) -> dict:
        self.records.setdefault(namespace, []).append(text)
        return {"namespace": namespace, "stored": text}

    def retrieve(self, namespace: str, query: str) -> list[dict]:
        return [{"text": t} for t in self.records.get(namespace, [])]


_AWS_KEY_PATTERN = re.compile(r"AKIA[0-9A-Z]{16}")


def fake_apply_guardrail(text: str) -> str:
    """Stands in for bedrock_runtime.apply_guardrail(source="INPUT").

    Real regex-based secret redaction, same pattern the real Bedrock
    Guardrail config uses - this part of the fake is a faithful enough
    model of the real behavior to trust the eval's assertion on it.
    """
    return _AWS_KEY_PATTERN.sub("[REDACTED]", text)
