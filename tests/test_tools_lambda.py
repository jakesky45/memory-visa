"""The MCP tools Lambda, called the way the Gateway calls it: run with `make eval`.

Needs boto3 to import the module (it is in the Lambda runtime; `pip install boto3`
locally). No AWS call is made: the grant store and the memory are replaced with the
in-memory ones, and identity arrives as the interceptor would inject it.
"""
from __future__ import annotations

import datetime as dt
import importlib
import json
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("boto3")

from memory.grants import GrantStore  # noqa: E402
from memory.guarded_memory import GuardedMemory  # noqa: E402
from memory.guardrail import ContentBlockedError  # noqa: E402

from .fakes import FakeMemoryBackend, fake_apply_guardrail  # noqa: E402

LEGAL = "org_robotinc_dept_legal"
MAYA = {"_verified_email": "maya@robotinc.example", "_verified_org": "robotinc", "_verified_department": "engineering"}
LIAM = {"_verified_email": "liam@robotinc.example", "_verified_org": "robotinc", "_verified_department": "legal"}
PRIYA = {"_verified_email": "priya@robotinc.example", "_verified_org": "robotinc", "_verified_department": "finance"}


@pytest.fixture
def tools(monkeypatch):
    monkeypatch.setenv("GRANTS_TABLE_NAME", "grants")
    monkeypatch.setenv("GRANTS_AUDIT_TABLE_NAME", "grants-audit")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-2")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    sys.modules.pop("tools.access_request_tools", None)
    module = importlib.import_module("tools.access_request_tools")

    grants = GrantStore()
    backend = FakeMemoryBackend()
    monkeypatch.setattr(module, "_grants", grants)
    monkeypatch.setattr(module, "_memory", GuardedMemory(backend, fake_apply_guardrail, grants))
    module.backend = backend
    return module


