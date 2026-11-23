#!/usr/bin/env python3
"""The temporal (Dogwood) policy experiment. See REVIEW.md, "Exploring: let Dogwood
enforce the grant".

    cd cdk && npx cdk --app "python3 spike_app.py" deploy MemoryVisaDogwoodSpike && cd ..
    python3 spike/dogwood_spike.py policies     # create the two temporal policies
    python3 spike/dogwood_spike.py test         # run the checks and print a table
    python3 spike/dogwood_spike.py teardown     # delete the policies and the two test users
    cd cdk && npx cdk --app "python3 spike_app.py" destroy MemoryVisaDogwoodSpike

`test` makes two throwaway Cognito users (spike-a, spike-b) with a random password it
does not store, so it needs no existing password. Needs boto3 1.44 or later.
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
import urllib.error
import urllib.request
import uuid

import boto3
from botocore.exceptions import ClientError

REGION = "eu-west-2"
WINDOW = "1m"
USERS = ("spike-a", "spike-b")

# A: the read needs a completed check_grant for the same namespace, in this session.
#    Correlates on the input only, so it needs no output schema.
# B: the same, and the check's output must say active. Needs the declared output schema.
POLICIES = {
    "ReadNeedsCheckedGrant": """permit (
    principal is AgentCore::OAuthUser,
    action == AgentCore::Action::"SpikeTools___visa_read_a",
    resource == AgentCore::Gateway::"{gateway_arn}"
)
when temporal {{
    formerly within {window} AgentCore::Action::"SpikeTools___check_grant"::response{{
        eventResource: resource,
        input.namespace: context.input.namespace
    }}
}};""",
    "ReadNeedsActiveGrant": """permit (
    principal is AgentCore::OAuthUser,
    action == AgentCore::Action::"SpikeTools___visa_read_b",
    resource == AgentCore::Gateway::"{gateway_arn}"
)
when temporal {{
    formerly within {window} AgentCore::Action::"SpikeTools___check_grant"::response{{
        eventResource: resource,
        input.namespace: context.input.namespace,
        output.active: true
    }}
}};""",
}


def parameter(ssm, name: str) -> str:
    return ssm.get_parameter(Name=f"/memory-visa/{name}")["Parameter"]["Value"]


def create_policies() -> int:
    ssm = boto3.client("ssm", region_name=REGION)
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    engine_id, gateway_arn = parameter(ssm, "spike/policy-engine-id"), parameter(ssm, "spike/gateway-arn")
    failed = 0
    for name, template in POLICIES.items():
        statement = template.format(gateway_arn=gateway_arn, window=WINDOW)
        print(f"--- {name}\n{statement}")
        try:
            created = control.create_policy(
                policyEngineId=engine_id,
                name=name,
                validationMode="FAIL_ON_ANY_FINDINGS",
                enforcementMode="ACTIVE",
                definition={"policy": {"statement": statement}},
            )
        except ClientError as error:
            failed += 1
            print(f"NOT CREATED: {error.response['Error']['Code']}: {error.response['Error']['Message']}\n")
            continue
        policy_id = created["policyId"]
        status = created.get("status")
        for _ in range(60):
            got = control.get_policy(policyEngineId=engine_id, policyId=policy_id)
            status = got.get("status")
            if status not in ("CREATING", "UPDATING"):
                break
            time.sleep(3)
        print(f"{policy_id}: {status} {json.dumps(got.get('statusReasons', []))}\n")
        if status != "ACTIVE":
            failed += 1
    return failed


def delete_policies() -> None:
    ssm = boto3.client("ssm", region_name=REGION)
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    engine_id = parameter(ssm, "spike/policy-engine-id")
    for policy in control.list_policies(policyEngineId=engine_id).get("policies", []):
        if policy.get("name") in POLICIES:
            control.delete_policy(policyEngineId=engine_id, policyId=policy["policyId"])
            print(f"deleted policy {policy['name']}")
    cognito = boto3.client("cognito-idp", region_name=REGION)
    pool = parameter(ssm, "user-pool-id")
    for user in USERS:
        try:
            cognito.admin_delete_user(UserPoolId=pool, Username=user)
            print(f"deleted user {user}")
        except ClientError as error:
            if error.response["Error"]["Code"] != "UserNotFoundException":
                raise


def tokens() -> dict[str, str]:
    """Two throwaway users in the main stack's pool. The password is random and not kept."""
    ssm = boto3.client("ssm", region_name=REGION)
    cognito = boto3.client("cognito-idp", region_name=REGION)
    pool, client = parameter(ssm, "user-pool-id"), parameter(ssm, "user-client-id")
    out = {}
    for user in USERS:
        password = "Sp1ke-" + secrets.token_urlsafe(18)
        try:
            cognito.admin_create_user(UserPoolId=pool, Username=user, MessageAction="SUPPRESS")
        except ClientError as error:
            if error.response["Error"]["Code"] != "UsernameExistsException":
                raise
        cognito.admin_set_user_password(UserPoolId=pool, Username=user, Password=password, Permanent=True)
        auth = cognito.initiate_auth(
            ClientId=client, AuthFlow="USER_PASSWORD_AUTH", AuthParameters={"USERNAME": user, "PASSWORD": password}
        )
        out[user] = auth["AuthenticationResult"]["AccessToken"]
    return out


