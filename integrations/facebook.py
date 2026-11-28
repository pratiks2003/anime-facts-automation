"""Facebook Reels cross-posting (Meta Graph API, Page "video_reels" flow). Off unless FB_PAGE_ID is set.

Flow (Meta docs "Reels Publishing API"):
  1. POST /{page-id}/video_reels upload_phase=start             -> video_id
  2. POST rupload.facebook.com/video-upload/{ver}/{video_id}    header file_url: <public video URL>
  3. POST /{page-id}/video_reels upload_phase=finish video_state=PUBLISHED description=...
Needs a Page access token: derived from the system-user token (GET /{page-id}?fields=access_token), which requires
the permissions pages_show_list, pages_read_engagement and pages_manage_posts on the token.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from typing import Optional

GRAPH = "https://graph.facebook.com"


def log(msg: str) -> None:
    print(f"[facebook] {msg}", file=sys.stderr, flush=True)


def _call(method: str, url: str, params: Optional[dict] = None, headers: Optional[dict] = None, opener=urllib.request.urlopen) -> dict:
    data = urllib.parse.urlencode(params or {}).encode() if method == "POST" else None
    if method == "GET" and params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with opener(req, timeout=120) as r:
        return json.loads(r.read().decode() or "{}")


def cross_post_reel(video_url: str, description: str, opener=urllib.request.urlopen) -> Optional[str]:
    """Publish the same Reel on the Facebook Page. Returns the video id, or None (off / failed - never raises)."""
    page = os.environ.get("FB_PAGE_ID", "").strip()
    token = os.environ.get("IG_ACCESS_TOKEN", "").strip()
    version = os.environ.get("IG_API_VERSION") or "v27.0"
    if not page or not token or not video_url:
        return None
    try:
        page_token = _call("GET", f"{GRAPH}/{version}/{page}", {"fields": "access_token", "access_token": token}, opener=opener)["access_token"]
        start = _call("POST", f"{GRAPH}/{version}/{page}/video_reels", {"upload_phase": "start", "access_token": page_token}, opener=opener)
        video_id = start["video_id"]
        _call("POST", f"https://rupload.facebook.com/video-upload/{version}/{video_id}",
              headers={"Authorization": f"OAuth {page_token}", "file_url": video_url}, opener=opener)
        _call("POST", f"{GRAPH}/{version}/{page}/video_reels", {"upload_phase": "finish", "video_id": video_id,
              "video_state": "PUBLISHED", "description": description[:2000], "access_token": page_token}, opener=opener)
        log(f"cross-posted to Facebook Reels ({video_id})")
        return str(video_id)
    except Exception as exc:
        log(f"Facebook cross-post skipped ({type(exc).__name__}: {str(exc)[:200]})")
        return None
