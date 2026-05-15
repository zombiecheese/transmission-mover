from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.api_serializers import to_torrent_out
from app.db import get_session
from app.runtime import log_activity_error
from app.schemas import TorrentLabelAssignIn, TransmissionConfigIn, TransmissionTorrentsOut
from app.transmission import TransmissionClient

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["transmission"])


@router.post("/transmission/torrents", response_model=TransmissionTorrentsOut)
def list_transmission_torrents(
    payload: TransmissionConfigIn,
    session: Session = Depends(get_session),
) -> TransmissionTorrentsOut:
    client = TransmissionClient(
        rpc_url=payload.rpc_url,
        username=payload.username,
        password=payload.password,
        verify_tls=payload.verify_tls,
    )

    try:
        torrents = client.get_torrents()
    except Exception as exc:
        logger.exception("Failed to load torrents")
        log_activity_error(session, "transmission-torrents", f"Failed to load torrents from {payload.rpc_url}: {exc}")
        raise HTTPException(status_code=400, detail=f"Failed to load torrents: {exc}") from exc

    torrent_items = [to_torrent_out(torrent) for torrent in torrents]
    known_labels = sorted({label for torrent in torrent_items for label in torrent.labels if label})
    return TransmissionTorrentsOut(torrents=torrent_items, labels=known_labels)


@router.post("/transmission/torrents/label")
def assign_torrent_label(
    payload: TorrentLabelAssignIn,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    label = payload.label.strip()
    if not label:
        raise HTTPException(status_code=400, detail="Label cannot be empty")

    client = TransmissionClient(
        rpc_url=payload.rpc_url,
        username=payload.username,
        password=payload.password,
        verify_tls=payload.verify_tls,
    )

    try:
        torrent = client.add_label(payload.torrent_id, label)
    except Exception as exc:
        logger.exception("Failed to assign label")
        log_activity_error(session, "label-assign", f"Failed to assign label '{label}' to torrent_id={payload.torrent_id}: {exc}")
        raise HTTPException(status_code=400, detail=f"Failed to assign label: {exc}") from exc

    return {"ok": True, "torrent": to_torrent_out(torrent)}
