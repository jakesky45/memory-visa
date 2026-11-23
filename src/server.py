"""The orchestrator: the agent's HTTP entry point in the AgentCore Runtime container.

POST /invocations  {"prompt": "...", "access_token": "...", "session_id": "..."}
GET  /ping         health check

The agent (agents/chat_agent.py) calls a model on Amazon Bedrock and reaches memory
only through the Gateway's MCP tools, with the caller's own access token (REVIEW.md
D9, option A). This container has no memory, grant or Guardrail permission. It does
not read the token and does not trust it: it passes it to the Gateway, and the
Gateway decides who the caller is. That keeps M1 closed. The old path trusted a
`claims` dict in the request body; nothing here takes identity from the body.

The token comes from the Authorization header when the Runtime passes it on, or
from "access_token" in the body. Either way it is only ever sent to the Gateway.

Short-term memory: one message list per (session, verified email), held in this
process. A Runtime session keeps its own container, so the list lasts as long as
the session does. Long-term memory is AgentCore Memory, through the tools.
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agents.chat_agent import ChatAgent, make_model_client
from agents.gateway_client import GatewayClient, GatewayError

SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"
MAX_SESSIONS = 200
MAX_BODY_BYTES = 256 * 1024

_sessions: dict[tuple[str, str], list] = {}
_lock = threading.Lock()
_model_client = None
_gateway_url = None


def _get_model_client():
    global _model_client
    if _model_client is None:
        _model_client = make_model_client()
    return _model_client


def _get_gateway_url() -> str:
    """GATEWAY_MCP_URL, or the SSM parameter the stack writes. Read from SSM and not
    set by CloudFormation, because the Gateway's target already depends on this Runtime."""
    global _gateway_url
    if _gateway_url is None:
        _gateway_url = os.environ.get("GATEWAY_MCP_URL")
    if _gateway_url is None:
        import boto3

        ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "eu-west-2"))
        _gateway_url = ssm.get_parameter(Name="/memory-visa/gateway-mcp-url")["Parameter"]["Value"]
    return _gateway_url


def _messages_for(session_id: str, email: str) -> list:
    """The conversation for this session and this person. The email is the Gateway's
    verified answer, so one person cannot continue another person's conversation."""
    with _lock:
        key = (session_id, email)
        if key not in _sessions:
            if len(_sessions) >= MAX_SESSIONS:
                _sessions.pop(next(iter(_sessions)))
            _sessions[key] = []
        return _sessions[key]


def handle_invocation(body: dict, headers, make_agent=None) -> tuple[int, dict]:
    """One chat turn. Returns (HTTP status, JSON answer). `make_agent` is for tests."""
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return 400, {"ok": False, "error": "send {\"prompt\": \"...\"}"}

    auth = headers.get("Authorization") or ""
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else body.get("access_token")
    if not token:
        return 401, {"ok": False, "error": "no access token: send the user's own token as 'access_token'"}

    session_id = str(headers.get(SESSION_HEADER) or body.get("session_id") or "default")

    try:
        if make_agent is None:
            agent = ChatAgent(_get_model_client(), GatewayClient(_get_gateway_url(), token))
        else:
            agent = make_agent(token)
        identity = agent.load()
        email = identity.get("email")
        if not email:
            return 403, {"ok": False, "error": "the Gateway gave no verified identity for this token"}
        messages = _messages_for(session_id, email)
        with _lock:
            turn_start = len(messages)
        try:
            reply = agent.turn(messages, prompt)
        except Exception:
            del messages[turn_start:]  # a failed turn leaves no half conversation behind
            raise
    except GatewayError as exc:
        return 403, {"ok": False, "error": str(exc)}

    return 200, {
        "ok": True,
        "reply": reply.text,
        "user": email,
        "session_id": session_id,
        "memory_calls": [
            {"tool": c.tool, "arguments": c.arguments, "status": c.status, "result": c.result} for c in reply.tool_calls
        ],
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/ping":
            self._respond(200, {"status": "Healthy"})
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/invocations":
            self._respond(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            self._respond(413, {"ok": False, "error": "request too large"})
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("not an object")
        except ValueError:
            self._respond(400, {"ok": False, "error": "the body must be a JSON object"})
            return
        try:
            status, payload = handle_invocation(body, self.headers)
        except Exception as exc:  # the model call or anything not expected
            print(json.dumps({"error": type(exc).__name__, "detail": str(exc)[:500]}))
            status, payload = 502, {"ok": False, "error": f"the agent failed: {type(exc).__name__}"}
        self._respond(status, payload)

    def _respond(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
