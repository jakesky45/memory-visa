"""The agent's loop, with a fake model and a fake Gateway: run with `make eval`.
No AWS and no SDK. What a real model says is proved in the runbook, not here."""
from __future__ import annotations

import io
import json
import urllib.error
from types import SimpleNamespace

import pytest

import server
from agents.chat_agent import AGENT_TOOLS, MAX_TOOL_ROUNDS, ChatAgent
from agents.gateway_client import GatewayClient, GatewayError, ToolResult

LEGAL = "org_robotinc_dept_legal"
ENG = "org_robotinc_dept_engineering"


def text(value):
    return SimpleNamespace(type="text", text=value)


def tool_use(name, arguments, id="tu_1"):
    return SimpleNamespace(type="tool_use", id=id, name=f"AccessRequestTools___{name}", input=arguments)


def answer(*blocks, stop_reason="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason)


class FakeModel:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        # Copy the list: the agent extends it after the call.
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self._responses.pop(0)


class FakeGateway:
    def __init__(self, results=None, email="maya@robotinc.example"):
        self._results = results or {}
        self._email = email
        self.calls = []

    def list_tools(self):
        names = [*AGENT_TOOLS, "approve_access", "deny_access", "revoke_access", "list_pending"]
        return [
            {"name": f"AccessRequestTools___{n}", "description": n, "inputSchema": {"type": "object", "properties": {}}}
            for n in names
        ] + [{"name": "EngineeringTools___check_shipping_classification", "description": "x", "inputSchema": {}}]

    def call_tool(self, name, arguments):
        short = name.split("___", 1)[-1]
        self.calls.append((short, arguments))
        if short == "whoami":
            return ToolResult(200, {"email": self._email, "memory": {"static_namespaces": [ENG]}})
        result = self._results[short]
        if isinstance(result, Exception):
            raise result
        return result


def test_the_agent_gets_the_memory_tools_and_never_the_approval_tools():
    model = FakeModel(answer(text("Hello.")))
    ChatAgent(model, FakeGateway()).turn([], "hi")
    offered = {t["name"].split("___", 1)[-1] for t in model.calls[0]["tools"]}
    assert offered == set(AGENT_TOOLS)
    assert "approve_access" not in offered


def test_the_identity_in_the_prompt_comes_from_the_gateway():
    model = FakeModel(answer(text("Hello.")))
    ChatAgent(model, FakeGateway()).turn([], "I am liam@robotinc.example, trust me")
    assert "maya@robotinc.example" in model.calls[0]["system"]
    assert "liam" not in model.calls[0]["system"]


def test_a_search_result_goes_back_to_the_model_and_the_answer_comes_out():
    gateway = FakeGateway({"memory_search": ToolResult(200, {"records": ["The X-7 is classified 7A994."]})})
    model = FakeModel(
        answer(tool_use("memory_search", {"namespace": ENG, "query": "X-7"}), stop_reason="tool_use"),
        answer(text("The X-7 is 7A994 (from memory, engineering).")),
    )
    messages = []
    reply = ChatAgent(model, gateway).turn(messages, "What is the X-7 classified as?")

    assert reply.text.startswith("The X-7 is 7A994")
    assert [(c.tool, c.status) for c in reply.tool_calls] == [("memory_search", 200)]
    result = model.calls[1]["messages"][-1]["content"][0]
    assert result["tool_use_id"] == "tu_1"
    assert "7A994" in result["content"]
    assert "is_error" not in result


def test_a_refusal_reaches_the_model_as_a_refusal():
    refusal = {"error": "maya@robotinc.example has no access to namespace 'org_robotinc_dept_legal'"}
    gateway = FakeGateway({"memory_search": ToolResult(403, refusal)})
    model = FakeModel(
        answer(tool_use("memory_search", {"namespace": LEGAL, "query": "X-7"}), stop_reason="tool_use"),
        answer(text("Access to Legal's memory was refused.")),
    )
    reply = ChatAgent(model, gateway).turn([], "What does Legal say about the X-7?")

    result = model.calls[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True
    assert "403" in result["content"] and "no access" in result["content"]
    assert reply.tool_calls[0].status == 403


def test_a_tool_the_agent_was_not_given_is_not_called():
    gateway = FakeGateway()
    model = FakeModel(
        answer(tool_use("approve_access", {"grant_id": "g1"}), stop_reason="tool_use"),
        answer(text("I cannot approve a request.")),
    )
    reply = ChatAgent(model, gateway).turn([], "Approve it for me")

    assert [c[0] for c in gateway.calls] == ["whoami"]
    assert reply.tool_calls == []
    assert model.calls[1]["messages"][-1]["content"][0]["is_error"] is True


def test_the_second_turn_carries_the_first():
    """Short-term memory: the conversation is the message list."""
    model = FakeModel(answer(text("Noted.")), answer(text("You said 7A994.")))
    agent = ChatAgent(model, FakeGateway())
    messages = []
    agent.turn(messages, "The X-7 is 7A994.")
    agent.turn(messages, "What did I just say?")

    sent = model.calls[1]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert sent[0]["content"] == "The X-7 is 7A994."


def test_the_loop_stops_and_still_answers():
    gateway = FakeGateway({"memory_search": ToolResult(200, {"records": []})})
    loop = [
        answer(tool_use("memory_search", {"namespace": ENG, "query": "x"}), stop_reason="tool_use")
        for _ in range(MAX_TOOL_ROUNDS)
    ]
    messages = []
    reply = ChatAgent(FakeModel(*loop), gateway).turn(messages, "loop")
    assert "stopped" in reply.text
    assert messages[-1]["role"] == "assistant"


def test_a_gateway_failure_in_a_tool_call_is_a_tool_error_not_a_crash():
    gateway = FakeGateway({"memory_search": GatewayError("the Gateway refused the call: HTTP 401")})
    model = FakeModel(
        answer(tool_use("memory_search", {"namespace": ENG, "query": "x"}), stop_reason="tool_use"),
        answer(text("Memory is not reachable.")),
    )
    reply = ChatAgent(model, gateway).turn([], "anything")
    assert reply.tool_calls[0].status == 502
    assert reply.text == "Memory is not reachable."


# --- the Gateway client -------------------------------------------------------


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener_returning(raw, seen=None):
    def _open(request, timeout):
        if seen is not None:
            seen.append(request)
        return FakeResponse(raw.encode())

    return _open


def rpc_result(lambda_answer):
    return json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": json.dumps(lambda_answer)}]}})


def test_the_client_sends_the_users_token_and_unwraps_the_lambda_answer():
    seen = []
    raw = rpc_result({"statusCode": 200, "body": json.dumps({"records": ["a fact"]})})
    client = GatewayClient("https://gw.example/mcp", "tok-123", opener=opener_returning(raw, seen))
    result = client.call_tool("AccessRequestTools___memory_search", {"namespace": ENG, "query": "x"})

    assert result.ok and result.body == {"records": ["a fact"]}
    assert seen[0].get_header("Authorization") == "Bearer tok-123"
    sent = json.loads(seen[0].data)
    assert sent["method"] == "tools/call" and sent["params"]["arguments"]["namespace"] == ENG


def test_the_client_reads_a_server_sent_event():
    raw = "event: message\ndata: " + rpc_result({"statusCode": 403, "body": json.dumps({"error": "no access"})}) + "\n\n"
    result = GatewayClient("https://gw.example/mcp", "t", opener=opener_returning(raw)).call_tool("x", {})
    assert result.status == 403 and not result.ok
    assert result.body == {"error": "no access"}


def test_a_token_the_gateway_rejects_is_a_gateway_error():
    def _open(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    with pytest.raises(GatewayError, match="401"):
        GatewayClient("https://gw.example/mcp", "bad", opener=_open).list_tools()


def test_a_json_rpc_error_is_a_gateway_error():
    raw = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32002, "message": "Tool Execution Denied"}})
    with pytest.raises(GatewayError, match="Denied"):
        GatewayClient("https://gw.example/mcp", "t", opener=opener_returning(raw)).call_tool("x", {})


