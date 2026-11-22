"""The one real Gateway Target tool: check_shipping_classification.

Deliberately a stub, not a real export-control database lookup - this exists
to give Cedar's auto-generated schema a real action
(AgentCore::Action::"EngineeringTools___check_shipping_classification") to
validate the domain-gating policy against. Named for what Engineering would
actually use it for in this story - checking a shipment's classification
before it goes out - rather than an unrelated placeholder.
"""
from __future__ import annotations

import json


def handler(event, context):
    shipment = event.get("shipment", "unspecified")
    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "tool": "check_shipping_classification",
                "shipment": shipment,
                "classification_summary": f"stub lookup for '{shipment}' - no real export-control database behind this",
                "note": "Placeholder tool - proves the Gateway Target/Cedar wiring, not a real classification lookup.",
            }
        ),
    }
