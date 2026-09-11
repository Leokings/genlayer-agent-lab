"""Loopback-only HTTP interface with separate administrator and run credentials."""

from __future__ import annotations

import hmac
import ipaddress
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, model_validator

from . import __version__
from .reports import export_report

LOGGER = logging.getLogger(__name__)
DEFAULT_PORT = 8765
MAX_BODY_BYTES = 65_536
MAX_PROJECT_BODY_BYTES = 2_500_000
TokenString = Annotated[str, StringConstraints(min_length=1, max_length=256)]
IdempotencyString = Annotated[str, StringConstraints(min_length=1, max_length=128)]
NameString = Annotated[str, StringConstraints(min_length=1, max_length=160)]


def default_data_dir() -> Path:
    configured = os.environ.get("LAB_DATA_DIR")
    return Path(configured) if configured else Path.home() / ".genlayer-agent-lab"


def initialize_data_dir(data_dir: Path) -> Path:
    """Create only our own state and token. Never replace an existing token."""
    data_dir = data_dir.expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    token_path = data_dir / "admin.token"
    try:
        descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(secrets.token_urlsafe(32) + "\n")
    read_admin_token(data_dir)
    return data_dir


def read_admin_token(data_dir: Path) -> str:
    value = (data_dir / "admin.token").read_text(encoding="utf-8").strip()
    if len(value) < 24 or len(value) > 256 or not value.isascii() or any(
        char.isspace() for char in value
    ):
        raise RuntimeError("Invalid admin.token; restore the installation token before starting.")
    return value


def validate_loopback_url(url: str) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise ValueError("Use a loopback HTTP base URL without credentials, path, query or fragment.")
    try:
        allowed = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        allowed = parsed.hostname.lower() == "localhost"
    if not allowed:
        raise ValueError("Only loopback API URLs are supported. Use an SSH tunnel for a server.")
    # Accessing port validates malformed/out-of-range numeric ports.
    _ = parsed.port
    return url.rstrip("/")


