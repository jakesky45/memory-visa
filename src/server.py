"""The real orchestrator, replacing the /ping+/invocations stub.

Identity note: the request body carries `claims` explicitly for now, not a
verified JWT - real JWT verification belongs at the Gateway (CUSTOM_JWT,
already configured) or an MCP-layer step, not reinvented here. This is the
orchestrator proving out GuardedMemory/access_requests end to end against
the real AgentCore Memory backend; wiring real verified identity through is
the MCP step that follows.

Cost attribution: a structured JSON log line per memory operation (org,
department, action, namespace) - the healthcare sample's documented pattern,
achieved here with zero extra AWS resources (CloudWatch Logs Insights can
already query it), rather than standing up Bedrock Projects for a demo.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

from agents.access_requests import (
    NotDomainOwnerError,
    approve_request,
    deny_request,
    pending_requests_for_owner,
    request_cross_domain_access,
)
from memory.agentcore_backend import AgentCoreMemoryBackend
from memory.grants_dynamodb import DynamoGrantStore
from memory.guarded_memory import GuardedMemory, PermissionError as GuardedMemoryPermissionError, UserClaims
from memory.visa_credentials import VisaIssuer

MEMORY_ID = os.environ.get("MEMORY_ID", "")
REGION = os.environ.get("AWS_REGION", "eu-west-2")
GRANTS_TABLE = os.environ.get("GRANTS_TABLE_NAME", "")
GRANTS_AUDIT_TABLE = os.environ.get("GRANTS_AUDIT_TABLE_NAME", "")
VISA_ROLE_ARN = os.environ.get("VISA_ROLE_ARN", "")
MEMORY_ARN = os.environ.get("MEMORY_ARN", "")

# Same DynamoDB-backed store the tier-two MCP tools (AccessRequestTools) read
# and write - not a second, disconnected in-process one. A grant Liam
# approves through his own Claude session is the same grant this orchestrator
# checks before letting Maya's retrieve through.
_grants = DynamoGrantStore(GRANTS_TABLE, GRANTS_AUDIT_TABLE, region=REGION) if GRANTS_TABLE else None
_apply_guardrail = lambda text: text  # the Bedrock Guardrail attaches at the model-invocation layer, not here
# Reads of long-term memory go through a visa: credentials AWS limits to one namespace
# until the grant ends (memory/visa_credentials.py). Without the role, reads use this
# process's own role, as before.
_visa_issuer = VisaIssuer(VISA_ROLE_ARN, MEMORY_ARN) if VISA_ROLE_ARN and MEMORY_ARN else None
_memory = (
    GuardedMemory(AgentCoreMemoryBackend(MEMORY_ID, REGION, visa_issuer=_visa_issuer), _apply_guardrail, _grants)
    if MEMORY_ID and _grants
    else None
)


def _log_usage(claims: UserClaims, action: str, namespace: str, query: str | None = None) -> None:
    entry = {
        "email": claims.email,
        "org": claims.org,
        "departments": list(claims.departments),
        "action": action,
        "namespace": namespace,
        "at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    if query is not None:
        entry["query"] = query
    print(json.dumps(entry))


def _claims_from(payload: dict) -> UserClaims:
    c = payload["claims"]
    return UserClaims(
        email=c["email"],
        org=c["org"],
        departments=tuple(c.get("departments", [])),
        projects=tuple(c.get("projects", [])),
    )


def _handle_invocation(payload: dict) -> dict:
    action = payload.get("action")
    now = dt.datetime.now(dt.timezone.utc)

    if action == "put":
        claims = _claims_from(payload)
        _log_usage(claims, action, payload["namespace"])
        result = _memory.put(claims, payload["namespace"], payload["text"], now)
        return {"ok": True, "result": result}

    if action == "retrieve":
        claims = _claims_from(payload)
        _log_usage(claims, action, payload["namespace"], query=payload["query"])
        result = _memory.retrieve(claims, payload["namespace"], payload["query"], now)
        return {"ok": True, "result": result}

    if action == "request_access":
        claims = _claims_from(payload)
        grant = request_cross_domain_access(
            _grants,
            claims,
            target_org=payload["target_org"],
            target_department=payload["target_department"],
            reason=payload["reason"],
            duration=dt.timedelta(hours=payload.get("duration_hours", 2)),
            now=now,
        )
        return {"ok": True, "grant_id": grant.grant_id, "status": grant.status.value}

    if action == "list_pending":
        pending = pending_requests_for_owner(_grants, payload["owner_email"])
        return {"ok": True, "pending": [{"grant_id": g.grant_id, "reason": g.reason} for g in pending]}

    if action == "approve_access":
        grant = approve_request(_grants, payload["grant_id"], payload["approver_email"], now)
        return {"ok": True, "status": grant.status.value}

    if action == "deny_access":
        grant = deny_request(_grants, payload["grant_id"], payload["approver_email"], now)
        return {"ok": True, "status": grant.status.value}

    return {"ok": False, "error": f"unknown action '{action}'"}


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

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body or b"{}")
            result = _handle_invocation(payload)
            self._respond(200, result)
        except (GuardedMemoryPermissionError, NotDomainOwnerError) as exc:
            self._respond(403, {"ok": False, "error": str(exc)})
        except KeyError as exc:
            self._respond(400, {"ok": False, "error": f"missing required field: {exc}"})
        except Exception as exc:  # noqa: BLE001 - deliberately broad: an
            # unhandled exception here must still produce an HTTP response.
            # Letting it propagate out of do_POST crashes the single-threaded
            # handler with no response sent at all, which the AgentCore
            # proxy reports as a flat 502 - confirmed by hitting exactly that
            # on the first real invocation, with nothing useful in the logs
            # to explain it. A 500 with the real error is strictly better.
            import traceback

            traceback.print_exc()
            self._respond(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

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
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
