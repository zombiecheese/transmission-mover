"""Web authentication management router."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from sqlmodel import Session

from app import crud
from app.auth_crypto import hash_password, verify_password
from app.db import engine
from app.schemas import MessageOut, SetupAuthRequest, ChangePasswordRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _get_client_ip(request: Request) -> str:
    """Extract client IP from request."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/setup", response_model=MessageOut)
def setup_web_auth(request_body: SetupAuthRequest, request: Request) -> MessageOut:
    """
    Initial web auth setup. Only allowed if no credentials exist yet.
    Should be called once to set up credentials.
    """
    with Session(engine) as session:
        existing_auth = crud.get_web_auth(session)
        if existing_auth:
            crud.log_auth_attempt(
                session,
                username=request_body.username,
                ip_address=_get_client_ip(request),
                success=False,
                message="Setup attempted but credentials already exist",
            )
            raise HTTPException(status_code=403, detail="Web auth already configured")

        if not request_body.username or not request_body.password:
            raise HTTPException(status_code=400, detail="Username and password are required")

        if len(request_body.password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

        username_hash = hash_password(request_body.username)
        password_hash = hash_password(request_body.password)

        crud.create_or_update_web_auth(session, username_hash, password_hash)
        crud.log_auth_attempt(
            session,
            username=request_body.username,
            ip_address=_get_client_ip(request),
            success=True,
            message="Web auth configured",
        )
        logger.info("Web authentication credentials configured")
        return MessageOut(message="Web auth configured successfully")


@router.post("/change-password", response_model=MessageOut)
def change_password(request_body: ChangePasswordRequest, request: Request) -> MessageOut:
    """
    Change the web authentication password.
    Requires authentication (verified in middleware before reaching this endpoint).
    """
    with Session(engine) as session:
        web_auth = crud.get_web_auth(session)
        if not web_auth:
            raise HTTPException(status_code=404, detail="Web auth not configured")

        # Extract username from Authorization header for logging
        auth_header = request.headers.get("authorization") or ""
        username_attempted = "unknown"
        if auth_header.startswith("Basic "):
            try:
                import base64

                token = auth_header[6:]
                decoded = base64.b64decode(token).decode("utf-8")
                username_attempted, _, _ = decoded.partition(":")
            except Exception:
                pass

        # Verify old password
        if not verify_password(request_body.old_password, web_auth.password_hash):
            crud.log_auth_attempt(
                session,
                username=username_attempted,
                ip_address=_get_client_ip(request),
                success=False,
                message="Password change failed: invalid old password",
            )
            raise HTTPException(status_code=401, detail="Invalid old password")

        if not request_body.new_password:
            raise HTTPException(status_code=400, detail="New password is required")

        if len(request_body.new_password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

        # Update password
        new_password_hash = hash_password(request_body.new_password)
        crud.create_or_update_web_auth(session, web_auth.username_hash, new_password_hash)

        crud.log_auth_attempt(
            session,
            username=username_attempted,
            ip_address=_get_client_ip(request),
            success=True,
            message="Password changed successfully",
        )
        logger.info("Web authentication password changed")
        return MessageOut(message="Password changed successfully")
