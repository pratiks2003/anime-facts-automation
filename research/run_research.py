"""Daily trend research (research.yml): collect signals, let the Trend Analyst agent write the brief.

Signals (all free):
- watchlist: public anime pages via Instagram business_discovery -> breakout posts (engagement vs the page's median)
- anilist: trending shows right now
- youtube: trending anime Shorts (only if YT_API_KEY is set; free 10k units/day)
- ours: our recent Reels with metrics
The brief lands in Trend_Brief__c; winning topics go to the Topic_Idea__c backlog.

  python -m research.run_research [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import List, Optional

DEFAULT_WATCHLIST = ["crunchyroll"]


def log(msg: str) -> None:
    print(f"[research] {msg}", file=sys.stderr, flush=True)


def watchlist(sf) -> List[str]:
    names: List[str] = []
    try:
        config = sf.request("GET", "insta/research/brief", base="apexrest") or {}
        names = [str(n).strip().lstrip("@").lower() for n in config.get("watchlist") or [] if str(n).strip()]
    except Exception as exc:
        log(f"watchlist unavailable ({exc}); using defaults")
    return list(dict.fromkeys(names or DEFAULT_WATCHLIST))[:20]


def breakouts(page: dict, days: int = 14) -> dict:
    """Score each recent post vs the page's own median engagement (ratio >= 3 = breaking out)."""
    posts = page.get("posts") or []
    scores = [p["likes"] + 3 * p["comments"] for p in posts]
    median = statistics.median(scores) if scores else 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for p, score in zip(posts, scores):
        try:
            at = datetime.strptime(p["at"], "%Y-%m-%dT%H:%M:%S%z")
        except (TypeError, ValueError):
            at = None
        if at and at < cutoff:
            continue
        out.append({**p, "ratio": round(score / median, 2) if median else None})
    out.sort(key=lambda p: p["ratio"] or 0, reverse=True)
    return {"username": page.get("username"), "followers": page.get("followers"), "median_engagement": median,
            "top_posts": out[:5]}


def watchlist_signals(ig, names: List[str]) -> tuple:
    pages, problems = [], []
    for name in names:
        try:
            pages.append(breakouts(ig.discover(name, 25)))
        except Exception as exc:
            problems.append(f"{name}: {str(exc)[:120]}")
    return pages, problems


def anilist_signals() -> List[dict]:
    from trends.anilist import fetch_trending

    return [{"title": t.title, "trending": t.trending, "popularity": t.popularity, "score": t.average_score,
             "status": t.status, "genres": t.genres[:4]} for t in fetch_trending(limit=15)]


def youtube_signals(api_key: str, opener=urllib.request.urlopen) -> List[dict]:
    after = (datetime.now(timezone.utc) - timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
    q = urllib.parse.urlencode({"part": "snippet", "q": "anime #shorts", "type": "video", "videoDuration": "short",
                                "order": "viewCount", "publishedAfter": after, "maxResults": 25, "key": api_key})
    with opener(f"https://www.googleapis.com/youtube/v3/search?{q}", timeout=30) as resp:
        items = json.loads(resp.read().decode()).get("items", [])
    ids = ",".join(i["id"]["videoId"] for i in items if i.get("id", {}).get("videoId"))
    if not ids:
        return []
    q = urllib.parse.urlencode({"part": "statistics,snippet", "id": ids, "key": api_key})
    with opener(f"https://www.googleapis.com/youtube/v3/videos?{q}", timeout=30) as resp:
        videos = json.loads(resp.read().decode()).get("items", [])
    out = [{"title": v["snippet"]["title"][:140], "channel": v["snippet"].get("channelTitle"),
            "views": int(v.get("statistics", {}).get("viewCount", 0))} for v in videos]
    return sorted(out, key=lambda v: v["views"], reverse=True)[:15]


def our_signals(sf) -> List[dict]:
    rows = sf.query(
        "SELECT Format__c, Anime__r.Name, Hook__c, Style__r.Name, Duration_Seconds__c, "
        "(SELECT Views__c, Avg_Watch_Time_Ms__c, Shares__c, Saves__c, Likes__c FROM Social_Posts__r LIMIT 1) "
        "FROM Anime_Content__c WHERE Status__c = 'Published' ORDER BY CreatedDate DESC LIMIT 10") or []
    out = []
    for r in rows:
        post = ((r.get("Social_Posts__r") or {}).get("records") or [{}])[0]
        out.append({"format": r.get("Format__c"), "anime": (r.get("Anime__r") or {}).get("Name"), "hook": r.get("Hook__c"),
                    "style": (r.get("Style__r") or {}).get("Name"), "seconds": r.get("Duration_Seconds__c"),
                    "views": post.get("Views__c"), "avg_watch_ms": post.get("Avg_Watch_Time_Ms__c"),
                    "shares": post.get("Shares__c"), "saves": post.get("Saves__c"), "likes": post.get("Likes__c")})
    return out


def collect(sf, ig, yt_key: Optional[str]) -> dict:
    signals: dict = {"collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "problems": []}
    if ig is not None:
        signals["watchlist"], problems = watchlist_signals(ig, watchlist(sf))
        signals["problems"] += problems
    try:  # what fans are discussing right now: forum threads, news headlines, Bluesky buzz
        from research.conversation import collect as conversation

        trending = [t["title"] for t in anilist_signals()][:8]
        talk = conversation(trending)
        signals["problems"] += talk.pop("problems", [])
        signals["conversation"] = talk
    except Exception as exc:
        signals["problems"].append(f"conversation: {str(exc)[:160]}")
    for key, fn in (("anilist", anilist_signals), ("ours", lambda: our_signals(sf)),
                    ("youtube", (lambda: youtube_signals(yt_key)) if yt_key else None)):
        if fn is None:
            continue
        try:
            signals[key] = fn()
        except Exception as exc:
            signals["problems"].append(f"{key}: {str(exc)[:160]}")
            signals[key] = []
    return signals


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="collect and print signals; no agent call")
    args = parser.parse_args(argv)
    from pipeline.run_reel import salesforce

    sf = salesforce()
    try:
        from integrations.instagram import client_from_env

        ig = client_from_env()
    except Exception as exc:
        log(f"Instagram unavailable ({exc}); skipping the watchlist")
        ig = None
    signals = collect(sf, ig, os.environ.get("YT_API_KEY"))
    talk = signals.get("conversation") or {}
    log(f"conversation: {len(talk.get('forum', []))} threads, {len(talk.get('news', []))} headlines, "
        f"{len(talk.get('bluesky', []))} shows on Bluesky")
    log(f"signals: {len(signals.get('watchlist', []))} pages, {len(signals.get('anilist', []))} trending shows, "
        f"{len(signals.get('youtube', []))} shorts, {len(signals.get('ours', []))} of ours; problems: {signals['problems']}")
    if args.dry_run:
        print(json.dumps(signals, indent=2, ensure_ascii=False))
        return 0
    result = sf.apex_post("insta/research/brief", signals)
    if not result or result.get("error"):
        log(f"Trend Analyst failed: {result}")
        return 1
    log(f"brief: {str(result.get('summary'))[:400]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
