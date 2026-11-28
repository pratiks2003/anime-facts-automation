"""YouTube Shorts cross-posting (YouTube Data API v3, resumable upload). Off unless YT_REFRESH_TOKEN is set.

Until Google approves the API project in its audit, YouTube keeps API uploads private; they become public-capable after.
Quota: an upload costs ~1,600 of the default 10,000 units a day (about 6 uploads).
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"


def log(msg: str) -> None:
    print(f"[youtube] {msg}", file=sys.stderr, flush=True)


def access_token(opener=urllib.request.urlopen) -> Optional[str]:
    cid, secret, refresh = (os.environ.get(k, "").strip() for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"))
    if not (cid and secret and refresh):
        return None
    body = urllib.parse.urlencode({"client_id": cid, "client_secret": secret, "refresh_token": refresh,
                                   "grant_type": "refresh_token"}).encode()
    with opener(urllib.request.Request(TOKEN_URL, data=body), timeout=30) as r:
        return json.loads(r.read().decode())["access_token"]


def shorts_metadata(caption: str, anime: str) -> dict:
    first = caption.strip().split("\n")[0]
    title = re.sub(r"\s+", " ", re.sub(r"#\w+", "", first)).strip()[:85] or f"{anime} secret"
    tags = [t.lstrip("#") for t in re.findall(r"#\w+", caption)][:12]
    return {"snippet": {"title": f"{title} #Shorts", "description": caption[:4800], "tags": tags, "categoryId": "1"},
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}}


def upload_short(video: Path, caption: str, anime: str = "", opener=urllib.request.urlopen) -> Optional[str]:
    """Upload the Reel as a YouTube Short. Returns the video id, or None (off / failed - never raises)."""
    try:
        token = access_token(opener)
        if not token or not video or not Path(video).is_file():
            return None
        meta = json.dumps(shorts_metadata(caption, anime)).encode()
        size = Path(video).stat().st_size
        start = urllib.request.Request(UPLOAD_URL, data=meta, method="POST")
        for k, v in {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
                     "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)}.items():
            start.add_header(k, v)
        with opener(start, timeout=60) as r:
            session = r.headers["Location"]
        put = urllib.request.Request(session, data=Path(video).read_bytes(), method="PUT")
        put.add_header("Authorization", f"Bearer {token}")
        put.add_header("Content-Type", "video/mp4")
        with opener(put, timeout=600) as r:
            video_id = json.loads(r.read().decode()).get("id")
        log(f"uploaded YouTube Short https://youtube.com/shorts/{video_id}")
        return video_id
    except Exception as exc:
        log(f"YouTube upload skipped ({type(exc).__name__}: {str(exc)[:200]})")
        return None
