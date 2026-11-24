"""Trending anime from the AniList GraphQL API (free, no API key).

AniList rate-limits per IP (roughly 30-90 requests/minute depending on load);
one daily call is far below that. We still honour Retry-After on HTTP 429.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Callable, List, Optional

ANILIST_URL = "https://graphql.anilist.co"
USER_AGENT = "anime-facts-automation/0.1"

TRENDING_QUERY = """
query ($perPage: Int) {
  Page(page: 1, perPage: $perPage) {
    media(type: ANIME, sort: TRENDING_DESC, isAdult: false) {
      id
      title { romaji english }
      trending
      popularity
      averageScore
      status
      season
      seasonYear
      genres
      siteUrl
    }
  }
}
"""


class TrendFetchError(RuntimeError):
    pass


@dataclass
class TrendingAnime:
    rank: int
    anilist_id: int
    title: str
    title_romaji: Optional[str]
    trending: int
    popularity: int
    average_score: Optional[int]
    status: Optional[str]
    season: Optional[str]
    season_year: Optional[int]
    genres: List[str] = field(default_factory=list)
    site_url: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def parse_trending(payload: dict) -> List[TrendingAnime]:
    if payload.get("errors"):
        messages = "; ".join(e.get("message", "?") for e in payload["errors"])
        raise TrendFetchError(f"AniList returned errors: {messages}")
    try:
        media = payload["data"]["Page"]["media"]
    except (KeyError, TypeError) as exc:
        raise TrendFetchError(f"unexpected AniList response shape: {exc}") from exc

    items = []
    for rank, m in enumerate(media, start=1):
        titles = m.get("title") or {}
        items.append(
            TrendingAnime(
                rank=rank,
                anilist_id=int(m["id"]),
                title=titles.get("english") or titles.get("romaji") or f"AniList {m['id']}",
                title_romaji=titles.get("romaji"),
                trending=int(m.get("trending") or 0),
                popularity=int(m.get("popularity") or 0),
                average_score=m.get("averageScore"),
                status=m.get("status"),
                season=m.get("season"),
                season_year=m.get("seasonYear"),
                genres=list(m.get("genres") or []),
                site_url=m.get("siteUrl"),
            )
        )
    return items


def fetch_trending(
    limit: int = 20,
    timeout: float = 20.0,
    max_attempts: int = 3,
    opener: Callable = urllib.request.urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> List[TrendingAnime]:
    """Fetch the current top-`limit` trending anime (limit capped at 50 by AniList)."""
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")

    body = json.dumps({"query": TRENDING_QUERY, "variables": {"perPage": limit}}).encode()
    request = urllib.request.Request(
        ANILIST_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )

    last_error: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            with opener(request, timeout=timeout) as response:
                return parse_trending(json.loads(response.read().decode("utf-8")))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 429 or 500 <= exc.code < 600:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                wait = float(retry_after) if retry_after and retry_after.isdigit() else 2.0 ** attempt
                if attempt < max_attempts:
                    sleep(min(wait, 60.0))
                    continue
            break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < max_attempts:
                sleep(2.0 ** attempt)
                continue
    raise TrendFetchError(f"could not fetch AniList trending after {max_attempts} attempt(s): {last_error}")
