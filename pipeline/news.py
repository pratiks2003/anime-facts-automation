"""Live anime news/hype facts from AniList (free GraphQL API, no key).

Facts produced here are structured database values (air dates, scores, popularity,
studios), so they are treated as verified with source_type "database" and the
AniList page as source. They are passed to the renderer as extra records and are
not written to the knowledge base (they go stale).
"""

from __future__ import annotations

import html
import json
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

ANILIST = "https://graphql.anilist.co"
IST = timezone(timedelta(hours=5, minutes=30))

MEDIA_FIELDS = """
  id title { romaji english } siteUrl popularity averageScore trending status episodes
  season seasonYear format genres description(asHtml: false)
  startDate { year month day }
  studios(isMain: true) { nodes { name } }
  nextAiringEpisode { episode airingAt }
  characters(sort: [ROLE, FAVOURITES_DESC], perPage: 4) { nodes { name { full } } }
"""

QUERIES = {
    "airing": """query ($from: Int, $to: Int) { Page(perPage: 50) {
        airingSchedules(airingAt_greater: $from, airingAt_lesser: $to, sort: TIME) {
          episode airingAt media { %s isAdult } } } }""" % MEDIA_FIELDS,
    "trending": """query ($status: MediaStatus) { Page(perPage: 8) {
        media(type: ANIME, sort: TRENDING_DESC, isAdult: false, status: $status) { %s } } }""" % MEDIA_FIELDS,
    "season": """query ($season: MediaSeason, $year: Int) { Page(perPage: 8) {
        media(type: ANIME, season: $season, seasonYear: $year, sort: POPULARITY_DESC, isAdult: false) { %s } } }""" % MEDIA_FIELDS,
    "upcoming": """query { Page(perPage: 8) {
        media(type: ANIME, status: NOT_YET_RELEASED, sort: TRENDING_DESC, isAdult: false) { %s } } }""" % MEDIA_FIELDS,
}


def _post(query: str, variables: dict, opener: Callable, attempts: int = 3) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(ANILIST, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json",
                                          "User-Agent": "anime-facts-automation/0.1"})
    for attempt in range(1, attempts + 1):
        try:
            with opener(req, timeout=40) as resp:
                data = json.loads(resp.read().decode())
            if data.get("errors"):
                raise RuntimeError("AniList: " + "; ".join(e.get("message", "?") for e in data["errors"]))
            return data["data"]
        except Exception:
            if attempt == attempts:
                raise
            time.sleep(4 * attempt)
    raise RuntimeError("unreachable")


def title_of(media: dict) -> str:
    t = media.get("title") or {}
    return t.get("english") or t.get("romaji") or f"AniList {media.get('id')}"


