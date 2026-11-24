"""Host Reel previews (MP4 + cover) as assets of a single 'reels' release in the public repo.

Salesforce stores only the URLs (its file storage is 20 MB). Old assets are pruned.
"""

from __future__ import annotations

import json
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Optional

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
TAG = "reels"


class ReleaseStore:
    def __init__(self, repo: str, token: str, opener: Callable = urllib.request.urlopen):
        if not repo or not token:
            raise ValueError("GITHUB_REPOSITORY and GITHUB_TOKEN are required")
        self.repo = repo
        self._token = token
        self._opener = opener

    @classmethod
    def from_env(cls) -> Optional["ReleaseStore"]:
        repo, token = os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_TOKEN", "")
        return cls(repo, token) if repo and token else None

    def _req(self, method: str, url: str, data: Optional[bytes] = None, ctype: str = "application/json") -> Optional[dict]:
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "anime-facts-automation/0.1")
        if data is not None:
            req.add_header("Content-Type", ctype)
        with self._opener(req, timeout=300) as resp:
            raw = resp.read()
            return json.loads(raw.decode()) if raw else None

    def release(self) -> dict:
        try:
            return self._req("GET", f"{API}/repos/{self.repo}/releases/tags/{TAG}")
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
        body = {"tag_name": TAG, "name": "Reel previews", "prerelease": True, "make_latest": "false",
                "body": "Auto-generated Reel previews for the InstaAgent admin site. Not a software release."}
        return self._req("POST", f"{API}/repos/{self.repo}/releases", json.dumps(body).encode())

    def upload(self, files: Dict[str, Path]) -> Dict[str, str]:
        """Upload {asset_name: path}; replaces same-named assets. Returns {asset_name: download_url}."""
        rel = self.release()
        existing = {a["name"]: a["id"] for a in rel.get("assets", [])}
        urls = {}
        for name, path in files.items():
            if name in existing:
                self._req("DELETE", f"{API}/repos/{self.repo}/releases/assets/{existing[name]}")
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            asset = self._req("POST", f"{UPLOADS}/repos/{self.repo}/releases/{rel['id']}/assets?name={urllib.parse.quote(name)}",
                              Path(path).read_bytes(), ctype)
            urls[name] = asset["browser_download_url"]
        return urls

    def download(self, name: str, dest: Path) -> Path:
        rel = self.release()
        asset = next((a for a in rel.get("assets", []) if a["name"] == name), None)
        if not asset:
            raise FileNotFoundError(f"release asset {name} not found")
        req = urllib.request.Request(asset["url"], headers={"Authorization": f"Bearer {self._token}",
                                                            "Accept": "application/octet-stream",
                                                            "User-Agent": "anime-facts-automation/0.1"})
        with self._opener(req, timeout=300) as resp:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(resp.read())
        return dest

    def prune(self, keep_days: int = 45) -> int:
        rel = self.release()
        cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
        removed = 0
        for a in rel.get("assets", []):
            created = datetime.fromisoformat(a["created_at"].replace("Z", "+00:00"))
            if created < cutoff:
                self._req("DELETE", f"{API}/repos/{self.repo}/releases/assets/{a['id']}")
                removed += 1
        return removed
