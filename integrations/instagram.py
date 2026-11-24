"""Instagram Reels publishing (Meta Graph API v27.0, docs checked 2026-11-24).

Two login setups are supported (env IG_LOGIN):
  unified    (default) Instagram API with Unified Login. Business Integration System User
             token ("EAA..."), host graph.facebook.com, IG account found via
             /me/assigned_instagram_accounts. Supports resumable (direct file) upload.
  instagram  Instagram API with Instagram Login. Instagram User token ("IGAA..."),
             host graph.instagram.com, account via /me. Video must be at a public URL.

Flow:
  1. POST /{ig_id}/media  media_type=REELS + (video_url | upload_type=resumable)  -> container id
  1b. (resumable) POST rupload.facebook.com/ig-api-upload/{ver}/{container}  file bytes
  2. GET  /{container}?fields=status_code   until FINISHED (Meta: poll ~1/min)
  3. POST /{ig_id}/media_publish  creation_id=<container>  -> media id
  4. GET  /{media_id}?fields=permalink

CLI (token from env IG_ACCESS_TOKEN or local .env; never printed):
  python -m integrations.instagram check
  python -m integrations.instagram container --video-file output/x.mp4 --caption "..."   # dry run
  python -m integrations.instagram publish   --video-file output/x.mp4 --caption "..." --confirm
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
from typing import Callable, Dict, Optional, Tuple

HOSTS = {"unified": "https://graph.facebook.com", "instagram": "https://graph.instagram.com"}
RUPLOAD_HOST = "https://rupload.facebook.com/ig-api-upload"
DEFAULT_API_VERSION = "v27.0"
USER_AGENT = "anime-facts-automation/0.1"
CAPTION_MAX = 2200
MAX_VIDEO_BYTES = 300 * 1024 * 1024

TRANSIENT_ERROR_CODES = {1, 2, 4, 17, 341}  # Meta: temporary / throttling errors


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
        login: str = "unified",
        api_version: str = DEFAULT_API_VERSION,
        opener: Callable = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 120.0,
        max_attempts: int = 3,
    ):
        if not access_token:
            raise ValueError("access_token is required")
        if login not in HOSTS:
            raise ValueError(f"login must be one of {list(HOSTS)}")
        self._token = access_token
        self._user_id = user_id or None
        self._username: Optional[str] = None
        self.login = login
        self.host = HOSTS[login]
        self.api_version = api_version
        self._opener = opener
        self._sleep = sleep
        self.timeout = timeout
        self.max_attempts = max_attempts

    # ---------- low-level ----------

    def _send(self, request: urllib.request.Request, what: str) -> dict:
        for attempt in range(1, self.max_attempts + 1):
            try:
                with self._opener(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                error = self._parse_error(exc)
            except (urllib.error.URLError, TimeoutError) as exc:
                # Never include the URL or headers: they contain the access token.
                error = InstagramAPIError(f"network error calling {what}: {type(exc).__name__}", status=599)
            if error.transient and attempt < self.max_attempts:
                self._sleep(min(2.0 ** attempt * 5, 60.0))
                continue
            raise error
        raise AssertionError("unreachable")

    def _request(self, method: str, path: str, params: Optional[Dict[str, str]] = None, versioned: bool = True) -> dict:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        params["access_token"] = self._token
        base = f"{self.host}/{self.api_version}" if versioned else self.host
        url = f"{base}/{path.lstrip('/')}"
        encoded = urllib.parse.urlencode(params)
        if method == "GET":
            request = urllib.request.Request(f"{url}?{encoded}", method="GET")
        else:
            request = urllib.request.Request(url, data=encoded.encode(), method=method)
            request.add_header("Content-Type", "application/x-www-form-urlencoded")
        request.add_header("User-Agent", USER_AGENT)
        return self._send(request, f"{method} /{path.split('?')[0]}")

    @staticmethod
    def _parse_error(exc: urllib.error.HTTPError) -> InstagramAPIError:
        try:
            body = json.loads(exc.read().decode("utf-8"))
            err = body.get("error", body.get("debug_info", {}))
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

    def account(self) -> dict:
        """Return {"user_id", "username"} of the Instagram professional account."""
        if self.login == "instagram":
            data = self._request("GET", "me", {"fields": "user_id,username"})
            return {"user_id": str(data["user_id"]), "username": data.get("username")}

        data = self._request("GET", "me/assigned_instagram_accounts", {"fields": "id,username"}).get("data", [])
        if not data:
            raise InstagramAPIError("token has no Instagram accounts assigned; reconnect the account")
        if self._user_id:
            match = [a for a in data if str(a["id"]) == str(self._user_id)]
            if not match:
                raise InstagramAPIError("IG_USER_ID is not among the accounts this token can access")
            data = match
        elif len(data) > 1:
            raise InstagramAPIError("token can access several Instagram accounts; set IG_USER_ID to choose one")
        return {"user_id": str(data[0]["id"]), "username": data[0].get("username")}

    @property
    def user_id(self) -> str:
        if not self._user_id:
            self._user_id = self.account()["user_id"]
        return self._user_id

    def publishing_limit(self) -> dict:
        data = self._request("GET", f"{self.user_id}/content_publishing_limit", {"fields": "quota_usage,config"})
        rows = data.get("data") or [{}]
        return rows[0]

    def refresh_token(self) -> dict:
        """Instagram Login only. Returns {"access_token", "token_type", "expires_in"}; token must be >= 24h old."""
        if self.login != "instagram":
            raise ValueError(
                "Unified Login tokens are not refreshed this way; their expiry is set by the login "
                "configuration (see the configuration preview in the App Dashboard)"
            )
        return self._request("GET", "refresh_access_token", {"grant_type": "ig_refresh_token"}, versioned=False)

    # ---------- publishing ----------

    @staticmethod
    def _check_caption(caption: str) -> None:
        if len(caption) > CAPTION_MAX:
            raise ValueError(f"caption is {len(caption)} chars; Instagram max is {CAPTION_MAX}")
        if caption.count("#") > 30:
            raise ValueError("caption has more than 30 hashtags")

    def _container_params(self, caption: str, share_to_feed: bool, cover_url: Optional[str],
                          thumb_offset_ms: Optional[int]) -> Dict[str, Optional[str]]:
        self._check_caption(caption)
        return {
            "media_type": "REELS",
            "caption": caption,
            "share_to_feed": "true" if share_to_feed else "false",
            "cover_url": cover_url,
            "thumb_offset": str(thumb_offset_ms) if thumb_offset_ms is not None else None,
        }

    def create_reel_container(self, video_url: str, caption: str = "", share_to_feed: bool = True,
                              cover_url: Optional[str] = None, thumb_offset_ms: Optional[int] = None) -> str:
        """Container from a public video URL (Meta downloads it)."""
        if not video_url.startswith("https://"):
            raise ValueError("video_url must be a public https:// URL")
        params = self._container_params(caption, share_to_feed, cover_url, thumb_offset_ms)
        params["video_url"] = video_url
        data = self._request("POST", f"{self.user_id}/media", params)
        if "id" not in data:
            raise InstagramAPIError(f"container creation returned no id: {data}")
        return str(data["id"])

    def upload_reel_file(self, video_path: str, caption: str = "", share_to_feed: bool = True,
                         cover_url: Optional[str] = None, thumb_offset_ms: Optional[int] = None) -> str:
        """Container via resumable upload of a local file (Unified / Facebook Login only)."""
        if self.login == "instagram":
            raise ValueError("resumable upload is not available with Instagram Login; use a public video_url")
        path = Path(video_path)
        if not path.is_file():
            raise ValueError(f"video file not found: {path}")
        size = path.stat().st_size
        if not 0 < size <= MAX_VIDEO_BYTES:
            raise ValueError(f"video is {size} bytes; must be between 1 byte and 300 MB")

        params = self._container_params(caption, share_to_feed, cover_url, thumb_offset_ms)
        params["upload_type"] = "resumable"
        try:
            data = self._request("POST", f"{self.user_id}/media", params)
        except InstagramAPIError as exc:
            raise InstagramAPIError(f"create container: {exc}", exc.status, exc.code, exc.subcode, exc.fbtrace_id) from None
        if "id" not in data:
            raise InstagramAPIError(f"resumable container creation returned no id: {data}")
        container_id = str(data["id"])
        upload_uri = data.get("uri") or f"{RUPLOAD_HOST}/{self.api_version}/{container_id}"
        if not upload_uri.startswith("https://rupload.facebook.com/"):
            raise InstagramAPIError("unexpected upload URI host returned by Meta")

        request = urllib.request.Request(upload_uri, data=path.read_bytes(), method="POST")
        request.add_header("Authorization", f"OAuth {self._token}")
        request.add_header("offset", "0")
        request.add_header("file_size", str(size))
        request.add_header("User-Agent", USER_AGENT)
        try:
            result = self._send(request, "POST rupload")
        except InstagramAPIError as exc:
            raise InstagramAPIError(f"upload video: {exc}", exc.status, exc.code, exc.subcode, exc.fbtrace_id) from None
        if not result.get("success"):
            raise InstagramAPIError(f"video upload was not accepted: {result}")
        return container_id

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
        try:
            data = self._request("POST", f"{self.user_id}/media_publish", {"creation_id": container_id})
        except InstagramAPIError as exc:
            raise InstagramAPIError(f"publish: {exc}", exc.status, exc.code, exc.subcode, exc.fbtrace_id) from None
        if "id" not in data:
            raise InstagramAPIError(f"media_publish returned no media id: {data}")
        return str(data["id"])

    def permalink(self, media_id: str) -> Optional[str]:
        return self._request("GET", media_id, {"fields": "permalink"}).get("permalink")

    def _prepare_container(self, caption: str, video_url: Optional[str], video_path: Optional[str],
                           attempts: int = 3, **container_kwargs) -> Tuple[str, str]:
        """Create + upload + wait, retrying the whole sequence on Meta's intermittent failures
        ("Request processing failed", container ERROR). Returns (container_id, status)."""
        last: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                if video_path:
                    container_id = self.upload_reel_file(video_path, caption, **container_kwargs)
                else:
                    container_id = self.create_reel_container(video_url, caption, **container_kwargs)
                return container_id, self.wait_for_container(container_id)
            except (InstagramAPIError, ContainerError) as exc:
                last = exc
                retryable = isinstance(exc, ContainerError) or exc.transient or "processing failed" in str(exc).lower()
                if not retryable or attempt == attempts:
                    raise
                self._sleep(20 * attempt)
        raise last  # pragma: no cover

    def publish_reel(self, caption: str, video_url: Optional[str] = None, video_path: Optional[str] = None,
                     publish: bool = False, **container_kwargs) -> PublishResult:
        """Full flow. With publish=False (default) it stops once the container is FINISHED."""
        if bool(video_url) == bool(video_path):
            raise ValueError("pass exactly one of video_url or video_path")
        container_id, status = self._prepare_container(caption, video_url, video_path, **container_kwargs)
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
        login=os.environ.get("IG_LOGIN") or "unified",
        api_version=os.environ.get("IG_API_VERSION") or DEFAULT_API_VERSION,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Instagram Reels publishing")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="verify token, show account and publishing quota")
    for name in ("container", "publish"):
        p = sub.add_parser(name)
        source = p.add_mutually_exclusive_group(required=True)
        source.add_argument("--video-file", help="local MP4, uploaded directly (Unified Login)")
        source.add_argument("--video-url", help="public https URL Meta downloads")
        p.add_argument("--caption", default="")
        p.add_argument("--no-feed", action="store_true", help="Reels tab only, not main feed")
        if name == "publish":
            p.add_argument("--confirm", action="store_true", help="required: actually publish")
    r = sub.add_parser("refresh-token", help="Instagram Login only; writes new token to a file, never stdout")
    r.add_argument("--out", default=".ig_token.new")
    args = parser.parse_args(argv)

    client = client_from_env()
    try:
        if args.command == "check":
            account = client.account()
            client._user_id = account["user_id"]
            print(json.dumps({"login": client.login, **account, "publishing_limit": client.publishing_limit()}, indent=2))
        elif args.command in ("container", "publish"):
            if args.command == "publish" and not args.confirm:
                print("refusing to publish without --confirm", file=sys.stderr)
                return 2
            result = client.publish_reel(
                args.caption, video_url=args.video_url, video_path=args.video_file,
                publish=args.command == "publish", share_to_feed=not args.no_feed,
            )
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
