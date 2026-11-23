#!/usr/bin/env python3
"""Talk to the agent: demo beats 1 and 2 (short-term and long-term memory).

Runs the same agent as the Runtime container (src/agents/chat_agent.py), on your
machine. The model is called on Amazon Bedrock with your AWS credentials. Memory is
reached through the Gateway with the user's own token, so every check is the
deployed one: identity, grant, Guardrail, visa.

    pip install boto3 "anthropic[bedrock]"
    MV_PASSWORD='<password>' python3 demo/agent_chat.py --user maya

Each memory call the agent makes is printed under its reply. Type /new to start a
new conversation (the agent forgets the transcript and keeps only long-term memory),
and /quit to stop.

To talk to the deployed container and not the local loop, give its URL:

    python3 demo/agent_chat.py --user maya --url "$GATEWAY_URL/invocations"

The stack's values are read from Parameter Store (/memory-visa/...).
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agents.chat_agent import ChatAgent, make_model_client  # noqa: E402
from agents.gateway_client import GatewayClient, GatewayError  # noqa: E402


def _parameter(ssm, name: str) -> str:
    return ssm.get_parameter(Name=f"/memory-visa/{name}")["Parameter"]["Value"]


def _token(region: str, client_id: str, user: str) -> str:
    password = os.environ.get("MV_PASSWORD") or getpass.getpass(f"Password for {user}: ")
    cognito = boto3.client("cognito-idp", region_name=region)
    result = cognito.initiate_auth(
        ClientId=client_id,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": user, "PASSWORD": password},
    )
    return result["AuthenticationResult"]["AccessToken"]


def _show_calls(calls: list[dict]) -> None:
    for call in calls:
        mark = "ok" if 200 <= call["status"] < 300 else "REFUSED"
        print(f"    [{call['tool']} {json.dumps(call['arguments'])} -> {call['status']} {mark}]")
        if mark == "REFUSED":
            print(f"    [{json.dumps(call['result'])}]")


def _remote_turn(url: str, token: str, session_id: str, prompt: str) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps({"prompt": prompt, "access_token": token, "session_id": session_id}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"HTTP {exc.code}: {exc.read().decode()[:300]}"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user", default="maya", help="Cognito username (maya, liam, priya)")
    parser.add_argument("--token", help="an access token, if you already have one")
    parser.add_argument("--url", help="POST to the deployed agent at this URL; default is the local loop")
    parser.add_argument("--model", help="Bedrock model ID; default anthropic.claude-opus-5-5")
    parser.add_argument(
        "--provider",
        choices=("bedrock", "anthropic"),
        default="bedrock",
        help="anthropic: call the Claude API with ANTHROPIC_API_KEY, when the AWS account cannot call a model",
    )
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "eu-west-2"))
    args = parser.parse_args()

    ssm = boto3.client("ssm", region_name=args.region)
    token = args.token or _token(args.region, _parameter(ssm, "user-client-id"), args.user)

    agent = None
    if not args.url:
        gateway = GatewayClient(_parameter(ssm, "gateway-mcp-url"), token)
        model_id = args.model or ("claude-opus-5-5" if args.provider == "anthropic" else None)
        agent = ChatAgent(make_model_client(args.region, args.provider), gateway, model_id=model_id)
        try:
            identity = agent.load()
        except GatewayError as exc:
            print(f"The Gateway did not accept this user: {exc}")
            return 1
        print(f"You are {identity.get('email')}. Memory: {json.dumps(identity.get('memory'))}")

    messages: list = []
    session_id = str(uuid.uuid4())
    print("Type a message. /new starts a new conversation, /quit stops.\n")
    while True:
        try:
            prompt = input(f"{args.user}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not prompt:
            continue
        if prompt == "/quit":
            return 0
        if prompt == "/new":
            messages, session_id = [], str(uuid.uuid4())
            print("--- new conversation: the transcript is gone, long-term memory stays ---\n")
            continue

        if agent is not None:
            reply = agent.turn(messages, prompt)
            text = reply.text
            calls = [{"tool": c.tool, "arguments": c.arguments, "status": c.status, "result": c.result} for c in reply.tool_calls]
        else:
            answer = _remote_turn(args.url, token, session_id, prompt)
            if not answer.get("ok"):
                print(f"agent> (failed) {answer.get('error', answer)}\n")
                continue
            text, calls = answer["reply"], answer.get("memory_calls", [])
        print(f"agent> {text}")
        _show_calls(calls)
        print()


if __name__ == "__main__":
    sys.exit(main())
