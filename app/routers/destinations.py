from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app import crud
from app.api_serializers import to_safe_destination
from app.api_validators import normalize_destination_payload
from app.db import get_session
from app.schemas import DestinationIn, DestinationSafeOut

router = APIRouter(prefix="/api", tags=["destinations"])


@router.get("/destinations", response_model=list[DestinationSafeOut])
def get_destinations(session: Session = Depends(get_session)) -> list[DestinationSafeOut]:
    return [to_safe_destination(destination) for destination in crud.list_destinations(session)]


@router.post("/destinations", response_model=DestinationSafeOut)
def post_destination(payload: DestinationIn, session: Session = Depends(get_session)) -> DestinationSafeOut:
    payload = normalize_destination_payload(payload)
    try:
        obj = crud.create_destination(session, payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to create destination: {exc}") from exc
    return to_safe_destination(obj)


@router.put("/destinations/{destination_id}", response_model=DestinationSafeOut)
def put_destination(
    destination_id: int,
    payload: DestinationIn,
    session: Session = Depends(get_session),
) -> DestinationSafeOut:
    payload = normalize_destination_payload(payload)
    obj = crud.update_destination(session, destination_id, payload)
    if not obj:
        raise HTTPException(status_code=404, detail="Destination not found")
    return to_safe_destination(obj)


@router.delete("/destinations/{destination_id}")
def remove_destination(destination_id: int, session: Session = Depends(get_session)) -> dict[str, bool]:
    ok = crud.delete_destination(session, destination_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Destination not found")
    return {"deleted": True}
