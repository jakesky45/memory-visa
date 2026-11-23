#!/usr/bin/env python3
"""The proof that AWS, not our Python, keeps a direct record write inside a published
namespace (REVIEW.md, P6).

The tools Lambda may call BatchCreateMemoryRecords only where the namespace is like
org_*_published. Its Python refuses any other publish first, so no tool call can show
IAM doing it. This script assumes a role that has that one statement and nothing
else, and calls AWS directly:

  1. a published namespace          -> allowed
  2. a department's own namespace   -> AccessDenied, from IAM
  3. a person's own namespace       -> AccessDenied, from IAM

Needs the deployed stack, with the demo role turned on for your own role:

    cdk deploy MemoryVisaAgentCore -c visaDemoPrincipalArn=<your role ARN>

    python3 demo/publish_boundary_demo.py

The record from call 1 goes to org_demo_published, which no user belongs to, and the
script deletes it again with your own credentials.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import uuid

import boto3
from botocore.exceptions import ClientError


def _parameter(ssm, name: str) -> str:
    return ssm.get_parameter(Name=f"/memory-visa/{name}")["Parameter"]["Value"]


def _write(client, memory_id: str, namespace: str) -> tuple[str, str | None]:
    """Try one direct write. Returns the line to print and the record ID, if one was made."""
    record = {
        "requestIdentifier": uuid.uuid4().hex,
        "namespaces": [namespace],
        "content": {"text": "Boundary proof record. Safe to delete."},
        "timestamp": dt.datetime.now(dt.timezone.utc),
    }
    try:
        response = client.batch_create_memory_records(memoryId=memory_id, records=[record])
    except ClientError as error:
        return f"REFUSED by AWS: {error.response['Error']['Code']}", None
    failed = response.get("failedRecords", [])
    if failed:
        return f"FAILED, not by IAM: {failed[0].get('errorMessage', failed[0])}", None
    record_id = response.get("successfulRecords", [{}])[0].get("memoryRecordId")
    return f"ALLOWED: record {record_id}", record_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--published", default="org_demo_published", help="a published namespace")
    parser.add_argument("--department", default="org_robotinc_dept_legal", help="a department's own namespace")
    parser.add_argument("--person", default="org_robotinc_user_maya_at_robotinc_dot_example", help="a person's own namespace")
    parser.add_argument("--region", default="eu-west-2")
    args = parser.parse_args()

    ssm = boto3.client("ssm", region_name=args.region)
    memory_id = _parameter(ssm, "memory-id")
    role_arn = _parameter(ssm, "publisher-demo-role-arn")

    sts = boto3.client("sts", region_name=args.region)
    credentials = sts.assume_role(RoleArn=role_arn, RoleSessionName="publish-boundary-demo", DurationSeconds=900)["Credentials"]
    narrow = boto3.client(
        "bedrock-agentcore",
        region_name=args.region,
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )

    iam = boto3.client("iam")
    role_name = role_arn.rsplit("/", 1)[-1]
    print(f"Role {role_name}. Its whole permission, the same statement the tools Lambda has:")
    for name in iam.list_role_policies(RoleName=role_name)["PolicyNames"]:
        document = iam.get_role_policy(RoleName=role_name, PolicyName=name)["PolicyDocument"]
        print(json.dumps(document["Statement"], indent=2))
    print()

    created = None
    for number, namespace in enumerate((args.published, args.department, args.person), 1):
        line, record_id = _write(narrow, memory_id, namespace)
        print(f"{number}. {namespace:48s} {line}")
        if record_id and namespace == args.published:
            created = record_id
        elif record_id:
            print(f"   UNEXPECTED: a record was written outside a published namespace. Delete {record_id} and tell Claude.")

    if created:
        try:
            boto3.client("bedrock-agentcore", region_name=args.region).delete_memory_record(
                memoryId=memory_id, memoryRecordId=created
            )
            print(f"\nDeleted the proof record {created} with your own credentials.")
        except Exception as error:  # the proof is done; a failed clean-up must not hide it
            print(f"\nCould not delete the proof record {created}: {type(error).__name__}. It is harmless.")


if __name__ == "__main__":
    main()