def call(tools, name: str, identity: dict, **arguments):
    context = SimpleNamespace(client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": f"AccessRequestTools___{name}"}))
    response = tools.handler({**arguments, **identity}, context)
    return response["statusCode"], json.loads(response["body"])


def request(tools, identity=MAYA, **overrides):
    arguments = {"target_org": "robotinc", "target_department": "legal", "reason": "Need the classification.", "duration_hours": 2}
    return call(tools, "request_access", identity, **{**arguments, **overrides})


def test_the_requesters_org_comes_from_the_token_not_an_argument(tools):
    status, grant = request(tools, requester_org="helios-biotech")
    assert status == 200
    assert grant["requester_namespace"] == "org_robotinc_user_maya@robotinc.example"


def test_a_caller_with_no_verified_identity_is_refused(tools):
    status, body = request(tools, identity={})
    assert status == 403
    status, body = call(tools, "memory_search", {"_verified_email": "x@y.example"}, namespace=LEGAL, query="q")
    assert status == 403, "an email with no verified org is not an identity"


def test_identity_typed_into_the_arguments_is_only_what_the_interceptor_put_there(tools):
    """The interceptor overwrites _verified_*. Any other identity field is ignored."""
    status, body = call(tools, "approve_access", MAYA, grant_id=request(tools)[1]["grant_id"], approver_email="liam@robotinc.example")
    assert status == 403
    assert "maya@robotinc.example does not own" in body["error"]


def test_only_the_owner_decides_and_only_once(tools):
    grant_id = request(tools)[1]["grant_id"]
    assert call(tools, "approve_access", PRIYA, grant_id=grant_id)[0] == 403
    assert call(tools, "approve_access", LIAM, grant_id=grant_id)[0] == 200
    assert call(tools, "approve_access", LIAM, grant_id=grant_id)[0] == 409
    assert call(tools, "deny_access", LIAM, grant_id=grant_id)[0] == 409


def test_a_request_without_a_reason_or_with_too_long_a_window_is_a_400(tools):
    assert request(tools, reason=" ")[0] == 400
    assert request(tools, duration_hours=24 * 365)[0] == 400


def test_memory_follows_the_grant(tools):
    assert call(tools, "memory_write", LIAM, namespace=LEGAL, text="Export control 7A994 blocks Supplier X.")[0] == 200

    assert call(tools, "memory_search", MAYA, namespace=LEGAL, query="export control")[0] == 403

    grant_id = request(tools)[1]["grant_id"]
    assert call(tools, "memory_search", MAYA, namespace=LEGAL, query="export control")[0] == 403, "pending is not approved"
    call(tools, "approve_access", LIAM, grant_id=grant_id)

    status, body = call(tools, "memory_search", MAYA, namespace=LEGAL, query="export control")
    assert status == 200
    assert any("7A994" in r["text"] for r in body["records"])

    call(tools, "revoke_access", LIAM, grant_id=grant_id)
    assert call(tools, "memory_search", MAYA, namespace=LEGAL, query="export control")[0] == 403


def test_a_grant_never_writes(tools):
    grant_id = request(tools)[1]["grant_id"]
    call(tools, "approve_access", LIAM, grant_id=grant_id)

    status, body = call(tools, "memory_write", MAYA, namespace=LEGAL, text="Supplier X is cleared for export.")
    assert status == 403
    assert tools.backend.records.get(LEGAL, []) == []


def test_a_member_writes_to_their_own_and_their_departments_namespace(tools):
    assert call(tools, "memory_write", MAYA, namespace="org_robotinc_dept_engineering", text="note")[0] == 200
    assert call(tools, "memory_write", MAYA, namespace="org_robotinc_user_maya@robotinc.example", text="note")[0] == 200
    assert call(tools, "memory_write", MAYA, namespace="org_robotinc_dept_finance", text="note")[0] == 403


def test_blocked_content_is_a_422_and_nothing_is_stored(tools, monkeypatch):
    def refuse(text):
        raise ContentBlockedError("this content may not be stored: the Guardrail blocked it")

    monkeypatch.setattr(tools, "_memory", GuardedMemory(tools.backend, refuse, tools._grants))
    status, body = call(tools, "memory_write", LIAM, namespace=LEGAL, text="-----BEGIN RSA PRIVATE KEY-----")
    assert status == 422
    assert tools.backend.records.get(LEGAL, []) == []


def test_a_grant_carries_tools_only_when_asked_for(tools):
    plain = request(tools)[1]
    call(tools, "approve_access", LIAM, grant_id=plain["grant_id"])
    assert plain["include_tools"] is False
    assert "search_contracts" not in call(tools, "whoami", MAYA)[1]["domain_tools"]

    with_tools = request(tools, include_tools=True)[1]
    pending = call(tools, "list_pending", LIAM)[1]["pending"]
    assert [g["include_tools"] for g in pending] == [True], "the owner sees what is being asked for"
    call(tools, "approve_access", LIAM, grant_id=with_tools["grant_id"])
    assert "search_contracts" in call(tools, "whoami", MAYA)[1]["domain_tools"]


def test_an_unknown_tool_is_a_400(tools):
    assert call(tools, "delete_everything", LIAM)[0] == 400


def test_the_owner_sees_what_was_read_under_the_grant(tools):
    """N2: Liam approves, Maya reads, and Liam can see that she did and what she asked."""
    call(tools, "memory_write", LIAM, namespace=LEGAL, text="Export control 7A994 blocks Supplier X.")
    grant_id = request(tools)[1]["grant_id"]
    call(tools, "approve_access", LIAM, grant_id=grant_id)
    status, body = call(tools, "memory_search", MAYA, namespace=LEGAL, query="export control")
    assert body["records"][0]["access"]["via"] == "grant"
    assert body["records"][0]["access"]["approved_by"] == "liam@robotinc.example"
    call(tools, "memory_write", MAYA, namespace=LEGAL, text="It is EAR99.")

    status, body = call(tools, "grant_activity", LIAM, grant_id=grant_id)
    assert status == 200
    assert [e["event"] for e in body["activity"]] == ["requested", "approved", "read", "write_refused"]
    read = body["activity"][2]
    assert read["actor"] == "maya@robotinc.example" and read["query"] == "export control"

    assert call(tools, "grant_activity", MAYA, grant_id=grant_id)[0] == 200, "the requester may see her own grant"
    assert call(tools, "grant_activity", PRIYA, grant_id=grant_id)[0] == 403, "nobody else may"
