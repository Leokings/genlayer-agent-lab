"""Browser pairing requires both owner approval and the original claimant secret."""

import concurrent.futures
import re
import threading

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from genlayer_agent_lab.dashboard_access import (
    MAX_PENDING,
    MAX_SESSIONS,
    PENDING_SECONDS,
    SESSION_SECONDS,
    DashboardAccess,
    DashboardAccessLimit,
    mount_dashboard_access_routes,
)


@pytest.fixture
def pairing():
    now = [100.0]
    return DashboardAccess(clock=lambda: now[0]), now


def finish(access, pending):
    access.approve(pending["code"])
    return access.claim(pending["request_id"], pending["claim_secret"])


def test_pairing_issues_an_independent_session_only_once(pairing, capsys):
    access, _ = pairing
    pending = access.request()
    assert re.fullmatch(r"[A-HJ-NP-Z2-9]{4}(-[A-HJ-NP-Z2-9]{4}){2}", pending["code"])
    assert len(pending["request_id"]) == len(pending["claim_secret"]) == 43
    assert pending["expires_in"] == PENDING_SECONDS
    assert access.claim(pending["request_id"], pending["claim_secret"])["status"] == "pending"
    assert access.approve(pending["code"].lower()) == {"status": "approved"}
    with pytest.raises(ValueError):
        access.approve(pending["code"])
    session = access.claim(pending["request_id"], pending["claim_secret"])
    assert session["status"] == "approved" and session["expires_in"] == SESSION_SECONDS
    assert access.authenticate(session["token"])
    for wrong in (pending["claim_secret"], pending["code"], pending["request_id"], "admin-secret", "run-secret"):
        assert not access.authenticate(wrong)
    with pytest.raises(ValueError):
        access.claim(pending["request_id"], pending["claim_secret"])
    assert not access._pending and len(access._sessions) == 1
    assert session["token"] not in repr(access._sessions)
    assert capsys.readouterr() == ("", "")


def test_wrong_claimant_and_code_do_not_consume_or_approve(pairing):
    access, _ = pairing
    pending = access.request()
    state = repr(access._pending)
    assert pending["claim_secret"] not in state and pending["code"] not in state
    errors = []
    for identifier, secret in [(pending["request_id"], "x" * 43), ("forged", pending["claim_secret"]),
                               (pending["request_id"], pending["code"])]:
        with pytest.raises(ValueError) as exc:
            access.claim(identifier, secret)
        errors.append(str(exc.value))
    assert len(set(errors)) == 1
    with pytest.raises(ValueError):
        access.approve("AAAA-AAAA-AAAA")
    assert access.claim(pending["request_id"], pending["claim_secret"])["status"] == "pending"
    assert access.authenticate(finish(access, pending)["token"])


def test_expiry_and_restart_invalidate_pairing_and_sessions(pairing):
    access, now = pairing
    old = access.request()
    now[0] += PENDING_SECONDS
    for operation, arguments in [(access.approve, (old["code"],)),
                                 (access.claim, (old["request_id"], old["claim_secret"]))]:
        with pytest.raises(ValueError):
            operation(*arguments)
    session = finish(access, access.request())
    assert not DashboardAccess().authenticate(session["token"])
    now[0] += SESSION_SECONDS - .1
    assert access.authenticate(session["token"])
    now[0] += .1
    assert not access.authenticate(session["token"]) and not access._sessions


def test_approval_does_not_extend_original_request_expiry(pairing):
    access, now = pairing
    pending = access.request()
    now[0] += PENDING_SECONDS - 1
    access.approve(pending["code"])
    now[0] += 1
    with pytest.raises(ValueError):
        access.claim(pending["request_id"], pending["claim_secret"])


def test_pending_and_session_limits_fail_closed_and_prune(pairing):
    access, now = pairing
    for _ in range(MAX_PENDING):
        access.request()
    with pytest.raises(DashboardAccessLimit):
        access.request()
    now[0] += PENDING_SECONDS
    assert access.request()
    for _ in range(MAX_SESSIONS):
        finish(access, access.request())
    pending = access.request()
    access.approve(pending["code"])
    with pytest.raises(DashboardAccessLimit):
        access.claim(pending["request_id"], pending["claim_secret"])
    assert len(access._sessions) == MAX_SESSIONS and pending["request_id"] in access._pending
    now[0] += SESSION_SECONDS
    assert access.authenticate(finish(access, access.request())["token"])
    assert len(access._sessions) == 1


