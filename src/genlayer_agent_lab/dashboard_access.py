"""Short-lived owner approval for a browser connected to the loopback dashboard.

All state is process-local: use one service worker; restarting requires browsers
to pair again. The approval route must receive the original workspace-token-only
dependency, never a dependency which also accepts the browser sessions issued here.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import math
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, StringConstraints

PENDING_SECONDS = 600
SESSION_SECONDS = 86_400
MAX_PENDING = 20
MAX_SESSIONS = 20
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_INVALID = "Invalid or expired dashboard connection."
_PATHS = {"/v1/dashboard/connect", "/v1/dashboard/claim", "/v1/dashboard/approve"}


class DashboardAccessLimit(ValueError):
    """The bounded pending-request or browser-session capacity is occupied."""


@dataclass
class _Pending:
    code_hash: bytes
    secret_hash: bytes
    expires_at: float
    approved: bool = False


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("ascii")).digest()


def _bounded_secret(value) -> bool:
    return type(value) is str and 1 <= len(value) <= 128 and value.isascii()


class DashboardAccess:
    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}
        self._sessions: dict[bytes, float] = {}

    def _prune(self, now):
        self._pending = {key: item for key, item in self._pending.items() if item.expires_at > now}
        self._sessions = {key: expiry for key, expiry in self._sessions.items() if expiry > now}

    def request(self):
        with self._lock:
            now = self._clock()
            self._prune(now)
            if len(self._pending) >= MAX_PENDING:
                raise DashboardAccessLimit("Too many pending browser connections. Try again later.")
            for _ in range(8):
                identifier = secrets.token_urlsafe(32)
                code = "".join(secrets.choice(_ALPHABET) for _ in range(12))
                code_hash = _digest(code)
                if identifier not in self._pending and not any(
                    hmac.compare_digest(code_hash, item.code_hash) for item in self._pending.values()
                ):
                    break
            else:
                raise RuntimeError("Cannot allocate a browser connection.")
            secret = secrets.token_urlsafe(32)
            self._pending[identifier] = _Pending(code_hash, _digest(secret), now + PENDING_SECONDS)
            return {"request_id": identifier, "code": "-".join(code[i:i + 4] for i in (0, 4, 8)),
                    "claim_secret": secret, "expires_in": PENDING_SECONDS}

    def approve(self, code):
        if type(code) is not str or len(code) > 14:
            raise ValueError(_INVALID)
        normalized = code.upper().replace("-", "")
        if re.fullmatch(r"[A-HJ-NP-Z2-9]{12}", normalized) is None:
            raise ValueError(_INVALID)
        digest = _digest(normalized)
        with self._lock:
            self._prune(self._clock())
            found = None
            for item in self._pending.values():
                if hmac.compare_digest(digest, item.code_hash):
                    found = item
            if found is None or found.approved:
                raise ValueError(_INVALID)
            found.approved = True
            return {"status": "approved"}

    def claim(self, request_id, claim_secret):
        if not _bounded_secret(request_id) or not _bounded_secret(claim_secret):
            raise ValueError(_INVALID)
        digest = _digest(claim_secret)
        with self._lock:
            now = self._clock()
            self._prune(now)
            pending = self._pending.get(request_id)
            matches = hmac.compare_digest(digest, pending.secret_hash if pending else bytes(32))
            if pending is None or not matches:
                raise ValueError(_INVALID)
            if not pending.approved:
                return {"status": "pending", "expires_in": math.ceil(pending.expires_at - now)}
            if len(self._sessions) >= MAX_SESSIONS:
                raise DashboardAccessLimit("Too many browser sessions. Try again after a session expires.")
            for _ in range(8):
                token = "lab-browser-" + secrets.token_urlsafe(32)
                digest = _digest(token)
                if digest not in self._sessions:
                    break
            else:
                raise RuntimeError("Cannot allocate a browser session.")
            self._sessions[digest] = now + SESSION_SECONDS
            del self._pending[request_id]
            return {"status": "approved", "token": token, "expires_in": SESSION_SECONDS}

    def authenticate(self, token):
        if not _bounded_secret(token):
            return False
        digest = _digest(token)
        with self._lock:
            self._prune(self._clock())
            return any(hmac.compare_digest(digest, existing) for existing in self._sessions)


class _ConnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _ClaimRequest(_ConnectRequest):
    request_id: Annotated[str, StringConstraints(min_length=43, max_length=43,
                                               pattern=r"^[A-Za-z0-9_-]{43}$")]
    claim_secret: Annotated[str, StringConstraints(min_length=43, max_length=43,
                                                 pattern=r"^[A-Za-z0-9_-]{43}$")]


class _ApprovalRequest(_ConnectRequest):
    code: Annotated[str, StringConstraints(min_length=12, max_length=14,
                                         pattern=r"^[A-HJ-NP-Za-hj-np-z2-9-]+$")]


def _loopback_origin(value):
    if type(value) is not str or re.search(r"[^\x21-\x7e]", value):
        raise ValueError
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.path or parsed.query or parsed.fragment):
        raise ValueError
    hostname = parsed.hostname.lower()
    if hostname != "localhost":
        if "%" in hostname or not ipaddress.ip_address(hostname).is_loopback:
            raise ValueError
    port = parsed.port
    if port == 0:
        raise ValueError
    return parsed.scheme, hostname, port if port is not None else (443 if parsed.scheme == "https" else 80)


class _DashboardBoundary:
    """Apply origin checks and no-store even to dependency/validation failures."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path", "").rstrip("/") not in _PATHS:
            await self.app(scope, receive, send)
            return

        async def private_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = [(key, value) for key, value in message.get("headers", [])
                                      if key.lower() not in {b"cache-control", b"pragma"}]
                message["headers"] += [(b"cache-control", b"no-store"), (b"pragma", b"no-cache")]
            await send(message)

        headers = scope.get("headers", [])
        hosts = [value for key, value in headers if key.lower() == b"host"]
        origins = [value for key, value in headers if key.lower() == b"origin"]
        try:
            if len(hosts) != 1:
                raise ValueError
            expected = _loopback_origin(scope.get("scheme", "http") + "://" + hosts[0].decode("ascii"))
        except (ValueError, UnicodeError):
            await JSONResponse({"detail": "Invalid dashboard host."}, 400)(scope, receive, private_send)
            return
        try:
            if len(origins) > 1 or origins and _loopback_origin(origins[0].decode("ascii")) != expected:
                raise ValueError
        except (ValueError, UnicodeError):
            await JSONResponse({"detail": "Cross-origin requests are not allowed."}, 403)(scope, receive, private_send)
            return
        await self.app(scope, receive, private_send)


class _PrivateRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                # Default validation errors echo submitted claim secrets/codes.
                return JSONResponse({"detail": "Invalid dashboard connection request."}, 422)

        return private_handler


def mount_dashboard_access_routes(app, access: DashboardAccess, administrator):
    """Mount with the workspace-token-only administrator dependency."""
    router = APIRouter(route_class=_PrivateRoute)

    def perform(operation, *arguments):
        try:
            return operation(*arguments)
        except DashboardAccessLimit as exc:
            return JSONResponse({"detail": str(exc)}, 429)
        except ValueError:
            return JSONResponse({"detail": _INVALID}, 400)

    @router.post("/v1/dashboard/connect")
    def connect(body: _ConnectRequest):
        return perform(access.request)

    @router.post("/v1/dashboard/claim")
    def claim(body: _ClaimRequest):
        return perform(access.claim, body.request_id, body.claim_secret)

    @router.post("/v1/dashboard/approve", dependencies=[Depends(administrator)])
    def approve(body: _ApprovalRequest):
        return perform(access.approve, body.code)

    app.include_router(router)
    app.add_middleware(_DashboardBoundary)
