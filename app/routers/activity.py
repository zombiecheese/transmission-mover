from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app import crud
from app.db import get_session
from app.runtime import worker
from app.schemas import MoveLogOut, TransferProgressOut

router = APIRouter(prefix="/api", tags=["activity"])


@router.post("/run-once")
def run_once() -> dict[str, bool]:
    worker.run_once()
    return {"ok": True}


@router.post("/transfer/torrent/{torrent_id}")
def transfer_torrent_now(torrent_id: int) -> dict[str, bool | str]:
    result = worker.run_torrent_now(torrent_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message") or "Transfer failed")
    return {"ok": True, "message": str(result.get("message") or "Transfer completed")}


@router.get("/logs", response_model=list[MoveLogOut])
def get_logs(limit: int = 100, session: Session = Depends(get_session)) -> list[MoveLogOut]:
    if limit < 1 or limit > 1000:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 1000")
    return [MoveLogOut.model_validate(log.model_dump()) for log in crud.list_logs(session, limit=limit)]


@router.delete("/logs")
def clear_logs(session: Session = Depends(get_session)) -> dict[str, int]:
    deleted = crud.clear_logs(session)
    return {"deleted": deleted}


@router.get("/transfers/active", response_model=list[TransferProgressOut])
def get_active_transfers() -> list[TransferProgressOut]:
    return [TransferProgressOut.model_validate(item) for item in worker.get_active_transfers()]