def clean_description(text: Optional[str], limit: int = 400) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = re.sub(r"\(Source:[^)]*\)", "", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def ist_day(ts: int) -> str:
    return datetime.fromtimestamp(ts, IST).strftime("%A %d %b, %H:%M IST")


def _record(n: int, media: dict, title: str, content: str, category: str = "news") -> dict:
    return {
        "id": f"NEWS-{n:03d}",
        "anime": title_of(media),
        "title": title,
        "content": content,
        "characters": [c["name"]["full"] for c in ((media.get("characters") or {}).get("nodes") or [])][:4],
        "category": category,
        "canon_status": "canon",
        "spoiler_level": "none",
        "source_url": media.get("siteUrl") or f"https://anilist.co/anime/{media.get('id')}",
        "source_type": "database",
        "license": None,
        "verified": True,
        "last_reviewed": datetime.now(IST).date().isoformat(),
        "tags": ["news"],
    }


def media_facts(media: dict, start: int) -> List[dict]:
    """A handful of verifiable facts about one show."""
    title = title_of(media)
    facts: List[dict] = []
    studio = ", ".join(s["name"] for s in ((media.get("studios") or {}).get("nodes") or [])) or None
    sd = media.get("startDate") or {}
    if studio:
        facts.append(_record(start + len(facts), media, f"{title} studio", f"{title} is animated by {studio}."))
    if media.get("averageScore"):
        facts.append(_record(start + len(facts), media, f"{title} score",
                             f"{title} has an average AniList score of {media['averageScore']}/100 from fans."))
    if media.get("popularity"):
        facts.append(_record(start + len(facts), media, f"{title} popularity",
                             f"Over {media['popularity']:,} AniList users have {title} on their lists."))
    if media.get("genres"):
        facts.append(_record(start + len(facts), media, f"{title} genres", f"{title} is listed as {', '.join(media['genres'][:4])}."))
    nxt = media.get("nextAiringEpisode")
    if nxt:
        facts.append(_record(start + len(facts), media, f"{title} next episode",
                             f"Episode {nxt['episode']} of {title} airs {ist_day(nxt['airingAt'])}."))
    if sd.get("year") and media.get("status") == "NOT_YET_RELEASED":
        when = "-".join(str(x) for x in (sd.get("year"), sd.get("month"), sd.get("day")) if x)
        facts.append(_record(start + len(facts), media, f"{title} premiere", f"{title} is scheduled to premiere {when}."))
    desc = clean_description(media.get("description"))
    if desc:
        facts.append(_record(start + len(facts), media, f"{title} premise", f"Premise of {title}: {desc}", "plot_event"))
    return facts


def next_season(now: datetime):
    order = ["WINTER", "SPRING", "SUMMER", "FALL"]
    current = order[(now.month - 1) // 3 if now.month != 12 else 0]
    if now.month == 12:
        return "WINTER", now.year + 1
    idx = order.index(current)
    return (order[idx + 1], now.year) if idx < 3 else ("WINTER", now.year + 1)


def news_facts(format_name: str, opener: Callable = urllib.request.urlopen,
               now: Optional[datetime] = None) -> Dict[str, object]:
    """Return {"headline_context": str, "facts": [...], "shows": [titles]} for a news format."""
    now = now or datetime.now(timezone.utc)
    shows: List[dict] = []
    if format_name == "This Week's Drops":
        data = _post(QUERIES["airing"], {"from": int(now.timestamp()), "to": int((now + timedelta(days=7)).timestamp())}, opener)
        seen, picks = set(), []
        for s in sorted(data["Page"]["airingSchedules"], key=lambda s: -(s["media"].get("popularity") or 0)):
            m = s["media"]
            if m.get("isAdult") or m["id"] in seen:
                continue
            seen.add(m["id"])
            m["nextAiringEpisode"] = {"episode": s["episode"], "airingAt": s["airingAt"]}
            picks.append(m)
        shows = picks[:5]
        context = "The most popular anime episodes airing in the next 7 days."
    elif format_name in ("Why It's Trending", "Hype Check"):
        status = "RELEASING" if format_name == "Hype Check" else None
        shows = _post(QUERIES["trending"], {"status": status} if status else {}, opener)["Page"]["media"][:3]
        context = "The anime trending hardest on AniList right now."
    elif format_name == "Season Preview":
        season, year = next_season(now)
        shows = _post(QUERIES["season"], {"season": season, "year": year}, opener)["Page"]["media"][:5]
        context = f"The most anticipated anime of {season.title()} {year}."
    elif format_name == "Just Announced":
        shows = _post(QUERIES["upcoming"], {}, opener)["Page"]["media"][:4]
        context = "Upcoming anime generating the most buzz before release."
    else:
        raise ValueError(f"{format_name} is not a news format")

    facts: List[dict] = []
    for media in shows:
        facts += media_facts(media, len(facts) + 1)
    return {"headline_context": context, "facts": facts, "shows": [title_of(m) for m in shows],
            "media": shows}


SEARCH_QUERY = """query ($search: String) { Media(search: $search, type: ANIME, isAdult: false) {
    id title { romaji english } synonyms siteUrl popularity
    characters(sort: [FAVOURITES_DESC], perPage: 10) { nodes { name { full alternative } } } } }"""

TRENDING_POPULAR = """query { Page(perPage: 15) {
    media(type: ANIME, sort: TRENDING_DESC, isAdult: false, popularity_greater: 150000) {
      id title { romaji english } synonyms popularity
      characters(sort: [FAVOURITES_DESC], perPage: 10) { nodes { name { full alternative } } } } } }"""


def search_media(name: str, opener: Callable = urllib.request.urlopen) -> Optional[dict]:
    try:
        return _post(SEARCH_QUERY, {"search": name}, opener).get("Media")
    except Exception:
        return None


def trending_popular(opener: Callable = urllib.request.urlopen) -> List[dict]:
    """Trending shows that are popular enough to have rich Wikipedia coverage."""
    return _post(TRENDING_POPULAR, {}, opener)["Page"]["media"]


def character_titles(node: dict) -> List[str]:
    """Wikipedia title candidates for an AniList character: given-family order, family-given, alternatives."""
    name = node.get("name") or {}
    full = (name.get("full") or "").strip()
    out = [full]
    parts = full.split()
    if len(parts) == 2:
        out.append(f"{parts[1]} {parts[0]}")
    out += [a for a in (name.get("alternative") or []) if a]
    return [t for t in dict.fromkeys(out) if t]
