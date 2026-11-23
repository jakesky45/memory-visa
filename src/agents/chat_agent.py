"""The agent: a model on Amazon Bedrock that uses memory in a conversation (N6).

Decision D9, option A: the agent has no memory permission of its own. Each memory
call goes to the Gateway with the user's own access token (agents/gateway_client.py),
so there is one path to memory and it is the path already proved: verified identity,
the grant check, the Guardrail, and the visa. When AWS refuses a read, the refusal
comes back as a tool result and the model tells the user.

Short-term memory is the message list of one conversation, held by the caller
(server.py keeps one list per session). Long-term memory is AgentCore Memory, reached
with the memory_search and memory_write tools.

Decision D10: Claude Opus 5.5 on Amazon Bedrock. Set MODEL_ID to change the model.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from agents.gateway_client import GatewayClient, GatewayError

DEFAULT_MODEL_ID = "anthropic.claude-opus-5-5"
MAX_TOKENS = 8000
MAX_TOOL_ROUNDS = 8

# The Gateway names a tool "{Target}___{tool}". The agent gets these four and no
# others. It can ask for access for the user; only a person can approve.
AGENT_TOOLS = ("whoami", "memory_search", "memory_write", "request_access")

SYSTEM_PROMPT = """You are an assistant for one person at work. You have a shared memory that \
outlasts this conversation.

Who you are talking to, and the memory namespaces they can use now:
{identity}

How to use memory:
- Before you answer a question about work facts, search memory with memory_search. Search the \
person's own namespaces first.
- When the person states a fact worth keeping (a decision, a classification, a preference), \
store it with memory_write in one of their own namespaces. Write one clear sentence that is \
complete without this conversation. Do not store secrets or small talk.
- A recent write can take a minute to appear in search results. If the fact is earlier in \
this conversation, use it from the conversation.
- You can read another team's namespace only while an approved grant covers it. If a search is \
refused, tell the person plainly that access was refused and why. Do not guess the answer. \
Call whoami to see the current grants, because a grant can start or end during a conversation.
- Use request_access only when the person asks you to request access. Give their reason in \
their words. A person on the other team approves or denies it; you cannot.
- Say where a fact came from: this conversation, or memory and which namespace."""


def make_model_client(region: str | None = None):
    """The Anthropic SDK's client for Claude in Amazon Bedrock. It signs with the AWS
    credentials of the caller: the Runtime's role in the container, your own locally."""
    from anthropic import AnthropicBedrockMantle  # lazy: the offline tests need no SDK

    return AnthropicBedrockMantle(aws_region=region or os.environ.get("AWS_REGION", "eu-west-2"))


def _short_name(tool_name: str) -> str:
    return tool_name.split("___", 1)[-1]


def _block_type(block) -> str:
    return block["type"] if isinstance(block, dict) else block.type


@dataclass
class ToolCall:
    """One memory call the agent made, for the demo to show beside the reply."""

    tool: str
    arguments: dict
    status: int
    result: object


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


class ChatAgent:
    """One user's agent. `gateway` carries that user's token, so make one per caller."""

    def __init__(self, model_client, gateway: GatewayClient, model_id: str | None = None) -> None:
        self._client = model_client
        self._gateway = gateway
        self._model_id = model_id or os.environ.get("MODEL_ID", DEFAULT_MODEL_ID)
        self._tools: list[dict] | None = None
        self._system: str | None = None
        self.identity: dict = {}

    def load(self) -> dict:
        """Ask the Gateway what this user may call and who they are. The tool list
        and the identity both come from the Gateway, never from the request.
        Returns the identity (the whoami answer)."""
        if self._tools is not None:
            return self.identity
        tools = []
        whoami_name = None
        for tool in self._gateway.list_tools():
            short = _short_name(tool["name"])
            if short not in AGENT_TOOLS:
                continue
            if short == "whoami":
                whoami_name = tool["name"]
            tools.append(
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("inputSchema") or {"type": "object", "properties": {}},
                }
            )
        if whoami_name is None:
            raise GatewayError("the Gateway did not offer the whoami tool to this caller")
        identity = self._gateway.call_tool(whoami_name, {})
        if not identity.ok:
            raise GatewayError(f"whoami was refused: {identity.as_text()}")
        self._tools = tools
        self._system = SYSTEM_PROMPT.format(identity=identity.as_text())
        self.identity = identity.body if isinstance(identity.body, dict) else {}
        return self.identity

    def _run_tool(self, block, trace: list[ToolCall]) -> dict:
        allowed = {tool["name"] for tool in self._tools}
        if block.name not in allowed:
            return {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": f"Error: '{block.name}' is not a tool this agent can call.",
                "is_error": True,
            }
        arguments = dict(block.input or {})
        try:
            result = self._gateway.call_tool(block.name, arguments)
            status, body, text, ok = result.status, result.body, result.as_text(), result.ok
        except GatewayError as exc:
            status, body, text, ok = 502, str(exc), str(exc), False
        trace.append(ToolCall(_short_name(block.name), arguments, status, body))
        answer = {"type": "tool_result", "tool_use_id": block.id, "content": text if ok else f"Refused ({status}): {text}"}
        if not ok:
            answer["is_error"] = True
        return answer

    def turn(self, messages: list, user_text: str) -> Reply:
        """Answer one user message. `messages` is the conversation so far and is
        extended in place; pass the same list on the next turn."""
        self.load()
        messages.append({"role": "user", "content": user_text})
        trace: list[ToolCall] = []

        for _ in range(MAX_TOOL_ROUNDS):
            response = self._client.messages.create(
                model=self._model_id,
                max_tokens=MAX_TOKENS,
                system=self._system,
                tools=self._tools,
                messages=messages,
            )
            # The whole content goes back into the conversation, thinking blocks too.
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "tool_use":
                results = [self._run_tool(b, trace) for b in response.content if _block_type(b) == "tool_use"]
                messages.append({"role": "user", "content": results})
                continue

            text = "".join(b.text for b in response.content if _block_type(b) == "text").strip()
            if response.stop_reason == "refusal":
                text = text or "The model declined to answer."
            elif response.stop_reason == "max_tokens":
                text = (text + "\n\n(The answer was cut short.)").strip()
            return Reply(text, trace)

        # The conversation must not end on tool results with no answer.
        text = "I stopped after too many memory calls without an answer. Ask again in a different way."
        messages.append({"role": "assistant", "content": text})
        return Reply(text, trace)
