"""The claims Cognito is told to put in the access token (N10): run with `make eval`."""
from __future__ import annotations

from tools.token_claims import handler


def _event(attributes):
    return {"version": "2", "triggerSource": "TokenGeneration_Authentication", "request": {"userAttributes": attributes}, "response": {}}


def _added(event):
    return handler(event, None)["response"]["claimsAndScopeOverrideDetails"]["accessTokenGeneration"]["claimsToAddOrOverride"]


def test_org_and_department_go_into_the_access_token():
    added = _added(_event({"email": "maya@robotinc.example", "custom:org": "robotinc", "custom:department": "engineering"}))
    assert added == {"org": "robotinc", "department": "engineering"}


def test_a_contractor_with_no_department_gets_no_department_claim():
    """Jordan. The Cedar rule then sees no department tag, and he is refused the Engineering tool."""
    assert _added(_event({"email": "jordan@contractor.example", "custom:org": "robotinc"})) == {"org": "robotinc"}


def test_nothing_else_from_the_profile_is_copied():
    added = _added(_event({"email": "oleg@robotinc.example", "custom:org": "robotinc", "custom:department": "legal", "phone_number": "+440000"}))
    assert set(added) == {"org", "department"}


def test_an_event_with_no_attributes_adds_nothing_and_does_not_fail():
    assert _added({"request": {}}) == {}
