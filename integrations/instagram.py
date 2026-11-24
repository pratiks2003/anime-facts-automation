"""Instagram Reels publishing via the Instagram API with Instagram Login.

Flow (per Meta docs, checked 2026-11, API v27.0, host graph.instagram.com):
  1. POST /{ig_user_id}/media  media_type=REELS, video_url=<public URL>  -> container id
  2. GET  /{container_id}?fields=status_code   until FINISHED (Meta: poll ~1/min)
  3. POST /{ig_user_id}/media_publish  creation_id=<container id>  -> media id
  4. GET  /{media_id}?fields=permalink
Resumable (direct file) upload is only documented for Facebook Login apps, so the
video must be reachable at a public HTTPS URL that Meta downloads.

CLI (token read from env IG_ACCESS_TOKEN or a local .env file; never printed):
  python -m integrations.instagram check
  python -m integrations.instagram container --video-url URL --caption "..."   # dry run, no publish
  python -m integrations.instagram publish  --video-url URL --caption "..." --confirm
  python -m integrations.instagram refresh-token
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

GRAPH_HOST = "https://graph.instagram.com"
DEFAULT_API_VERSION = "v27.0"
USER_AGENT = "anime-facts-automation/0.1"
CAPTION_MAX = 2200

TRANSIENT_ERROR_CODES = {1, 2, 4, 17, 341}  # Meta: temporary / throttling errors
TOKEN_ERROR_CODES = {190}


class InstagramAPIError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None, code: Optional[int] = None,
                 subcode: Optional[int] = None, fbtrace_id: Optional[str] = None):
        self.status, self.code, self.subcode, self.fbtrace_id = status, code, subcode, fbtrace_id
        details = ", ".join(
            f"{k}={v}" for k, v in (("http", status), ("code", code), ("subcode", subcode), ("trace", fbtrace_id)) if v
        )
        super().__init__(f"{message} ({details})" if details else message)

    @property
    def transient(self) -> bool:
        return (self.status is not None and self.status >= 500) or self.status == 429 or self.code in TRANSIENT_ERROR_CODES


class ContainerError(RuntimeError):
    pass


@dataclass
class PublishResult:
    container_id: str
    status_code: str
    published: bool
    media_id: Optional[str] = None
    permalink: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


class InstagramClient:
    def __init__(
        self,
        access_token: str,
        user_id: Optional[str] = None,
        api_version: str = DEFAULT_API_VERSION,
        opener: Callable = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 60.0,
        max_attempts: int = 3,
    ):
        if not access_token:
            raise ValueError("access_token is required")
        self._token = access_token
        self._user_id = user_id or None
        self.api_version = api_version
        self._opener = opener
        self._sleep = sleep
        self.timeout = timeout
        self.max_attempts = max_attempts

    # ---------- low-level ----------

    def _request(self, method: str, path: str, params: Optional[Dict[str, str]] = None, versioned: bool = True) -> dict:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        params["access_token"] = self._token
        base = f"{GRAPH_HOST}/{self.api_version}" if versioned else GRAPH_HOST
        url = f"{base}/{path.lstrip('/')}"
        encoded = urllib.parse.urlencode(params)
        if method == "GET":
            request = urllib.request.Request(f"{url}?{encoded}", method="GET")
        else:
            request = urllib.request.Request(url, data=encoded.encode(), method=method)
            request.add_header("Content-Type", "application/x-www-form-urlencoded")
        request.add_header("User-Agent", USER_AGENT)

        for attempt in range(1, self.max_attempts + 1):
            try:
                with self._opener(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                error = self._parse_error(exc)
            except (urllib.error.URLError, TimeoutError) as exc:
                # Never include the URL: it contains the access token.
                error = InstagramAPIError(f"network error calling {method} /{path}: {type(exc).__name__}", status=599)
            if error.transient and attempt < self.max_attempts:
                self._sleep(min(2.0 ** attempt * 5, 60.0))
                continue
            raise error
        raise AssertionError("unreachable")

    @staticmethod
    def _parse_error(exc: urllib.error.HTTPError) -> InstagramAPIError:
        try:
            body = json.loads(exc.read().decode("utf-8"))
            err = body.get("error", {})
            return InstagramAPIError(
                err.get("message", "Instagram API error"),
                status=exc.code,
                code=err.get("code"),
                subcode=err.get("error_subcode"),
                fbtrace_id=err.get("fbtrace_id"),
            )
        except Exception:  # body not JSON
            return InstagramAPIError("Instagram API error (non-JSON response)", status=exc.code)

    # ---------- account ----------

    def me(self) -> dict:
        return self._request("GET", "me", {"fields": "user_id,username,account_type"})

    @property
    def user_id(self) -> str:
        if not self._user_id:
            self._user_id = str(self.me()["user_id"])
        return self._user_id

    def publishing_limit(self) -> dict:
        data = self._request("GET", f"{self.user_id}/content_publishing_limit", {"fields": "quota_usage,config"})
        rows = data.get("data") or [{}]
        return rows[0]

    def refresh_token(self) -> dict:
        """Returns {"access_token", "token_type", "expires_in"}. Token must be >= 24h old."""
        return self._request(
            "GET", "refresh_access_token", {"grant_type": "ig_refresh_token"}, versioned=False
        )

    # ---------- publishing ----------

    def create_reel_container(self, video_url: str, caption: str = "", share_to_feed: bool = True,
                              cover_url: Optional[str] = None, thumb_offset_ms: Optional[int] = None) -> str:
        if not video_url.startswith("https://"):
            raise ValueError("video_url must be a public https:// URL")
        if len(caption) > CAPTION_MAX:
            raise ValueError(f"caption is {len(caption)} chars; Instagram max is {CAPTION_MAX}")
        if caption.count("#") > 30:
            raise ValueError("caption has more than 30 hashtags")
        data = self._request("POST", f"{self.user_id}/media", {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "true" if share_to_feed else "false",
            "cover_url": cover_url,
            "thumb_offset": str(thumb_offset_ms) if thumb_offset_ms is not None else None,
        })
        if "id" not in data:
            raise InstagramAPIError(f"container creation returned no id: {data}")
        return str(data["id"])

    def container_status(self, container_id: str) -> dict:
        return self._request("GET", container_id, {"fields": "status_code,status"})

    def wait_for_container(self, container_id: str, interval: float = 30.0, timeout: float = 600.0) -> str:
        """Poll until FINISHED. Raises ContainerError on ERROR/EXPIRED/timeout."""
        waited = 0.0
        while True:
            info = self.container_status(container_id)
            status = info.get("status_code")
            if status in ("FINISHED", "PUBLISHED"):
                return status
            if status in ("ERROR", "EXPIRED"):
                raise ContainerError(f"container {container_id} is {status}: {info.get('status', 'no detail')}")
            if waited >= timeout:
                raise ContainerError(f"container {container_id} still {status} after {int(waited)}s")
            self._sleep(interval)
            waited += interval

    def publish_container(self, container_id: str) -> str:
        data = self._request("POST", f"{self.user_id}/media_publish", {"creation_id": container_id})
        if "id" not in data:
            raise InstagramAPIError(f"media_publish returned no media id: {data}")
        return str(data["id"])

    def permalink(self, media_id: str) -> Optional[str]:
        return self._request("GET", media_id, {"fields": "permalink"}).get("permalink")

    def publish_reel(self, video_url: str, caption: str, publish: bool = False, **container_kwargs) -> PublishResult:
        """Full flow. With publish=False (default) it stops after the container is FINISHED."""
        container_id = self.create_reel_container(video_url, caption, **container_kwargs)
        status = self.wait_for_container(container_id)
        if not publish or status == "PUBLISHED":
            return PublishResult(container_id, status, published=status == "PUBLISHED")
        media_id = self.publish_container(container_id)
        # Only report published once Meta has returned a media id.
        return PublishResult(container_id, "PUBLISHED", True, media_id, self.permalink(media_id))


# ---------- config & CLI ----------

def load_env_file(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines). Existing env vars win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


def client_from_env() -> InstagramClient:
    load_env_file(Path(__file__).resolve().parent.parent / ".env")
    token = os.environ.get("IG_ACCESS_TOKEN", "")
    if not token:
        raise SystemExit("IG_ACCESS_TOKEN is not set (add it to .env locally or GitHub Secrets in CI)")
    return InstagramClient(
        token,
        user_id=os.environ.get("IG_USER_ID") or None,
        api_version=os.environ.get("IG_API_VERSION") or DEFAULT_API_VERSION,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Instagram Reels publishing")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="verify token, show account and publishing quota")
    for name in ("container", "publish"):
        p = sub.add_parser(name)
        p.add_argument("--video-url", required=True)
        p.add_argument("--caption", default="")
        p.add_argument("--no-feed", action="store_true", help="Reels tab only, not main feed")
        if name == "publish":
            p.add_argument("--confirm", action="store_true", help="required: actually publish")
    r = sub.add_parser("refresh-token", help="refresh long-lived token; writes new token to a file, never stdout")
    r.add_argument("--out", default=".ig_token.new", help="file to write the refreshed token to")
    args = parser.parse_args(argv)

    client = client_from_env()
    try:
        if args.command == "check":
            me = client.me()
            limit = client.publishing_limit()
            print(json.dumps({"username": me.get("username"), "user_id": me.get("user_id"),
                              "account_type": me.get("account_type"), "publishing_limit": limit}, indent=2))
        elif args.command == "container":
            result = client.publish_reel(args.video_url, args.caption, publish=False, share_to_feed=not args.no_feed)
            print(json.dumps(result.to_dict(), indent=2))
        elif args.command == "publish":
            if not args.confirm:
                print("refusing to publish without --confirm", file=sys.stderr)
                return 2
            result = client.publish_reel(args.video_url, args.caption, publish=True, share_to_feed=not args.no_feed)
            print(json.dumps(result.to_dict(), indent=2))
        elif args.command == "refresh-token":
            data = client.refresh_token()
            out = Path(args.out)
            out.write_text(data["access_token"], encoding="utf-8")
            out.chmod(0o600)
            print(json.dumps({"written_to": str(out), "expires_in_days": round(data.get("expires_in", 0) / 86400, 1)}))
    except (InstagramAPIError, ContainerError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
