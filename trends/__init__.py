"""Trending-topic discovery (AniList now; Wikipedia pageviews and news feeds in Phase 2)."""

from .anilist import TrendFetchError, TrendingAnime, fetch_trending, parse_trending
from .coverage import annotate_coverage

__all__ = ["TrendFetchError", "TrendingAnime", "annotate_coverage", "fetch_trending", "parse_trending"]