class Gateway:
    def __init__(self, url: str) -> None:
        self._url = url.rstrip("/") + "/mcp"
        self._id = 0

    def call(self, token: str, tool: str, namespace: str, session: str | None) -> tuple[str, str]:
        """Returns ("ALLOW" | "DENY" | "TOOL-ERROR" | "HTTP <code>", detail)."""
        self._id += 1
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if session:
            headers["x-amzn-bedrock-agentcore-policy-session-id"] = session
        body = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": "tools/call",
            "params": {"name": f"SpikeTools___{tool}", "arguments": {"namespace": namespace}},
        }
        request = urllib.request.Request(self._url, data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read().decode()
        except urllib.error.HTTPError as error:
            return f"HTTP {error.code}", error.read().decode()[:300]
        text = raw.strip()
        if not text.startswith("{"):
            data = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
            text = data[-1] if data else text
        try:
            answer = json.loads(text)
        except json.JSONDecodeError:
            return "UNREADABLE", text[:300]
        if "error" in answer:
            return "DENY", json.dumps(answer["error"])[:300]
        result = answer.get("result", {})
        detail = json.dumps(result)[:300]
        return ("TOOL-ERROR" if result.get("isError") else "ALLOW"), detail


def run_tests() -> int:
    ssm = boto3.client("ssm", region_name=REGION)
    gateway = Gateway(parameter(ssm, "spike/gateway-url"))
    token = tokens()
    a, b = token["spike-a"], token["spike-b"]
    rows = []

    def new() -> str:
        return str(uuid.uuid4())

    def settle() -> None:
        time.sleep(3)  # a response event is recorded shortly after the call completes

    def record(number, what, expect, got, detail, also=""):
        rows.append((number, what, expect, got, detail, also))
        print(f"{number:>2}. {what}\n    expected {expect}, got {got}{'   <-- DIFFERENT' if got != expect else ''}\n    {detail}\n    {also}".rstrip())

    # 0. the prerequisite alone
    s = new()
    got, detail = gateway.call(a, "check_grant", "org_robotinc_dept_legal", s)
    record(0, "A: check_grant, with a session ID", "ALLOW", got, detail)

    # 1. check, then read, same person, same session
    settle()
    got, detail = gateway.call(a, "visa_read_a", "org_robotinc_dept_legal", s)
    record(1, "A: check_grant then visa_read_a, same namespace, same session", "ALLOW", got, detail)

    # 2. read with no check, new session
    got, detail = gateway.call(a, "visa_read_a", "org_robotinc_dept_legal", new())
    record(2, "A: visa_read_a in a new session, no check", "DENY", got, detail)

    # 3. the check was for another namespace
    got, detail = gateway.call(a, "visa_read_a", "org_robotinc_dept_finance", s)
    record(3, "A: check_grant for legal, then visa_read_a for finance, same session", "DENY", got, detail)

    # 4. THE QUESTION: does a policy on A's request see B's event in the same session ID?
    shared = new()
    got_b, detail_b = gateway.call(b, "check_grant", "org_robotinc_dept_legal", shared)
    settle()
    got, detail = gateway.call(a, "visa_read_a", "org_robotinc_dept_legal", shared)
    record(
        4,
        "B: check_grant in session X. Then A: visa_read_a in session X",
        "DENY",
        got,
        detail,
        f"(B's check: {got_b}). DENY = sessions are per caller, as the AWS page says. ALLOW = one session holds two callers.",
    )

    # 5. a check that fails: is it a response?
    s5 = new()
    got_c, detail_c = gateway.call(a, "check_grant", "error_ns", s5)
    settle()
    got, detail = gateway.call(a, "visa_read_a", "error_ns", s5)
    record(5, "A: check_grant fails (the tool raises), then visa_read_a", "DENY", got, detail, f"(the failed check: {got_c} {detail_c[:120]})")

    # 6. no session ID at all
    got, detail = gateway.call(a, "check_grant", "org_robotinc_dept_legal", None)
    record(6, "A: check_grant with no session ID header", "DENY", got, detail, "AWS: with a temporal policy on the engine, this fails validation.")

    # 7 and 8. the output field
    s7 = new()
    gateway.call(a, "check_grant", "org_robotinc_dept_legal", s7)
    settle()
    got, detail = gateway.call(a, "visa_read_b", "org_robotinc_dept_legal", s7)
    record(7, "A: check_grant returns active:true, then visa_read_b", "ALLOW", got, detail)

    s8 = new()
    got_c, detail_c = gateway.call(a, "check_grant", "inactive_ns", s8)
    settle()
    got, detail = gateway.call(a, "visa_read_b", "inactive_ns", s8)
    record(8, "A: check_grant returns active:false, then visa_read_b", "DENY", got, detail, f"(the check: {got_c} {detail_c[:120]})")

    # 9. the window
    s9 = new()
    gateway.call(a, "check_grant", "org_robotinc_dept_legal", s9)
    print(f"\nwaiting 75s for the {WINDOW} window to pass...")
    time.sleep(75)
    got, detail = gateway.call(a, "visa_read_a", "org_robotinc_dept_legal", s9)
    record(9, f"A: check_grant, wait past the {WINDOW} window, then visa_read_a", "DENY", got, detail)

    print("\n=== summary ===")
    for number, what, expect, got, _, _ in rows:
        print(f"{number:>2}  {'ok       ' if got == expect else 'DIFFERENT'}  expected {expect:5s} got {got:10s}  {what}")
    return sum(1 for r in rows if r[2] != r[3])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=("policies", "test", "teardown"))
    command = parser.parse_args().command
    if command == "policies":
        sys.exit(1 if create_policies() else 0)
    if command == "test":
        sys.exit(1 if run_tests() else 0)
    delete_policies()