def test_concurrent_claims_issue_exactly_one_session(pairing):
    access, _ = pairing
    pending = access.request()
    access.approve(pending["code"])
    barrier = threading.Barrier(8)

    def claim():
        barrier.wait(timeout=5)
        try:
            return access.claim(pending["request_id"], pending["claim_secret"])
        except ValueError:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: claim(), range(8)))
    assert len([result for result in results if result]) == 1
    assert len(access._sessions) == 1 and not access._pending


@pytest.mark.parametrize("value", [None, [], {}, True, 12, "é", "x" * 129])
def test_malformed_direct_credentials_fail_without_type_errors(pairing, value):
    access, _ = pairing
    assert not access.authenticate(value)
    with pytest.raises(ValueError):
        access.claim(value, value)
    with pytest.raises(ValueError):
        access.approve(value)


@pytest.fixture
def route_lab(pairing):
    access, now = pairing
    app = FastAPI()

    def administrator(authorization: str | None = Header(default=None)):
        if authorization != "Bearer workspace-only-secret":
            raise HTTPException(401, "Workspace owner approval required.")

    mount_dashboard_access_routes(app, access, administrator)
    with TestClient(app, base_url="http://127.0.0.1:8875") as client:
        yield client, access, now


def test_routes_require_owner_approval_but_not_owner_secret_in_browser(route_lab):
    client, access, _ = route_lab
    pending = client.post("/v1/dashboard/connect", json={}).json()
    payload = {key: pending[key] for key in ("request_id", "claim_secret")}
    for token in (None, "run-secret", pending["claim_secret"]):
        response = client.post("/v1/dashboard/approve", json={"code": pending["code"]},
                               headers={"Authorization": "Bearer " + token} if token else {})
        assert response.status_code == 401 and response.headers["cache-control"] == "no-store"
    assert client.post("/v1/dashboard/claim", json=payload).json()["status"] == "pending"
    response = client.post("/v1/dashboard/approve", json={"code": pending["code"]},
                           headers={"Authorization": "Bearer workspace-only-secret"})
    assert response.json() == {"status": "approved"} and "token" not in response.text
    response = client.post("/v1/dashboard/claim", json=payload)
    assert response.headers["cache-control"] == "no-store"
    token = response.json()["token"]
    assert access.authenticate(token)
    assert client.post("/v1/dashboard/claim", json=payload).status_code == 400
    next_request = client.post("/v1/dashboard/connect", json={}).json()
    assert client.post("/v1/dashboard/approve", json={"code": next_request["code"]},
                       headers={"Authorization": "Bearer " + token}).status_code == 401


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://localhost:8875",
                                    "http://127.0.0.1:8765", "https://127.0.0.1:8875",
                                    "http://127.0.0.1:8875/path", "http://127.0.0.1:8875?x=y"])
def test_cross_origin_requests_are_rejected_even_without_host_middleware(route_lab, origin):
    client, access, _ = route_lab
    for endpoint in ("connect", "claim"):
        response = client.post("/v1/dashboard/" + endpoint, json={}, headers={"Origin": origin})
        assert response.status_code == 403 and response.headers["cache-control"] == "no-store"
        assert "access-control-allow-origin" not in response.headers
    assert not access._pending


@pytest.mark.parametrize("host", ["localhost:8875", "127.0.0.1:8875", "[::1]:8875"])
def test_loopback_hosts_accept_absent_or_exact_same_origin(route_lab, host):
    client, _, _ = route_lab
    for origin in (None, "http://" + host):
        response = client.post("/v1/dashboard/connect", json={},
                               headers={"Host": host, **({"Origin": origin} if origin else {})})
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1.evil.example", "127.0.0.1@evil.example",
                                  "@localhost:8875", "127.0.0.1:99999", "127.0.0.1:0", "[2001:db8::1]:8875"])
def test_nonloopback_or_malformed_hosts_fail_closed(route_lab, host):
    client, access, _ = route_lab
    response = client.post("/v1/dashboard/connect", json={}, headers={"Host": host})
    assert response.status_code == 400 and response.headers["cache-control"] == "no-store"
    assert not access._pending


def test_validation_and_limit_errors_are_private_and_bounded(route_lab):
    client, _, _ = route_lab
    secret = "private-claim-value-must-not-be-echoed"
    for endpoint, payload in [("claim", {"request_id": [], "claim_secret": secret}),
                              ("connect", {"unexpected": secret})]:
        response = client.post("/v1/dashboard/" + endpoint, json=payload)
        assert response.status_code == 422 and secret not in response.text
        assert response.headers["cache-control"] == "no-store"
    for _ in range(MAX_PENDING):
        assert client.post("/v1/dashboard/connect", json={}).status_code == 200
    response = client.post("/v1/dashboard/connect", json={})
    assert response.status_code == 429 and response.headers["cache-control"] == "no-store"
