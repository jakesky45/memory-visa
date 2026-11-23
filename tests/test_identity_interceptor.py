"""The interceptor finds the caller's token whatever the header's case (U6): run with
`make eval`. Needs boto3, because the interceptor makes its Cognito client on import."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("boto3")
os.environ.setdefault("AWS_DEFAULT_REGION", "eu-west-2")

from tools import identity_interceptor  # noqa: E402


class FakeCognito:
    def get_user(self, AccessToken):
        if AccessToken != "maya-token":
            raise RuntimeError("NotAuthorizedException")
        return {
            "UserAttributes": [
                {"Name": "email", "Value": "maya@robotinc.example"},
                {"Name": "custom:org", "Value": "robotinc"},
                {"Name": "custom:department", "Value": "engineering"},
            ]
        }


@pytest.fixture(autouse=True)
def _cognito(monkeypatch):
    monkeypatch.setattr(identity_interceptor, "_cognito", FakeCognito())


def _call(headers, arguments=None):
    event = {"mcp": {"gatewayRequest": {"headers": headers, "body": {"params": {"arguments": arguments or {}}}}}}
    out = identity_interceptor.handler(event, None)
    return out["mcp"]["transformedGatewayRequest"]["body"]["params"]["arguments"]


@pytest.mark.parametrize("name", ["authorization", "Authorization", "AUTHORIZATION"])
def test_the_token_is_found_whatever_the_case_of_the_header(name):
    """HTTP/2 clients send "authorization"; HTTP/1.1 clients send "Authorization"."""
    assert _call({name: "Bearer maya-token"})["_verified_email"] == "maya@robotinc.example"


def test_a_callers_own_verified_fields_never_survive():
    arguments = _call({"Authorization": "Bearer bad-token"}, {"_verified_email": "liam@robotinc.example"})
    assert arguments["_verified_email"] is None


def test_no_header_means_no_identity():
    assert _call({})["_verified_email"] is None
