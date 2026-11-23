"""A small MCP client for the AgentCore Gateway: tools/list and tools/call.

The agent (agents/chat_agent.py) reaches memory only through this client, with the
user's own access token. The agent holds no memory permission of its own. The
Gateway checks the token, the interceptor adds the verified identity, and the tools
Lambda applies the grant, the Guardrail and the visa. A token that is not valid is
refused by the Gateway, not by code here.

Standard library only, so the offline tests need no SDK.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass


class GatewayError(Exception):
    """The Gateway refused the call, or its answer could not be read."""


@dataclass(frozen=True)
class ToolResult:
    """What one tools/call returned. `status` is the tools Lambda's own status code
    (200, 403, 422...), so a refusal reaches the model as a refusal."""

    status: int
    body: object

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def as_text(self) -> str:
        return self.body if isinstance(self.body, str) else json.dumps(self.body)


def _read_rpc(raw: str) -> dict:
    """The Gateway answers with plain JSON or with one server-sent event."""
    text = raw.strip()
    if not text.startswith("{"):
        data = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
        if not data:
            raise GatewayError("the Gateway answer was not JSON and not a server-sent event")
        text = data[-1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise GatewayError(f"the Gateway answer was not valid JSON: {exc}") from exc


def _read_tool_result(result: dict) -> ToolResult:
    """A Lambda target returns its own {"statusCode", "body"} as JSON text inside the
    MCP content. Unwrap it, so the caller sees the status and the body."""
    texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    text = "\n".join(texts)
    fallback_status = 500 if result.get("isError") else 200
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return ToolResult(fallback_status, text)
    if isinstance(payload, dict) and "statusCode" in payload:
        body = payload.get("body", "")
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError:
                pass
        return ToolResult(int(payload["statusCode"]), body)
    return ToolResult(fallback_status, payload)


class GatewayClient:
    def __init__(self, mcp_url: str, access_token: str, timeout: float = 30.0, opener=None) -> None:
        self._url = mcp_url
        self._token = access_token
        self._timeout = timeout
        self._open = opener or urllib.request.urlopen
        self._next_id = 0

    def _rpc(self, method: str, params: dict) -> dict:
        self._next_id += 1
        request = urllib.request.Request(
            self._url,
            data=json.dumps({"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params}).encode(),
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            method="POST",
        )
        try:
            with self._open(request, timeout=self._timeout) as response:
                raw = response.read().decode()
        except urllib.error.HTTPError as exc:
            # 401 and 403 here mean the Gateway did not accept the token.
            raise GatewayError(f"the Gateway refused the call: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise GatewayError(f"the Gateway could not be reached: {exc.reason}") from exc
        answer = _read_rpc(raw)
        if "error" in answer:
            raise GatewayError(f"the Gateway refused the call: {answer['error'].get('message', answer['error'])}")
        return answer.get("result", {})

    def list_tools(self) -> list[dict]:
        return self._rpc("tools/list", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> ToolResult:
        return _read_tool_result(self._rpc("tools/call", {"name": name, "arguments": arguments}))
