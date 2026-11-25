"""What anime fans are talking about RIGHT NOW (free, no keys):

- AniList forum: threads with the most recent replies (episode discussions, announcements)
- Anime News Network: latest headlines (last ~24 h)
- Bluesky: top fan posts per trending show (likes + reposts = buzz)

Reddit would be ideal but blocks anonymous access; these three are reachable from GitHub runners.
"""

from __future__ import annotations

import html
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Dict, List

UA = {"User-Agent": "Mozilla/5.0 (compatible; senpai-secrets-research/0.2)"}


def log(msg: str) -> None:
    print(f"[conversation] {msg}", file=sys.stderr, flush=True)


def _get(url: str, opener: Callable, data: bytes = None, headers: dict = None) -> bytes:
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with opener(req, timeout=30) as resp:
        return resp.read()


THREADS = """query { Page(perPage: 80) { threads(sort: [REPLIED_AT_DESC]) {
  title replyCount viewCount createdAt repliedAt mediaCategories { title { english romaji } } } } }"""


def anilist_threads(opener: Callable = urllib.request.urlopen, hours: int = 72) -> List[dict]:
    """Most-discussed recent AniList forum threads (heat = replies, views)."""
    raw = _get("https://graphql.anilist.co", opener, json.dumps({"query": THREADS}).encode(),
               {"Content-Type": "application/json", "Accept": "application/json"})
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp()
    out = []
    for t in json.loads(raw)["data"]["Page"]["threads"]:
        media = [(m["title"].get("english") or m["title"].get("romaji")) for m in t.get("mediaCategories") or []]
        about_show = bool(media) or re.search(r"episode|season|announc|trailer|movie|chapter", t["title"], re.I)
        if (t.get("createdAt") or 0) < since or not about_show:
            continue  # only fresh threads about a show (skip forum games, intros, clubs)
        out.append({"title": t["title"][:140], "replies": t.get("replyCount") or 0, "views": t.get("viewCount") or 0,
                    "anime": media[:2]})
    return sorted(out, key=lambda t: -(t["replies"] * 5 + t["views"] / 50))[:15]


def ann_news(opener: Callable = urllib.request.urlopen, hours: int = 30) -> List[dict]:
    """Latest Anime News Network headlines."""
    xml = _get("https://www.animenewsnetwork.com/all/rss.xml?ann-edition=w", opener).decode("utf-8", "replace")
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    out = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S):
        def tag(name: str) -> str:
            m = re.search(rf"<{name}>(.*?)</{name}>", item, re.S)
            return html.unescape(re.sub(r"<!\[CDATA\[|\]\]>|<[^>]+>", "", m.group(1))).strip() if m else ""
        try:
            when = parsedate_to_datetime(tag("pubDate"))
        except (TypeError, ValueError):
            continue
        if when < since:
            continue
        category = tag("category").lower()
        title = tag("title")
        if category in ("review", "interest", "column", "feature") or re.search(r"\breview\b", title, re.I):
            continue  # news only
        out.append({"title": tag("title")[:160], "summary": tag("description")[:300], "url": tag("link"),
                    "published": when.isoformat(timespec="minutes"), "category": category})
    return out[:20]


def bluesky_buzz(shows: List[str], opener: Callable = urllib.request.urlopen, hours: int = 24) -> List[dict]:
    """Per show: likes + reposts of its top posts in the last day, plus the top post texts."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = []
    for show in shows[:8]:
        q = urllib.parse.urlencode({"q": show, "sort": "top", "limit": 25, "since": since, "lang": "en"})
        try:
            posts = json.loads(_get(f"https://api.bsky.app/xrpc/app.bsky.feed.searchPosts?{q}", opener)).get("posts", [])
        except Exception as exc:
            log(f"bluesky '{show}' failed ({str(exc)[:80]})")
            continue
        buzz = sum((p.get("likeCount") or 0) + 2 * (p.get("repostCount") or 0) for p in posts)
        top = sorted(posts, key=lambda p: -((p.get("likeCount") or 0) + 2 * (p.get("repostCount") or 0)))[:3]
        out.append({"anime": show, "buzz": buzz, "posts": len(posts),
                    "top_posts": [re.sub(r"\s+", " ", p.get("record", {}).get("text", ""))[:160] for p in top]})
    return sorted(out, key=lambda x: -x["buzz"])


def collect(trending_titles: List[str], opener: Callable = urllib.request.urlopen) -> Dict[str, object]:
    signals: Dict[str, object] = {"problems": []}
    for key, fn in (("forum", lambda: anilist_threads(opener)), ("news", lambda: ann_news(opener)),
                    ("bluesky", lambda: bluesky_buzz(trending_titles, opener))):
        try:
            signals[key] = fn()
        except Exception as exc:
            signals[key] = []
            signals["problems"].append(f"{key}: {str(exc)[:120]}")
    return signals
