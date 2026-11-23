"""What a write may store after the Guardrail has seen it: run with `make eval`."""
from __future__ import annotations

import pytest

from memory.guardrail import ContentBlockedError, apply_guardrail


class FakeBedrock:
    def __init__(self, response: dict) -> None:
        self._response = response
        self.calls: list[dict] = []

    def apply_guardrail(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return self._response


def test_clean_text_is_stored_as_written():
    client = FakeBedrock({"action": "NONE", "outputs": [], "assessments": []})
    assert apply_guardrail(client, "gr-1", "DRAFT", "The X-7 is classified 7A994.") == "The X-7 is classified 7A994."
    assert client.calls[0]["source"] == "INPUT"


def test_a_masked_secret_is_stored_masked():
    client = FakeBedrock(
        {
            "action": "GUARDRAIL_INTERVENED",
            "outputs": [{"text": "The deploy key is {aws-access-key}, do not lose it."}],
            "assessments": [
                {"sensitiveInformationPolicy": {"regexes": [{"name": "aws-access-key", "action": "ANONYMIZED"}]}}
            ],
        }
    )
    stored = apply_guardrail(client, "gr-1", "DRAFT", "The deploy key is AKIAABCDEFGHIJKLMNOP, do not lose it.")
    assert "AKIA" not in stored
    assert "{aws-access-key}" in stored


def test_blocked_content_is_refused_and_the_refusal_is_never_stored():
    """When a rule blocks, "outputs" is the Guardrail's own refusal sentence. Storing it
    would put that sentence into memory as a fact."""
    client = FakeBedrock(
        {
            "action": "GUARDRAIL_INTERVENED",
            "outputs": [{"text": "This input contains content that can't be stored."}],
            "assessments": [
                {"sensitiveInformationPolicy": {"regexes": [{"name": "private-key-block", "action": "BLOCKED"}]}}
            ],
        }
    )
    with pytest.raises(ContentBlockedError):
        apply_guardrail(client, "gr-1", "DRAFT", "-----BEGIN RSA PRIVATE KEY-----\nMIIE...")


def test_one_block_among_masks_still_refuses():
    client = FakeBedrock(
        {
            "action": "GUARDRAIL_INTERVENED",
            "outputs": [{"text": "This input contains content that can't be stored."}],
            "assessments": [
                {
                    "sensitiveInformationPolicy": {
                        "regexes": [
                            {"name": "aws-access-key", "action": "ANONYMIZED"},
                            {"name": "private-key-block", "action": "BLOCKED"},
                        ]
                    }
                }
            ],
        }
    )
    with pytest.raises(ContentBlockedError):
        apply_guardrail(client, "gr-1", "DRAFT", "key and a private key")


def test_a_block_by_any_policy_refuses():
    client = FakeBedrock(
        {
            "action": "GUARDRAIL_INTERVENED",
            "outputs": [{"text": "refused"}],
            "assessments": [{"contentPolicy": {"filters": [{"type": "VIOLENCE", "action": "BLOCKED"}]}}],
        }
    )
    with pytest.raises(ContentBlockedError):
        apply_guardrail(client, "gr-1", "DRAFT", "text")


def test_an_intervention_with_nothing_to_store_refuses():
    client = FakeBedrock({"action": "GUARDRAIL_INTERVENED", "outputs": [], "assessments": []})
    with pytest.raises(ContentBlockedError):
        apply_guardrail(client, "gr-1", "DRAFT", "text")


def test_no_guardrail_means_no_write():
    with pytest.raises(ContentBlockedError):
        apply_guardrail(None, "", "DRAFT", "text")
    with pytest.raises(ContentBlockedError):
        apply_guardrail(FakeBedrock({"action": "NONE"}), "", "DRAFT", "text")
