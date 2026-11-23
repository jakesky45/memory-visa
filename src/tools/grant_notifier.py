"""Closes the pull-only gap, in both directions: a DynamoDB Streams trigger
on GrantsTable fires this Lambda on every grant write, and it posts to Slack
via an Incoming Webhook - no more "Oleg only finds out if he thinks to ask,"
and no more "Maya has no idea whether Oleg ever looked at it."

Reacts to the grant's new status, not the DynamoDB event type - `pending`
notifies the owner there's something to approve, `approved`/`denied` tells
the requester the outcome. Each status is only ever written once per grant by
`agents.access_requests` (approve/deny/revoke each set a distinct, terminal
status), so no OldImage comparison is needed to avoid double-notifying.

Demo note: the DOMAIN_OWNERS addresses (maya@robotinc.example, etc.) are
fictional - nothing could actually @-mention them in Slack. The real webhook
URL that posts during the demo comes from an SSM parameter (its name, not
the URL, is the only thing in this template or git) - the Lambda's own logic
still resolves and states the real intended owner in the message, it just
posts to one demo channel rather than paging that person directly.

Plain `urllib.request`, not the `slack_sdk` package: an Incoming Webhook is
just an HTTPS POST of a JSON body, and this project's Lambdas don't bundle
any pip dependency beyond the boto3 that's already in the runtime.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import urllib.request

import boto3

from agents.domain_owners import owner_of

SLACK_WEBHOOK_PARAM = os.environ["SLACK_WEBHOOK_PARAM"]

_ssm = boto3.client("ssm")
_webhook_url = _ssm.get_parameter(Name=SLACK_WEBHOOK_PARAM, WithDecryption=True)["Parameter"]["Value"]


def _deserialize(image: dict) -> dict:
    """Minimal DynamoDB-JSON -> plain dict, string and number attributes both -
    this table's status/email/reason fields are S, duration_seconds is N."""
    out = {}
    for key, value in image.items():
        if "S" in value:
            out[key] = value["S"]
        elif "N" in value:
            out[key] = value["N"]
    return out


def _expires_at(grant: dict) -> str:
    approved_at = dt.datetime.fromisoformat(grant["approved_at"])
    duration = dt.timedelta(seconds=int(grant["duration_seconds"]))
    return (approved_at + duration).strftime("%H:%M UTC")


def _post_to_slack(text: str) -> None:
    body = json.dumps({"text": text}).encode()
    request = urllib.request.Request(
        _webhook_url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        response.read()


def handler(event, context):
    sent = 0
    for record in event.get("Records", []):
        if record.get("eventName") not in ("INSERT", "MODIFY"):
            continue

        grant = _deserialize(record["dynamodb"]["NewImage"])
        status = grant.get("status")
        if status not in ("pending", "approved", "denied"):
            continue

        try:
            _, org, _, department = grant["target_namespace"].split("_", 3)
        except ValueError:
            continue
        requester = grant["requester_namespace"].split("_user_", 1)[-1]

        if status == "pending":
            owner_email = owner_of(org, department)
            if not owner_email:
                continue
            _post_to_slack(
                f":lock: *{requester}* requested access into *{org}/{department}*\n"
                f"Reason: {grant['reason']}\n"
                f"Grant ID: `{grant['grant_id']}`\n"
                f"Intended owner: {owner_email}\n"
                f'Say to your Claude session: "approve grant {grant["grant_id"]}"'
                f' (or "deny grant {grant["grant_id"]}")'
            )
        elif status == "approved":
            _post_to_slack(
                f":white_check_mark: *{requester}*'s request into *{org}/{department}* was "
                f"*approved* by {grant.get('approved_by', 'unknown')}. "
                f"Window closes at {_expires_at(grant)}."
            )
        elif status == "denied":
            _post_to_slack(
                f":x: *{requester}*'s request into *{org}/{department}* was "
                f"*denied* by {grant.get('approved_by', 'unknown')}."
            )
        sent += 1

    return {"notified": sent}
