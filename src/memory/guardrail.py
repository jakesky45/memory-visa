"""The Guardrail check a write passes before it reaches Memory.

Two different things can come back from ApplyGuardrail, and they must not be
treated the same:

  - ANONYMIZED: a secret was found and masked. The masked text is returned,
    and that is what gets stored.
  - BLOCKED: the content may not be stored at all. The Guardrail's "outputs" is
    then its refusal message. Storing that would write the refusal into memory
    as if it were a fact - so a block raises ContentBlockedError, and the write
    does not happen.

Anything unexpected also refuses the write: no Guardrail configured, or an
intervention with no text to store. A write that cannot be checked is not made.
"""
from __future__ import annotations


class ContentBlockedError(Exception):
    """The Guardrail refused this content, or the content could not be checked."""


def _was_blocked(node) -> bool:
    """True if any assessment, of any policy, reports a BLOCKED action."""
    if isinstance(node, dict):
        if node.get("action") == "BLOCKED":
            return True
        return any(_was_blocked(value) for value in node.values())
    if isinstance(node, list):
        return any(_was_blocked(item) for item in node)
    return False


def apply_guardrail(client, guardrail_id: str, guardrail_version: str, text: str) -> str:
    """The text to store: unchanged, or with secrets masked. Raises ContentBlockedError."""
    if client is None or not guardrail_id:
        raise ContentBlockedError("no Guardrail is configured, so this write cannot be checked")

    response = client.apply_guardrail(
        guardrailIdentifier=guardrail_id,
        guardrailVersion=guardrail_version,
        source="INPUT",
        content=[{"text": {"text": text}}],
    )
    if response.get("action") != "GUARDRAIL_INTERVENED":
        return text
    if _was_blocked(response.get("assessments", [])):
        raise ContentBlockedError("this content may not be stored: the Guardrail blocked it")

    masked = "".join(output.get("text", "") for output in response.get("outputs", []))
    if not masked:
        raise ContentBlockedError("the Guardrail changed this content but returned nothing to store")
    return masked
