from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import cast

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session

from app import crud
from app.auth_session import SESSION_COOKIE_NAME, get_session_username
from app.db import create_db_and_tables, engine
from app.models import WebAuth
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


_AUTH_CACHE_TTL_SECONDS = 5.0
_auth_cache: dict[str, float | WebAuth | None] = {
    "loaded_at": 0.0,
    "web_auth": None,
}


def _get_client_ip(request: Request) -> str:
    """Extract client IP from request."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_web_auth_enabled() -> bool:
    """Check if web auth is configured in the database."""
    now = time.monotonic()
    loaded_at = cast(float, _auth_cache.get("loaded_at", 0.0))
    if now - loaded_at > _AUTH_CACHE_TTL_SECONDS:
        with Session(engine) as session:
            _auth_cache["web_auth"] = crud.get_web_auth(session)
            _auth_cache["loaded_at"] = now
    return _auth_cache.get("web_auth") is not None


@app.middleware("http")
async def enforce_session_auth(request: Request, call_next):
    """Enforce session-cookie auth if web auth is configured."""
    path = request.url.path

    if path in {"/api/auth/setup", "/api/auth/login", "/api/auth/logout", "/api/health", "/login"}:
        return await call_next(request)
    if path.startswith("/static/"):
        return await call_next(request)

    if not _is_web_auth_enabled():
        return await call_next(request)

    if get_session_username(request.cookies.get(SESSION_COOKIE_NAME)) is not None:
        return await call_next(request)

    if path.startswith("/api/"):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return RedirectResponse(url="/login", status_code=302)


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


@app.get("/login", include_in_schema=False)
def login_page(request: Request):
    if _is_web_auth_enabled() and get_session_username(request.cookies.get(SESSION_COOKIE_NAME)) is not None:
        return RedirectResponse(url="/", status_code=302)
    return FileResponse(static_dir / "login.html")


@app.get("/api/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(status="ok")
