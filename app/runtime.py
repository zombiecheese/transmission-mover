from __future__ import annotations

from sqlmodel import Session

from app import crud
from app.settings import settings
from app.worker import MoveWorker

worker = MoveWorker(poll_seconds=settings.poll_seconds)


def log_activity_error(session: Session, scope: str, message: str) -> None:
    crud.create_log(session, torrent_name=f"<{scope}>", status="error", message=message)
