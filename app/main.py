from __future__ import annotations

import base64
import logging
import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session

from app import crud
from app.auth_crypto import verify_password
from app.db import create_db_and_tables, engine
from app.rate_limiter import check_rate_limit, record_failed_attempt, reset_rate_limit
from app.routers import activity_router, config_router, destinations_router, rules_router, transmission_router
from app.routers.auth import router as auth_router
from app.runtime import worker
from app.schemas import HealthOut
from app.secret_crypto import ensure_secret_crypto_ready
from app.settings import settings

logger = logging.getLogger(__name__)

logging.basicConfig(level=settings.log_level)

app = FastAPI(title=settings.app_name)

static_dir = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")
app.include_router(config_router)
app.include_router(transmission_router)
app.include_router(destinations_router)
app.include_router(rules_router)
app.include_router(activity_router)
app.include_router(auth_router)


def _get_client_ip(request: Request) -> str:
    """Extract client IP from request."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_web_auth_enabled() -> bool:
    """Check if web auth is configured in the database."""
    with Session(engine) as session:
        return crud.get_web_auth(session) is not None


def _is_authorized_request(request: Request) -> tuple[bool, str | None]:
    """
    Check if the request is authorized via HTTP Basic Auth.
    Returns (is_authorized, username_attempted).
    """
    auth_header = request.headers.get("authorization") or ""
    if not auth_header.startswith("Basic "):
        return False, None

    token = auth_header[6:]
    try:
        decoded = base64.b64decode(token).decode("utf-8")
    except Exception:
        return False, None

    username, separator, password = decoded.partition(":")
    if not separator:
        return False, username

    with Session(engine) as session:
        web_auth = crud.get_web_auth(session)
        if not web_auth:
            return False, username

        if verify_password(username, web_auth.username_hash) and verify_password(
            password, web_auth.password_hash
        ):
            reset_rate_limit(_get_client_ip(request))
            crud.log_auth_attempt(
                session,
                username=username,
                ip_address=_get_client_ip(request),
                success=True,
                message="Authentication successful",
            )
            return True, username
        else:
            crud.log_auth_attempt(
                session,
                username=username,
                ip_address=_get_client_ip(request),
                success=False,
                message="Authentication failed: invalid credentials",
            )
            return False, username


@app.middleware("http")
async def enforce_basic_auth(request: Request, call_next):
    """Enforce HTTP Basic Auth if configured."""
    # Skip auth for setup endpoint if not yet configured
    if request.url.path == "/api/auth/setup":
        return await call_next(request)

    if not _is_web_auth_enabled():
        return await call_next(request)

    # Check rate limit
    client_ip = _get_client_ip(request)
    if not check_rate_limit(client_ip):
        with Session(engine) as session:
            crud.log_auth_attempt(
                session,
                username=None,
                ip_address=client_ip,
                success=False,
                message="Rate limit exceeded",
            )
        return PlainTextResponse(
            "Too many failed authentication attempts. Please try again later.",
            status_code=429,
        )

    is_authorized, username_attempted = _is_authorized_request(request)
    if is_authorized:
        return await call_next(request)

    # Record failed attempt
    record_failed_attempt(client_ip)
    return PlainTextResponse(
        "Unauthorized",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Transmission Mover"'},
    )


@app.on_event("startup")
def on_startup() -> None:
    ensure_secret_crypto_ready()
    create_db_and_tables()
    with Session(engine) as session:
        crud.migrate_plaintext_secrets(session)

        # Initialize web auth from environment variables if not already configured
        existing_auth = crud.get_web_auth(session)
        if not existing_auth:
            env_username = (settings.web_auth_username or "").strip()
            env_password = (settings.web_auth_password or "").strip()
            if env_username and env_password:
                from app.auth_crypto import hash_password

                username_hash = hash_password(env_username)
                password_hash = hash_password(env_password)
                crud.create_or_update_web_auth(session, username_hash, password_hash)
                logger.info("Web auth initialized from environment variables")
            else:
                logger.warning("Web auth not configured. API is open. Configure credentials via POST /api/auth/setup")

    worker.start()


@app.on_event("shutdown")
def on_shutdown() -> None:
    worker.stop()


@app.get("/", include_in_schema=False)
def root() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/api/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(status="ok")
