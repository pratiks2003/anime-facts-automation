"""Compare every trend source per show and decide what is REALLY hot.

Signals per show (all free, no keys):
  wikipedia  - yesterday's page views vs the show's own 7-day normal (spike) and volume
  anilist    - trending rank / popularity (how big the show is)
  bluesky    - fan post buzz (likes + reposts)
  forum      - fresh AniList discussion threads
  news       - Anime News Network + Crunchyroll News headlines mentioning it
  watchlist  - breakout posts on watched Instagram pages mentioning it
  ours       - how many verified facts we have (rich Reels need material)

A show is "hot" only when at least 2 independent sources agree. Big shows and shows we know well
get extra weight, so day-to-day picks favour the main franchises fans follow.
"""

from __future__ import annotations

import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

UA = {"User-Agent": "senpai-secrets-research/0.2 (anime trend research; personal)"}

# Big franchises our audience follows: always measured, even when AniList trending moves on.
MAIN_SHOWS = ["One Piece", "Naruto", "Boruto: Naruto Next Generations", "Jujutsu Kaisen", "Demon Slayer: Kimetsu no Yaiba",
              "Chainsaw Man", "Attack on Titan", "My Hero Academia", "Dragon Ball", "Bleach", "Solo Leveling",
              "Frieren: Beyond Journey's End", "Dandadan", "Spy × Family", "Hunter × Hunter", "Black Clover",
              "One-Punch Man", "Kaiju No. 8", "Blue Lock", "Sakamoto Days", "Oshi no Ko", "Tokyo Revengers"]


def log(msg: str) -> None:
    print(f"[fusion] {msg}", file=sys.stderr, flush=True)


def _json(url: str, opener: Callable, sleep=time.sleep, attempts: int = 4) -> dict:
    """GET JSON politely: Wikimedia throttles bursts (429), so pause and back off."""
    import urllib.error

    for attempt in range(attempts):
        sleep(0.6 + 2.5 * attempt)
        try:
            with opener(urllib.request.Request(url, headers=UA), timeout=25) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == attempts - 1:
                raise
    raise RuntimeError("unreachable")


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def key_words(name: str) -> List[str]:
    stop = {"the", "season", "part", "and", "no", "of", "a", "an", "to", "in", "wa", "ga", "beyond", "journey", "s"}
    return [w for w in norm(re.sub(r"\(.*?\)|:.*$", "", name)).split() if w not in stop and len(w) > 1][:3]


def mentions(text: str, name: str) -> bool:
    words = key_words(name)
    t = norm(text)
    return bool(words) and all(w in t.split() or w in t for w in words)


# Known article titles (no search call needed; Wikipedia's search API rate-limits bursts)
WIKI_TITLES = {"one piece": "One Piece", "naruto": "Naruto", "boruto naruto next generations": "Boruto: Naruto Next Generations",
               "jujutsu kaisen": "Jujutsu Kaisen", "demon slayer kimetsu no yaiba": "Demon Slayer: Kimetsu no Yaiba",
               "chainsaw man": "Chainsaw Man", "attack on titan": "Attack on Titan", "my hero academia": "My Hero Academia",
               "dragon ball": "Dragon Ball", "bleach": "Bleach (manga)", "solo leveling": "Solo Leveling",
               "frieren beyond journey s end": "Frieren", "dandadan": "Dandadan", "spy family": "Spy × Family",
               "hunter hunter": "Hunter × Hunter", "black clover": "Black Clover", "one punch man": "One-Punch Man",
               "kaiju no 8": "Kaiju No. 8", "blue lock": "Blue Lock", "sakamoto days": "Sakamoto Days",
               "oshi no ko": "Oshi no Ko", "tokyo revengers": "Tokyo Revengers"}
_searches = {"left": 8}


def wiki_title(name: str, opener: Callable, sleep=time.sleep) -> Optional[str]:
    known = WIKI_TITLES.get(norm(name).replace(" x ", " ").replace("  ", " "))
    if known:
        return known
    if _searches["left"] <= 0:
        return None  # stay well under Wikipedia's search rate limit
    _searches["left"] -= 1
    sleep(1.2)
    q = urllib.parse.urlencode({"action": "opensearch", "search": name, "limit": 3, "namespace": 0, "format": "json"})
    try:
        titles = _json(f"https://en.wikipedia.org/w/api.php?{q}", opener)[1]
    except Exception:
        return None
    return titles[0] if titles else None