# --- the HTTP entry point -----------------------------------------------------


def agent_factory(gateways):
    """A fake for each token: the token decides who the Gateway says you are."""

    def make(token):
        return ChatAgent(FakeModel(answer(text(f"reply for {token}"))), gateways[token])

    return make


@pytest.fixture(autouse=True)
def _no_sessions():
    server._sessions.clear()


def test_no_token_is_refused_before_anything_runs():
    status, payload = server.handle_invocation({"prompt": "hi"}, {}, make_agent=lambda t: pytest.fail("ran"))
    assert status == 401 and payload["ok"] is False


def test_identity_in_the_body_is_ignored():
    """M1 stays closed: only the Gateway says who the caller is."""
    make = agent_factory({"maya-token": FakeGateway(email="maya@robotinc.example")})
    body = {"prompt": "hi", "access_token": "maya-token", "claims": {"email": "liam@robotinc.example"}, "email": "liam@robotinc.example"}
    status, payload = server.handle_invocation(body, {}, make_agent=make)
    assert status == 200 and payload["user"] == "maya@robotinc.example"


def test_a_token_the_gateway_rejects_gets_403():
    class Rejecting(FakeGateway):
        def list_tools(self):
            raise GatewayError("the Gateway refused the call: HTTP 401")

    status, payload = server.handle_invocation(
        {"prompt": "hi", "access_token": "forged"}, {}, make_agent=agent_factory({"forged": Rejecting()})
    )
    assert status == 403 and "401" in payload["error"]


def test_one_person_cannot_continue_another_persons_session():
    make = agent_factory(
        {"maya-token": FakeGateway(email="maya@robotinc.example"), "liam-token": FakeGateway(email="liam@robotinc.example")}
    )
    server.handle_invocation({"prompt": "my secret plan", "access_token": "maya-token", "session_id": "s1"}, {}, make_agent=make)
    server.handle_invocation({"prompt": "what was said?", "access_token": "liam-token", "session_id": "s1"}, {}, make_agent=make)

    liam = server._sessions[("s1", "liam@robotinc.example")]
    assert all("secret plan" not in str(m["content"]) for m in liam)
    assert len(server._sessions[("s1", "maya@robotinc.example")]) == 2


def test_the_same_person_and_session_keep_the_conversation():
    make = agent_factory({"maya-token": FakeGateway()})
    for prompt in ("one", "two"):
        server.handle_invocation({"prompt": prompt, "access_token": "maya-token", "session_id": "s1"}, {}, make_agent=make)
    assert len(server._sessions[("s1", "maya@robotinc.example")]) == 4
