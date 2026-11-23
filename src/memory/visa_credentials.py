"""The visa as a real AWS credential.

GuardedMemory decides WHO may read a namespace. This module makes AWS enforce
that decision: every read of long-term memory uses short-lived credentials whose
session policy allows one namespace, until one moment in time, and nothing else.

Why it matters: the Python check in guarded_memory.py is one `if`. If it has a
bug, or another piece of code runs with the orchestrator's role, memory is open.
With these credentials, a read outside the namespace, or after the grant's end,
is refused by IAM itself - AccessDenied from AWS, not a PermissionError from us.

How it works:

  1. The caller's own role has no permission to read memory. It may only assume
     the visa role (created in cdk/stacks/agentcore_stack.py).
  2. The visa role may read any namespace. Nobody uses it as it is.
  3. For each read, `VisaIssuer.issue` assumes the visa role with a SESSION
     POLICY. A session policy can only narrow a role, never widen it. Ours
     allows the read actions when both conditions hold:
       - bedrock-agentcore:namespace is the one namespace asked for
       - aws:CurrentTime is before the grant's end
  4. The credentials are used once, on the server. They are never sent to the
     AI client.

The time condition is what ends the visa. STS credentials live for at least 15
minutes, so their own expiry cannot end a 2-minute grant. aws:CurrentTime can:
it is checked by IAM on every call, to the second.

Audit: the role session name carries the grant ID, and the session tags carry the
grant ID and the requester, so CloudTrail shows which grant each read was made under.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass

# The read side of long-term memory. All three accept bedrock-agentcore:namespace
# as a condition key (IAM service authorization reference for Bedrock AgentCore).
READ_ACTIONS = (
    "bedrock-agentcore:RetrieveMemoryRecords",
    "bedrock-agentcore:ListMemoryRecords",
    "bedrock-agentcore:GetMemoryRecord",
)

# STS limits for AssumeRole. The credential's own life is clamped to these; the
# exact end of the visa is the aws:CurrentTime condition in the session policy.
MIN_SESSION_SECONDS = 900
MAX_SESSION_SECONDS = 3600

# How long a read of the caller's OWN namespaces (no grant involved) may last.
DEFAULT_STATIC_WINDOW = dt.timedelta(minutes=15)

_SESSION_NAME_UNSAFE = re.compile(r"[^\w+=,.@-]")


def namespace_condition_values(namespace: str) -> list[str]:
    """The values the namespace condition must accept for one namespace.

    AgentCore Memory stores a namespace with one leading and one trailing slash,
    and IAM compares against that form. This project's namespaces are flat
    strings today (org_robotinc_dept_legal), so both the string as sent and its
    stored form are listed. Both name the same namespace; this does not widen
    anything. Drop the first value once the namespaces are paths.
    """
    stored = "/" + namespace.strip("/") + "/"
    return [namespace] if namespace == stored else [namespace, stored]


def session_policy(memory_arn: str, namespace: str, not_after: dt.datetime) -> dict:
    """Allow reads of one namespace in one memory, until one moment."""
    if not_after.tzinfo is None:
        raise ValueError("not_after must be timezone-aware")
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ReadOneNamespaceUntilTheVisaEnds",
                "Effect": "Allow",
                "Action": list(READ_ACTIONS),
                "Resource": memory_arn,
                "Condition": {
                    "StringEquals": {"bedrock-agentcore:namespace": namespace_condition_values(namespace)},
                    "DateLessThan": {
                        "aws:CurrentTime": not_after.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    },
                },
            }
        ],
    }


@dataclass(frozen=True)
class Visa:
    """Credentials for one namespace, with the policy that limits them."""

    namespace: str
    not_after: dt.datetime
    access_key_id: str
    secret_access_key: str
    session_token: str
    policy: dict
    session_name: str

    def client(self, region: str):
        import boto3  # imported here so the offline eval suite needs no AWS SDK

        return boto3.client(
            "bedrock-agentcore",
            region_name=region,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            aws_session_token=self.session_token,
        )


class VisaIssuer:
    def __init__(self, role_arn: str, memory_arn: str, sts_client=None) -> None:
        self._role_arn = role_arn
        self._memory_arn = memory_arn
        if sts_client is None:
            import boto3  # imported here so the offline eval suite needs no AWS SDK

            sts_client = boto3.client("sts")
        self._sts = sts_client

    def issue(
        self,
        namespace: str,
        not_after: dt.datetime,
        now: dt.datetime,
        requester: str,
        grant_id: str | None = None,
    ) -> Visa:
        """Credentials that read `namespace` until `not_after`. Raises if that is already past."""
        remaining = int((not_after - now).total_seconds())
        if remaining <= 0:
            raise ValueError("the visa has already ended")

        policy = session_policy(self._memory_arn, namespace, not_after)
        # "static" marks a read of the caller's own namespaces: no grant was needed.
        label = grant_id or "static"
        session_name = _SESSION_NAME_UNSAFE.sub("-", f"visa-{label}")[:64]

        response = self._sts.assume_role(
            RoleArn=self._role_arn,
            RoleSessionName=session_name,
            DurationSeconds=max(MIN_SESSION_SECONDS, min(remaining, MAX_SESSION_SECONDS)),
            Policy=json.dumps(policy, separators=(",", ":")),
            Tags=[
                {"Key": "grant_id", "Value": label},
                {"Key": "requester", "Value": requester[:256]},
            ],
        )
        credentials = response["Credentials"]
        return Visa(
            namespace=namespace,
            not_after=not_after,
            access_key_id=credentials["AccessKeyId"],
            secret_access_key=credentials["SecretAccessKey"],
            session_token=credentials["SessionToken"],
            policy=policy,
            session_name=session_name,
        )