class BoundedRequestMiddleware:
    """Limit streamed request bodies before JSON parsing, not just Content-Length."""

    def __init__(self, app: Any, max_bytes: int = MAX_BODY_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        limit = (MAX_PROJECT_BODY_BYTES if scope.get("method") == "POST"
                 and scope.get("path") in {"/v1/workflows", "/v1/onboarding/preview",
                                           "/v1/onboarding/review"}
                 and headers.get(b"content-type", b"").split(b";", 1)[0].strip() == b"application/json"
                 else self.max_bytes)
        try:
            validate_loopback_url("http://" + headers.get(b"host", b"").decode("ascii"))
        except (ValueError, UnicodeError):
            await JSONResponse({"detail": "Invalid host header"}, 400)(scope, receive, send)
            return
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            await JSONResponse({"detail": "Invalid Content-Length"}, 400)(scope, receive, send)
            return
        if declared < 0 or declared > limit:
            await JSONResponse({"detail": "Request body too large"}, 413)(scope, receive, send)
            return
        origin = headers.get(b"origin")
        if origin:
            try:
                parsed = urlsplit(origin.decode("ascii"))
                origin_matches = (parsed.scheme in {"http", "https"}
                                  and parsed.netloc.encode("ascii") == headers.get(b"host"))
            except (ValueError, UnicodeError):
                origin_matches = False
            if not origin_matches:
                await JSONResponse({"detail": "Cross-origin requests are not allowed"}, 403)(
                    scope, receive, send
                )
                return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > limit:
                await JSONResponse({"detail": "Request body too large"}, 413)(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        sent = False

        async def replay() -> dict:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        async def headers_send(message: dict) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"content-security-policy", b"default-src 'self'; script-src 'self'; "
                     b"style-src 'self' 'unsafe-inline'; connect-src 'self'; "
                     b"frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
                ]
            await send(message)

        await self.app(scope, replay, headers_send)


class CreateRun(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scenario_id: NameString
    agent: Literal["external", "safe", "unsafe", "refuse"] = "external"
    backend: Literal["glsim", "fixture", "container-glsim", "studio"] = "glsim"
    binding_id: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,95}$")] | None = None

    @model_validator(mode="after")
    def binding_backend_matches(self):
        if ((self.binding_id is not None and self.backend not in {"container-glsim", "studio"})
                or (self.backend == "container-glsim" and self.binding_id is None)):
            raise ValueError("Bindings require container-glsim or studio; container-glsim requires a binding")
        return self


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    idempotency_key: IdempotencyString


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: NameString
    resource_id: NameString | None = None
    policy_version: NameString | None = None
    decision_id: TokenString | None = None
    revision: Annotated[StrictInt, Field(ge=0, le=2_147_483_647)] | None = None
    amount: Annotated[StrictInt, Field(ge=0, le=9_000_000_000_000_000)] | None = None
    idempotency_key: IdempotencyString


def create_app(data_dir: Path | str | None = None, *, engine: Any = None) -> FastAPI:
    data_dir = initialize_data_dir(Path(data_dir) if data_dir is not None else default_data_dir())
    admin_token = read_admin_token(data_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if engine is None:
            from .engine import Engine
            app.state.engine = Engine(data_dir)
        else:
            app.state.engine = engine
        try:
            yield
        finally:
            if engine is None:
                app.state.engine.close()

    app = FastAPI(title="GenLayer Agent Lab", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(BoundedRequestMiddleware)

    def bearer(authorization: str | None) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "Bearer credentials required", headers={"WWW-Authenticate": "Bearer"})
        token = authorization[7:]
        if not token or not token.isascii() or len(token) > 256:
            raise HTTPException(401, "Invalid credentials")
        return token

    def administrator(authorization: Annotated[str | None, Header()] = None) -> None:
        if not hmac.compare_digest(bearer(authorization), admin_token):
            raise HTTPException(401, "Invalid administrator credentials")

    from .onboarding import mount_onboarding_routes
    from .workflow_api import mount_workflow_routes
    mount_onboarding_routes(app, data_dir, administrator)
    mount_workflow_routes(app, lambda request: request.app.state.engine.workflows,
                          administrator, bearer)

    def agent_access(run_id: str, request: Request,
                     authorization: Annotated[str | None, Header()] = None) -> None:
        token = bearer(authorization)
        try:
            valid = request.app.state.engine.authenticate_agent(run_id, token)
        except KeyError:
            valid = False
        if not valid:
            raise HTTPException(401, "Invalid run credentials")

    @app.exception_handler(KeyError)
    async def missing_handler(request: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse({"detail": "Run, scenario or contract binding not found"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid_handler(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": "Request is invalid or conflicts with run state"}, status_code=409)

    @app.exception_handler(RuntimeError)
    async def runtime_handler(request: Request, exc: RuntimeError) -> JSONResponse:
        LOGGER.warning("A runtime operation failed; check local service diagnostics.")
        return JSONResponse({"detail": "Runtime unavailable; inspect service diagnostics"}, status_code=503)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Invalid action attempts are behavior evidence even though no effect can be applied.
        # Only authenticated, active runs are updated; never persist rejected raw input.
        route = request.scope.get("route")
        if request.method == "POST" and getattr(route, "path", None) == "/v1/runs/{run_id}/actions":
            try:
                token = bearer(request.headers.get("authorization"))
                run_id = request.path_params["run_id"]
                active_engine = request.app.state.engine
                if active_engine.authenticate_agent(run_id, token):
                    active_engine.reject_invalid_action(run_id, "Action schema validation failed")
            except (HTTPException, KeyError, ValueError, RuntimeError):
                pass
        if request.method == "POST" and getattr(route, "path", None) in {
            "/v1/workflows/{run_id}/operations", "/v1/workflows/{run_id}/appeals",
        }:
            try:
                token = bearer(request.headers.get("authorization"))
                run_id = request.path_params["run_id"]
                manager = request.app.state.engine.workflows
                if manager.authenticate(run_id, token):
                    manager.reject_invalid_action(run_id)
            except (HTTPException, KeyError, ValueError, RuntimeError):
                pass
        # Pydantic errors normally echo input values, which may contain credentials/evidence.
        errors = [{"location": list(error["loc"]), "type": error["type"]}
                  for error in exc.errors()]
        return JSONResponse({"detail": "Request validation failed", "errors": errors}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_handler(request: Request, exc: Exception) -> JSONResponse:
        LOGGER.error("An unexpected %s interrupted a request", type(exc).__name__)
        return JSONResponse({"detail": "Internal service error"}, status_code=500)

    @app.get("/health")
    def health(setup_nonce: Annotated[str | None, Query(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$",
    )] = None) -> dict:
        from .onboarding_setup import installation_identity, installation_proof
        result = {"status": "ok", "version": __version__,
                  "installation_id": installation_identity(data_dir, admin_token)}
        if setup_nonce is not None:
            result["setup_proof"] = installation_proof(admin_token, setup_nonce)
        return result

    @app.get("/v1/scenarios", dependencies=[Depends(administrator)])
    def scenarios(request: Request) -> list[dict]:
        return request.app.state.engine.list_scenarios()

    @app.get("/v1/bindings", dependencies=[Depends(administrator)])
    def bindings(request: Request) -> list[dict]:
        return request.app.state.engine.list_bindings()

    @app.get("/v1/runs", dependencies=[Depends(administrator)])
    def runs(request: Request) -> list[dict]:
        return request.app.state.engine.list_runs()

    @app.post("/v1/runs", status_code=201, dependencies=[Depends(administrator)])
    def create_run(payload: CreateRun, request: Request) -> dict:
        return request.app.state.engine.create_run(**payload.model_dump(exclude_none=True))

    @app.get("/v1/runs/{run_id}", dependencies=[Depends(administrator)])
    def get_run(run_id: str, request: Request) -> dict:
        return request.app.state.engine.get_run(run_id)

    @app.post("/v1/runs/{run_id}/cancel", dependencies=[Depends(administrator)])
    def cancel_run(run_id: str, request: Request) -> dict:
        return request.app.state.engine.cancel_run(run_id)

    @app.get("/v1/runs/{run_id}/report", dependencies=[Depends(administrator)])
    def report(run_id: str, request: Request,
               format: Literal["json", "html", "junit"] = "json") -> Response:
        result = request.app.state.engine.report(run_id)
        media_type = {"json": "application/json", "html": "text/html",
                      "junit": "application/xml"}[format]
        return Response(export_report(result, format), media_type=media_type)

    @app.post("/v1/runs/{run_id}/observe", dependencies=[Depends(agent_access)])
    def observe(run_id: str, request: Request) -> dict:
        return request.app.state.engine.observe(run_id)

    @app.post("/v1/runs/{run_id}/decision", dependencies=[Depends(agent_access)])
    def request_decision(run_id: str, payload: DecisionRequest, request: Request) -> dict:
        return request.app.state.engine.request_decision(run_id, payload.idempotency_key)

    @app.get("/v1/runs/{run_id}/decision", dependencies=[Depends(agent_access)])
    def read_decision(run_id: str, request: Request) -> dict:
        return request.app.state.engine.read_decision(run_id)

    @app.post("/v1/runs/{run_id}/actions", dependencies=[Depends(agent_access)])
    def act(run_id: str, payload: ActionRequest, request: Request) -> dict:
        return request.app.state.engine.act(run_id, payload.model_dump(exclude_none=True))

    @app.post("/v1/runs/{run_id}/finish", dependencies=[Depends(agent_access)])
    def finish(run_id: str, request: Request) -> dict:
        result = request.app.state.engine.finish(run_id)
        return {key: result.get(key) for key in ("run_id", "status", "verdict", "grades", "findings")}

    assets = Path(__file__).parent / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/setup", include_in_schema=False)
    def setup_instructions() -> Response:
        package = Path(__file__).parent
        # Wheels carry the same standalone document as the source checkout.
        page = package / "_kit" / "docs" / "START.html"
        if not page.is_file():
            page = package.parents[1] / "docs" / "START.html"
        if page.is_file():
            return FileResponse(page, media_type="text/html")
        return Response("Setup instructions are unavailable in this installation.",
                        status_code=404, media_type="text/plain")

    @app.get("/", include_in_schema=False)
    def dashboard() -> Response:
        index = assets / "workflows.html"
        if index.is_file():
            return FileResponse(index, media_type="text/html")
        return Response("GenLayer Agent Lab is running. Dashboard assets are unavailable.",
                        media_type="text/plain")

    return app
