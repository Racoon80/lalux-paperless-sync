"""Client for the (undocumented) API behind LALUX easyAPP.

The endpoints and the login flow were read from the easyAPP web client
(easyapphome.lalux.lu). LALUX does not publish this API, so it may change
without notice.

Login is a two-step Keycloak password grant:
  1. username + password + otp_type        -> session_token (OTP is sent)
  2. otp + session_code + scope=offline_access -> access + offline refresh token
The offline refresh token is what lets the sync run unattended afterwards.
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.parse
from dataclasses import dataclass
from typing import Callable

import requests

AUTH_URL = "https://auth.lalux-partners.lu/auth/realms/ClientExternalRealm"
SECURE_API = "https://api-client-external-secure.lalux-partners.lu"
CLIENT_ID = "appmobile-client"
APP_SOURCE = "EASY_APP_HOME"
APP_VERSION = "1.4.13"
TIMEOUT = 60


class LaluxError(Exception):
    pass


class ReloginRequired(LaluxError):
    """The stored refresh token is no longer accepted; run `login` again."""


@dataclass
class Document:
    id: str
    label: str
    category: str
    subcategory: str


@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    scope: str = ""


class LaluxClient:
    def __init__(self, language: str = "fr",
                 on_refresh: Callable[[str], None] | None = None) -> None:
        self.language = language
        self.session = requests.Session()
        self.access_token: str | None = None
        self.access_expires = 0.0
        self.refresh_token: str | None = None
        # Called with the new refresh token whenever Keycloak rotates it.
        self.on_refresh = on_refresh

    # --- auth -------------------------------------------------------------

    def _token_request(self, data: dict) -> dict:
        data = {"client_id": CLIENT_ID, "grant_type": "password", **data}
        r = self.session.post(
            f"{AUTH_URL}/protocol/openid-connect/token",
            data=data,
            headers={"Accept-Language": self.language},
            timeout=TIMEOUT,
        )
        try:
            body = r.json()
        except ValueError:
            body = {"error": r.text[:200]}
        if not r.ok:
            raise LaluxError(f"token request failed ({r.status_code}): "
                             f"{body.get('error')} {body.get('error_description', '')}".strip())
        return body

    def start_login(self, username: str, password: str, otp_type: str = "SMS") -> str:
        body = self._token_request(
            {"username": username, "password": password, "otp_type": otp_type})
        token = body.get("session_token")
        if not token:
            raise LaluxError(f"no session_token in login response (keys: {sorted(body)})")
        return token

    def finish_login(self, session_token: str, otp: str, otp_type: str = "SMS") -> Tokens:
        body = self._token_request({
            "otp": otp,
            "session_code": session_token,
            "otp_type": otp_type,
            "scope": "offline_access",
        })
        self._store(body)
        return Tokens(body["access_token"], body["refresh_token"], body.get("scope", ""))

    def refresh(self, refresh_token: str) -> Tokens:
        try:
            body = self._token_request(
                {"grant_type": "refresh_token", "refresh_token": refresh_token})
        except LaluxError as e:
            if "invalid_grant" in str(e):
                raise ReloginRequired(str(e)) from e
            raise
        # Keycloak may rotate the refresh token; keep the old one otherwise.
        body.setdefault("refresh_token", refresh_token)
        rotated = body["refresh_token"] != refresh_token
        self._store(body)
        if rotated and self.on_refresh:
            self.on_refresh(body["refresh_token"])
        return Tokens(body["access_token"], body["refresh_token"], body.get("scope", ""))

    def _store(self, body: dict) -> None:
        self.access_token = body["access_token"]
        self.refresh_token = body["refresh_token"]
        # Access tokens only live a few minutes; remember when this one ends.
        self.access_expires = _jwt_exp(self.access_token) or (
            time.time() + int(body.get("expires_in", 300)))

    def _ensure_fresh(self, force: bool = False) -> None:
        if self.refresh_token and (force or time.time() > self.access_expires - 30):
            self.refresh(self.refresh_token)

    # --- api --------------------------------------------------------------

    def _get(self, path: str, **kwargs) -> requests.Response:
        if not self.access_token:
            raise LaluxError("not logged in")
        self._ensure_fresh()
        for attempt in (1, 2):
            r = self.session.get(
                f"{SECURE_API}{path}",
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Accept-Language": self.language,
                    "App-Version": APP_VERSION,
                    "App-Source": APP_SOURCE,
                },
                timeout=TIMEOUT,
                **kwargs,
            )
            if r.status_code != 401 or attempt == 2 or not self.refresh_token:
                break
            self._ensure_fresh(force=True)
        if r.status_code == 401:
            raise LaluxError(f"GET {path}: 401 unauthorized")
        r.raise_for_status()
        return r

    def list_documents(self, sources: set[str] | None = None) -> list[Document]:
        """Every downloadable document, from the sources in `sources`.

        available  the "Documents" tab (tax certificates and similar)
        contracts  the documents attached to each contract (terms and conditions)
        invoices   the PDF behind each premium invoice
        All three are downloaded the same way, through /documents/{id}.
        """
        sources = sources or {"available", "contracts", "invoices"}
        docs: dict[str, Document] = {}

        def add(doc_id, label, category, subcategory=""):
            if doc_id and str(doc_id) not in docs:
                docs[str(doc_id)] = Document(str(doc_id), label or str(doc_id),
                                             category or "", subcategory or "")

        if "available" in sources:
            for cat in self._get("/documents/available").json() or []:
                for sub in cat.get("subCategories") or []:
                    for d in sub.get("documents") or []:
                        add(d.get("idDocument"), d.get("label"),
                            cat.get("libelleCategory"), sub.get("title"))

        if "contracts" in sources:
            for group in self._get("/contracts").json() or []:
                for contract in group.get("contracts") or []:
                    for obj in contract.get("contractObjects") or []:
                        if not obj.get("id"):
                            continue
                        detail = self._get(
                            f"/contracts/{urllib.parse.quote(obj['id'], safe='')}").json()
                        name = " ".join(p for p in (detail.get("title"),
                                                    detail.get("subTitle")) if p)
                        for d in detail.get("listDocument") or []:
                            add(d.get("idDocument"), d.get("label"),
                                group.get("typeName"), name)

        if "invoices" in sources:
            for item in self._invoices():
                if not item.get("documentAvailable"):
                    continue
                detail = self._get(
                    f"/invoices/{urllib.parse.quote(item['id'], safe='')}").json()
                label = " ".join(p for p in (item.get("label"), item.get("date")) if p)
                add(detail.get("gedDocumentId"), label, "Invoices")

        return list(docs.values())

    def _invoices(self) -> list[dict]:
        items: dict[str, dict] = {}
        offset, limit = 0, 50
        while True:
            page = self._get("/invoices", params={"offset": offset, "limit": limit}).json()
            before = len(items)
            for group in page.get("groups") or []:
                for item in group.get("items") or []:
                    if item.get("id"):
                        items.setdefault(item["id"], item)
            paging = page.get("pagingInfo") or {}
            offset += limit
            # Stop at the end, or if the server ignores paging and repeats itself.
            if len(items) == before or offset >= int(paging.get("total") or 0):
                return list(items.values())

    def download(self, doc: Document) -> tuple[str, bytes]:
        r = self._get(f"/documents/{urllib.parse.quote(doc.id, safe='')}",
                      params={"idFile": doc.id})
        filename = _filename(r.headers.get("content-disposition", ""))
        return filename or f"{_safe(doc.label)}.pdf", r.content


def _jwt_exp(token: str) -> float | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (IndexError, KeyError, ValueError):
        return None


def _filename(disposition: str) -> str | None:
    m = re.search(r'filename="([^"]+)"', urllib.parse.unquote(disposition))
    return m.group(1) if m else None


def _safe(name: str) -> str:
    return re.sub(r"[^\w.\- ]+", "_", name).strip() or "document"
