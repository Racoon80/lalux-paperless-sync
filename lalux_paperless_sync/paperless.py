"""Minimal Paperless-ngx REST client: upload a document with tags and correspondent."""

from __future__ import annotations

import hashlib
import json
import time

import requests

TIMEOUT = 60


class PaperlessClient:
    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self.session = requests.Session()
        # No pinned API version: Paperless 3.x rejects the old ones.
        self.session.headers.update({"Authorization": f"Token {token}"})
        self._ids: dict[tuple[str, str], int] = {}

    def check(self) -> None:
        r = self.session.get(f"{self.url}/api/tags/", params={"page_size": 1}, timeout=TIMEOUT)
        if r.status_code in (401, 403):
            raise RuntimeError(f"Paperless rejected the token ({r.status_code})")
        r.raise_for_status()

    def ensure(self, kind: str, name: str) -> int:
        """Id of the tag/correspondent/document_type called `name`, created if missing."""
        key = (kind, name.lower())
        if key in self._ids:
            return self._ids[key]
        r = self.session.get(f"{self.url}/api/{kind}/",
                             params={"name__iexact": name}, timeout=TIMEOUT)
        r.raise_for_status()
        results = r.json().get("results", [])
        if results:
            obj_id = results[0]["id"]
        else:
            r = self.session.post(f"{self.url}/api/{kind}/", json={"name": name}, timeout=TIMEOUT)
            r.raise_for_status()
            obj_id = r.json()["id"]
        self._ids[key] = obj_id
        return obj_id

    def find_by_content(self, content: bytes) -> int | None:
        """Id of a document with exactly this file, if Paperless has one already.

        Paperless 3.x stores SHA-256 checksums, 2.x MD5; try both. Paperless 3
        no longer refuses duplicates on its own, so this is the only guard.
        """
        for digest in (hashlib.sha256(content), hashlib.md5(content)):
            r = self.session.get(f"{self.url}/api/documents/",
                                 params={"checksum__iexact": digest.hexdigest(),
                                         "fields": "id"},
                                 timeout=TIMEOUT)
            r.raise_for_status()
            results = r.json().get("results", [])
            if results:
                return results[0]["id"]
        return None

    def upload(self, filename: str, content: bytes, title: str,
               tags: list[int], correspondent: int | None,
               document_type: int | None) -> str:
        data: list[tuple[str, str]] = [("title", title)]
        data += [("tags", str(t)) for t in tags]
        if correspondent:
            data.append(("correspondent", str(correspondent)))
        if document_type:
            data.append(("document_type", str(document_type)))
        r = self.session.post(
            f"{self.url}/api/documents/post_document/",
            data=data,
            files={"document": (filename, content)},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return str(r.json())  # the consume task id

    def wait_for_task(self, task_id: str, timeout: int = 120) -> tuple[str, str]:
        """Return (status, message) of a consume task, polling until it finishes."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r = self.session.get(f"{self.url}/api/tasks/",
                                 params={"task_id": task_id}, timeout=TIMEOUT)
            r.raise_for_status()
            tasks = r.json()
            if isinstance(tasks, dict):
                tasks = tasks.get("results", [])
            if tasks:
                # Paperless 2.x: "SUCCESS" + result; 3.x: "success" + result_data.
                status = str(tasks[0].get("status", "")).upper()
                if status in ("SUCCESS", "FAILURE", "REVOKED"):
                    result = tasks[0].get("result") or tasks[0].get("result_data") or ""
                    return status, result if isinstance(result, str) else json.dumps(result)
            time.sleep(3)
        return "PENDING", "still processing"
