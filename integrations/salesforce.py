"""Salesforce REST client for GitHub Actions callbacks.

Auth: OAuth 2.0 Client Credentials flow against an External Client App whose
"run as" user is the API-only integration user (permission set InstaAgent_Integration).
Env vars (GitHub Secrets):
  SF_DOMAIN         My Domain host, e.g. orgfarm-xxxx-dev-ed.develop.my.salesforce.com
  SF_CLIENT_ID      External Client App consumer key
  SF_CLIENT_SECRET  External Client App consumer secret
Records are addressed by external IDs (Anime_Content__c.External_Id__c,
Video_Job__c.Correlation_Id__c, Social_Post__c.Instagram_Media_Id__c), so every
callback is an idempotent upsert.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

API_VERSION = "v64.0"
USER_AGENT = "anime-facts-automation/0.1"


class SalesforceError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None):
        self.status = status
        super().__init__(message)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class SalesforceClient:
    def __init__(self, domain: str, client_id: str, client_secret: str,
                 opener: Callable = urllib.request.urlopen, sleep: Callable[[float], None] = time.sleep):
        if not domain or not client_id or not client_secret:
            raise ValueError("SF_DOMAIN, SF_CLIENT_ID and SF_CLIENT_SECRET are required")
        self.domain = domain.replace("https://", "").rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._opener = opener
        self._sleep = sleep
        self._token: Optional[str] = None
        self.instance_url: Optional[str] = None

    @classmethod
    def from_env(cls) -> Optional["SalesforceClient"]:
        domain, cid, secret = (os.environ.get(k, "") for k in ("SF_DOMAIN", "SF_CLIENT_ID", "SF_CLIENT_SECRET"))
        if not (domain and cid and secret):
            return None
        return cls(domain, cid, secret)

    # ------------------------------------------------------------ transport

    def _authenticate(self) -> None:
        body = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }).encode()
        req = urllib.request.Request(f"https://{self.domain}/services/oauth2/token", data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("User-Agent", USER_AGENT)
        try:
            with self._opener(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise SalesforceError(f"Salesforce auth failed: HTTP {exc.code} {detail}", exc.code) from None
        self._token = data["access_token"]
        self.instance_url = data.get("instance_url") or f"https://{self.domain}"

    def apex_post(self, path: str, payload: dict, timeout_attempts: int = 1) -> Optional[dict]:
        """POST to an Apex REST resource (/services/apexrest/...)."""
        return self.request("POST", path, payload, attempts=2, base="apexrest")

    def request(self, method: str, path: str, payload: Optional[dict] = None, attempts: int = 3,
                base: str = "data") -> Optional[dict]:
        if not self._token:
            self._authenticate()
        for attempt in range(1, attempts + 1):
            prefix = f"/services/data/{API_VERSION}" if base == "data" else "/services/apexrest"
            url = f"{self.instance_url}{prefix}/{path.lstrip('/')}"
            data = json.dumps(payload).encode() if payload is not None else None
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("Authorization", f"Bearer {self._token}")
            req.add_header("Content-Type", "application/json")
            req.add_header("User-Agent", USER_AGENT)
            try:
                with self._opener(req, timeout=180) as resp:
                    raw = resp.read()
                    return json.loads(raw.decode()) if raw else None
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")[:500]
                if exc.code == 401 and attempt < attempts:
                    self._authenticate()  # token expired
                    continue
                if exc.code >= 500 and attempt < attempts:
                    self._sleep(3 * attempt)
                    continue
                raise SalesforceError(f"{method} {path.split('?')[0]} failed: HTTP {exc.code} {detail}", exc.code) from None
            except urllib.error.URLError as exc:
                if attempt < attempts:
                    self._sleep(3 * attempt)
                    continue
                raise SalesforceError(f"network error calling Salesforce: {type(exc).__name__}") from None
        return None

    # ------------------------------------------------------------ helpers

    def upsert(self, sobject: str, ext_field: str, ext_value: str, fields: Dict) -> Optional[dict]:
        path = f"sobjects/{sobject}/{ext_field}/{urllib.parse.quote(ext_value, safe='')}"
        return self.request("PATCH", path, {k: v for k, v in fields.items()})

    def query(self, soql: str) -> List[dict]:
        data = self.request("GET", f"query?q={urllib.parse.quote(soql)}") or {}
        return data.get("records", [])

    # ------------------------------------------------------------ pipeline callbacks

    def job_update(self, job_id: str, **fields) -> None:
        """Video_Job__c by Correlation_Id__c (job_id input from the dispatch)."""
        self.upsert("Video_Job__c", "Correlation_Id__c", job_id, fields)

    def reel_update(self, content_id: str, **fields) -> None:
        """Anime_Content__c by External_Id__c."""
        self.upsert("Anime_Content__c", "External_Id__c", content_id, fields)

    def record_post(self, content_id: str, media_id: str, permalink: Optional[str], trial: bool = False) -> None:
        self.upsert("Social_Post__c", "Instagram_Media_Id__c", media_id, {
            "Is_Trial__c": trial,
            "Content__r": {"External_Id__c": content_id},
            "Platform__c": "Instagram",
            "Permalink__c": permalink,
            "Published_At__c": now_iso(),
            "Status__c": "Published",
        })


class NullSalesforce:
    """Used when Salesforce isn't configured (local runs): callbacks become log lines."""

    def __getattr__(self, name):
        def log(*args, **kwargs):
            print(f"[salesforce:skipped] {name} {args[:1]} {sorted(kwargs)}")
        return log
