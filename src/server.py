"""The orchestrator, reduced to a health check.

M1 (see REVIEW.md): this used to carry put/retrieve/request_access/
list_pending/approve_access/deny_access, all trusting an explicit `claims`
dict in the request body instead of a verified JWT - any caller could assert
any email/org/department and read, write, or approve as anyone. That path
has been deleted. Memory access is now `memory_search`/`memory_write` and
the grant workflow is `request_access`/`list_pending`/`approve_access`/
`deny_access`/`revoke_access`/`whoami` - all eight real MCP tools on the
AccessRequestTools Gateway Target (src/tools/access_request_tools.py), using
`_verified_email`/`_verified_org`/`_verified_department` from
IdentityInterceptor, the same verified-identity rule the grant tools always
used. Tier One's only remaining job is proving the Runtime container is
alive; there's no privileged action left to reach by calling it.

This also means the Orchestrator Gateway Target's POST:/invocations is no
longer a path worth guarding with anything beyond /ping - it has nothing to
hand out. Kept as a bare HTTP server rather than removed outright, as a stub
for N6 (REVIEW.md: "an agent that actually calls a model and uses memory in
conversation") - that work needs a long-running container to put a real
agent loop in, and this is already wired into the Runtime/Gateway/ECR
pipeline.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer


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

        self._respond(410, {
            "ok": False,
            "error": (
                "this orchestrator no longer handles memory or grant actions - use the "
                "memory_search/memory_write/request_access/list_pending/approve_access/"
                "deny_access/revoke_access/whoami MCP tools on the Gateway instead"
            ),
        })

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
