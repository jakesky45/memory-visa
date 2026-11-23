"""The two tools behind the Dogwood spike Gateway (REVIEW.md, "Exploring: let Dogwood
enforce the grant"). They hold no data. They exist so a temporal policy has something
to order: check_grant first, then a read.

  check_grant(namespace)   returns {"active": true}. A namespace that starts with
                           "inactive_" returns {"active": false}. One that starts with
                           "error_" makes the tool fail, to see how a failure is recorded.
  visa_read_a(namespace)   a read, gated on an earlier check for the same namespace
  visa_read_b(namespace)   the same, gated on the check's output saying active

Returned as a plain object, not {"statusCode", "body"}, so a declared output schema
can describe it.
"""
from __future__ import annotations


def handler(event, context):
    tool = context.client_context.custom["bedrockAgentCoreToolName"].split("___", 1)[-1]
    namespace = event.get("namespace", "")
    if tool == "check_grant":
        if namespace.startswith("error_"):
            raise RuntimeError(f"no grant for {namespace}")
        return {"active": not namespace.startswith("inactive_"), "namespace": namespace}
    return {"read": namespace, "tool": tool}
