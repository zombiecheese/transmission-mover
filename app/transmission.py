from __future__ import annotations

from typing import Any, Optional

import requests


class TransmissionClient:
    def __init__(self, rpc_url: str, username: Optional[str], password: Optional[str], verify_tls: bool = True):
        self.rpc_url = rpc_url
        self.auth = (username, password) if username else None
        self.verify_tls = verify_tls
        self._session_id: Optional[str] = None

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if self._session_id:
            headers["X-Transmission-Session-Id"] = self._session_id

        response = requests.post(
            self.rpc_url,
            json=payload,
            headers=headers,
            auth=self.auth,
            verify=self.verify_tls,
            timeout=30,
        )

        if response.status_code == 409:
            self._session_id = response.headers.get("X-Transmission-Session-Id")
            headers["X-Transmission-Session-Id"] = self._session_id or ""
            response = requests.post(
                self.rpc_url,
                json=payload,
                headers=headers,
                auth=self.auth,
                verify=self.verify_tls,
                timeout=30,
            )

        response.raise_for_status()
        data = response.json()
        if data.get("result") != "success":
            raise RuntimeError(f"Transmission RPC failed: {data.get('result')}")
        return data

    def get_torrents(self) -> list[dict[str, Any]]:
        payload = {
            "method": "torrent-get",
            "arguments": {
                "fields": [
                    "id",
                    "name",
                    "labels",
                    "status",
                    "percentDone",
                    "isFinished",
                    "downloadDir",
                ]
            },
        }
        return self._post(payload)["arguments"]["torrents"]

    def get_torrent(self, torrent_id: int) -> dict[str, Any]:
        payload = {
            "method": "torrent-get",
            "arguments": {
                "ids": [torrent_id],
                "fields": ["id", "name", "labels", "status", "percentDone"],
            },
        }
        torrents = self._post(payload)["arguments"]["torrents"]
        if not torrents:
            raise RuntimeError(f"Torrent {torrent_id} not found")
        return torrents[0]

    def add_label(self, torrent_id: int, label: str) -> dict[str, Any]:
        torrent = self.get_torrent(torrent_id)
        existing_labels = list(torrent.get("labels") or [])
        new_label = label.strip()
        if not new_label:
            raise RuntimeError("Label cannot be empty")
        if new_label not in existing_labels:
            existing_labels.append(new_label)

        payload = {
            "method": "torrent-set",
            "arguments": {
                "ids": [torrent_id],
                "labels": existing_labels,
            },
        }
        self._post(payload)
        return self.get_torrent(torrent_id)

    def remove_torrent(self, torrent_id: int, delete_local_data: bool = False) -> None:
        payload = {
            "method": "torrent-remove",
            "arguments": {
                "ids": [torrent_id],
                "delete-local-data": delete_local_data,
            },
        }
        self._post(payload)

    def ping(self) -> None:
        payload = {"method": "session-get", "arguments": {}}
        self._post(payload)