def wikipedia_interest(name: str, opener: Callable = urllib.request.urlopen) -> Optional[dict]:
    """{views (yesterday), normal (7-day median before), spike (views/normal)}."""
    title = wiki_title(name, opener)
    if not title:
        return None
    end = datetime.now(timezone.utc) - timedelta(days=1)
    start = end - timedelta(days=8)
    url = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
           f"{urllib.parse.quote(title.replace(' ', '_'), safe='')}/daily/{start:%Y%m%d}/{end:%Y%m%d}")
    try:
        views = [i["views"] for i in _json(url, opener)["items"]]
    except Exception:
        return None
    if len(views) < 3:
        return None
    before = sorted(views[:-1])
    normal = before[len(before) // 2] or 1
    return {"article": title, "views": views[-1], "normal": normal, "spike": round(views[-1] / normal, 2)}


def fuse(signals: dict, knowledge_counts: Dict[str, int], opener: Callable = urllib.request.urlopen,
         extra_shows: List[str] = ()) -> List[dict]:
    """Ranked shows with per-source evidence. `signals` is the research signal dict."""
    talk = signals.get("conversation") or {}
    anilist = signals.get("anilist") or []
    shows: Dict[str, dict] = {}

    def show(name: str) -> dict:
        key = norm(name)
        for k, v in shows.items():  # merge "ONE PIECE" / "One Piece"
            if k == key or (key_words(name) and key_words(name) == key_words(v["anime"])):
                return v
        shows[key] = {"anime": name, "sources": {}, "evidence": []}
        return shows[key]

    for rank, a in enumerate(anilist[:15]):
        s = show(a["title"])
        s["popularity"] = a.get("popularity") or 0
        s["sources"]["anilist"] = round(1.0 - rank / 15, 2)
    for name in [*MAIN_SHOWS, *extra_shows]:
        show(name)
    for b in talk.get("bluesky") or []:
        if b.get("buzz"):
            s = show(b["anime"])
            s["sources"]["bluesky"] = round(min(1.0, math.log10(1 + b["buzz"]) / 4), 2)
            s["evidence"] += [f"Bluesky: {t}" for t in b.get("top_posts", [])[:1]]
    news = (talk.get("news") or []) + (talk.get("crunchyroll_news") or [])
    threads = talk.get("forum") or []
    breakouts = [p for page in signals.get("watchlist") or [] for p in page.get("top_posts", []) if (p.get("ratio") or 0) >= 2]
    for s in list(shows.values()):
        name = s["anime"]
        hits = [n for n in news if mentions(n["title"] + " " + n.get("summary", ""), name)]
        if hits:
            s["sources"]["news"] = min(1.0, 0.5 + 0.25 * len(hits))
            s["evidence"] += [f"News: {n['title']}" for n in hits[:2]]
            s["headline"] = {"title": hits[0]["title"], "url": hits[0].get("url"), "summary": hits[0].get("summary", "")}
        th = [t for t in threads if any(mentions(a, name) for a in t.get("anime") or []) or mentions(t["title"], name)]
        if th:
            s["sources"]["forum"] = min(1.0, 0.3 + sum(t["replies"] for t in th) / 60)
            s["evidence"] += [f"Forum: {t['title']} ({t['replies']} replies)" for t in th[:1]]
        bo = [p for p in breakouts if mentions(p.get("caption", ""), name)]
        if bo:
            s["sources"]["watchlist"] = min(1.0, 0.4 + 0.1 * max(p["ratio"] for p in bo))
            s["evidence"].append(f"Breakout post ({max(p['ratio'] for p in bo)}x): {bo[0]['caption'][:80]}")
    # Wikipedia only for shows with at least one other signal or a main show (keeps calls bounded)
    for s in shows.values():
        if not s["sources"] and s["anime"] not in MAIN_SHOWS:
            continue
        w = wikipedia_interest(s["anime"], opener)
        if w:
            s["wikipedia"] = w
            if w["spike"] >= 1.3 or w["views"] >= 3000:
                s["sources"]["wikipedia"] = round(min(1.0, max(w["spike"] - 1, 0) + math.log10(1 + w["views"]) / 10), 2)
                s["evidence"].append(f"Wikipedia: {w['views']} views yesterday ({w['spike']}x normal)")
    ranked = []
    for s in shows.values():
        facts = max((c for k, c in knowledge_counts.items() if key_words(k) and key_words(k) == key_words(s["anime"])),
                    default=0)
        agree = len(s["sources"])
        main = any(key_words(m) == key_words(s["anime"]) for m in MAIN_SHOWS) or (s.get("popularity") or 0) >= 300_000
        heat = sum(s["sources"].values())
        score = heat * (1 + 0.15 * agree) * (1.35 if main else 1.0) * (1.2 if facts >= 6 else 1.0)
        ranked.append({"anime": s["anime"], "score": round(score, 2), "sources_agreeing": agree,
                       "sources": s["sources"], "main_show": main, "verified_facts": facts,
                       "hot": agree >= 2, "headline": s.get("headline"), "evidence": s["evidence"][:4],
                       "wikipedia": s.get("wikipedia")})
    ranked = [r for r in ranked if r["sources_agreeing"]]
    ranked.sort(key=lambda r: -r["score"])
    log("top: " + "; ".join(f"{r['anime']} {r['score']} ({r['sources_agreeing']} sources)" for r in ranked[:6]))
    return ranked[:20]
