from __future__ import annotations

from typing import Any, Optional

import logging
import requests
import time


class TransmissionClient:
    def __init__(self, rpc_url: str, username: Optional[str], password: Optional[str], verify_tls: bool = True):
        self.rpc_url = rpc_url
        # Only set auth if both username and password are provided and not None
        if username is not None and password is not None:
            self.auth = (str(username), str(password))
        else:
            self.auth = None
        self.verify_tls = verify_tls
        self._session_id: Optional[str] = None
        self._http = requests.Session()

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        start_time = time.time()
        try:
            headers: dict[str, str] = {}
            if self._session_id:
                headers["X-Transmission-Session-Id"] = self._session_id

            response = self._http.post(
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
                response = self._http.post(
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
        finally:
            duration = time.time() - start_time
            logging.info(f"Transmission RPC call to {self.rpc_url} took {duration:.2f} seconds")

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

    def remove_label(self, torrent_id: int, label: str) -> dict[str, Any]:
        torrent = self.get_torrent(torrent_id)
        existing_labels = list(torrent.get("labels") or [])
        remove_label = label.strip()
        if not remove_label:
            raise RuntimeError("Label cannot be empty")

        updated_labels = [item for item in existing_labels if str(item) != remove_label]

        payload = {
            "method": "torrent-set",
            "arguments": {
                "ids": [torrent_id],
                "labels": updated_labels,
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
