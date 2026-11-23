"""A person's own memory namespace must be one AgentCore accepts (Q4): run with `make eval`."""
from __future__ import annotations

import re

import pytest

from memory.guarded_memory import UserClaims, namespace_safe, requester_namespace, static_namespaces, user_namespace

# The pattern in AgentCore's ValidationException for RetrieveMemoryRecords, 23 November 2026.
AGENTCORE = re.compile(r"[a-zA-Z0-9/*][a-zA-Z0-9-_/*]*(?::[a-zA-Z0-9-_/*]+)*[a-zA-Z0-9-_/*]*")


@pytest.mark.parametrize(
    "email",
    ["maya@robotinc.example", "jordan.o'neil+test@contractor.example", "a_b@x.example", "ümit@x.example"],
)
def test_any_email_gives_a_namespace_agentcore_accepts(email):
    namespace = user_namespace(UserClaims(email=email, org="robotinc"))
    assert AGENTCORE.fullmatch(namespace), namespace


def test_it_stays_readable():
    assert user_namespace(UserClaims(email="maya@robotinc.example", org="robotinc")) == "org_robotinc_user_maya_at_robotinc_dot_example"


def test_two_different_emails_never_share_a_namespace():
    """Isolation depends on it. These pairs would collide under a simple replace."""
    pairs = [
        ("a.b@x.example", "a_dot_b@x.example"),
        ("a@b.example", "a_at_b.example"),
        ("a_b@x.example", "a__b@x.example"),
        ("a-b@x.example", "a.b@x.example"),
    ]
    for one, other in pairs:
        assert namespace_safe(one) != namespace_safe(other), (one, other)


def test_the_grant_key_keeps_the_plain_email():
    """A grant is filed under the requester's email. That key is never sent to AgentCore."""
    maya = UserClaims(email="maya@robotinc.example", org="robotinc")
    assert requester_namespace(maya) == "org_robotinc_user_maya@robotinc.example"
    assert requester_namespace(maya) not in static_namespaces(maya)
