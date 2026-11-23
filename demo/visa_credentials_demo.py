#!/usr/bin/env python3
"""The proof that AWS, not our Python, enforces a visa.

Mints credentials for ONE namespace that end in N seconds, then makes three calls to
AgentCore Memory with them, straight to the AWS API - no GuardedMemory in the way:

  1. the visa's own namespace           -> allowed
  2. a different namespace              -> AccessDenied, from IAM
  3. the visa's own namespace, after N  -> AccessDenied, from IAM

Needs the deployed stack, and your own role in the visa role's trust policy:

    cdk deploy MemoryVisaAgentCore -c visaDemoPrincipalArn=<your role ARN>

    python3 demo/visa_credentials_demo.py \
        --namespace org_robotinc_dept_legal --other org_robotinc_dept_finance --seconds 60

The stack's values are read from Parameter Store (/memory-visa/...).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memory.visa_credentials import VisaIssuer  # noqa: E402


def _parameter(ssm, name: str) -> str:
    return ssm.get_parameter(Name=f"/memory-visa/{name}")["Parameter"]["Value"]


def _read(client, memory_id: str, namespace: str, query: str) -> str:
    try:
        response = client.retrieve_memory_records(
            memoryId=memory_id, namespace=namespace, searchCriteria={"searchQuery": query, "topK": 3}
        )
    except ClientError as error:
        return f"REFUSED by AWS: {error.response['Error']['Code']}"
    records = response.get("memoryRecordSummaries", [])
    first = records[0].get("content", {}).get("text", "") if records else ""
    return f"ALLOWED: {len(records)} record(s)" + (f', first: "{first[:80]}"' if first else "")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--namespace", required=True, help="the namespace the visa is for")
    parser.add_argument("--other", required=True, help="a namespace the visa is NOT for")
    parser.add_argument("--seconds", type=int, default=60, help="how long the visa lasts")
    parser.add_argument("--query", default="export control")
    parser.add_argument("--requester", default="maya@robotinc.example")
    parser.add_argument("--region", default="eu-west-2")
    args = parser.parse_args()

    ssm = boto3.client("ssm", region_name=args.region)
    memory_id, memory_arn, role_arn = (_parameter(ssm, n) for n in ("memory-id", "memory-arn", "visa-role-arn"))

    now = dt.datetime.now(dt.timezone.utc)
    not_after = now + dt.timedelta(seconds=args.seconds)
    issuer = VisaIssuer(role_arn, memory_arn, sts_client=boto3.client("sts", region_name=args.region))
    visa = issuer.issue(args.namespace, not_after, now, requester=args.requester, grant_id="demo")
    client = visa.client(args.region)

    print(f"Visa for {args.namespace}, ends {not_after:%H:%M:%S} UTC. Session: {visa.session_name}")
    print("The session policy AWS will enforce:")
    print(json.dumps(visa.policy, indent=2))
    print()
    print(f"1. {args.namespace:32s} {_read(client, memory_id, args.namespace, args.query)}")
    print(f"2. {args.other:32s} {_read(client, memory_id, args.other, args.query)}")

    wait = (not_after - dt.datetime.now(dt.timezone.utc)).total_seconds() + 5
    print(f"\nWaiting {wait:.0f}s for the visa to end. The same credentials are used again...")
    time.sleep(max(wait, 0))
    print(f"3. {args.namespace:32s} {_read(client, memory_id, args.namespace, args.query)}")


if __name__ == "__main__":
    main()
