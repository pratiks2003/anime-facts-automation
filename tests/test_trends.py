import io
import json
import unittest
import urllib.error
from pathlib import Path

from rag.knowledge import load_knowledge, load_metadata
from trends.anilist import TrendFetchError, fetch_trending, parse_trending
from trends.coverage import annotate_coverage

FIXTURES = Path(__file__).parent / "fixtures" / "knowledge"

PAYLOAD = {
    "data": {
        "Page": {
            "media": [
                {"id": 999001, "title": {"romaji": "Shin Anime", "english": None}, "trending": 400,
                 "popularity": 1000, "averageScore": 70, "status": "RELEASING", "season": "FALL",
                 "seasonYear": 2026, "genres": ["Action"], "siteUrl": "https://anilist.co/anime/999001"},
                {"id": 21, "title": {"romaji": "ONE PIECE", "english": "ONE PIECE"}, "trending": 190,
                 "popularity": 770000, "averageScore": 87, "status": "RELEASING", "season": "FALL",
                 "seasonYear": 1999, "genres": ["Action", "Adventure"], "siteUrl": "https://anilist.co/anime/21"},
                {"id": 555, "title": {"romaji": "Naruto Shippuden", "english": None}, "trending": 50,
                 "popularity": 5, "averageScore": None, "status": "FINISHED", "season": None,
                 "seasonYear": None, "genres": [], "siteUrl": None},
            ]
        }
    }
}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ParseTests(unittest.TestCase):
    def test_parse_ranks_and_titles(self):
        items = parse_trending(PAYLOAD)
        self.assertEqual([i.rank for i in items], [1, 2, 3])
        self.assertEqual(items[0].title, "Shin Anime")  # falls back to romaji
        self.assertEqual(items[1].anilist_id, 21)

    def test_graphql_errors_raise(self):
        with self.assertRaises(TrendFetchError):
            parse_trending({"errors": [{"message": "boom"}], "data": None})

    def test_bad_shape_raises(self):
        with self.assertRaises(TrendFetchError):
            parse_trending({"data": {}})


class FetchTests(unittest.TestCase):
    def test_retries_after_429_then_succeeds(self):
        calls, sleeps = [], []

        def opener(request, timeout):
            calls.append(request)
            if len(calls) == 1:
                raise urllib.error.HTTPError(request.full_url, 429, "Too Many", {"Retry-After": "3"}, None)
            return FakeResponse(json.dumps(PAYLOAD).encode())

        items = fetch_trending(limit=3, opener=opener, sleep=sleeps.append)
        self.assertEqual(len(items), 3)
        self.assertEqual(sleeps, [3.0])
        body = json.loads(calls[0].data)
        self.assertEqual(body["variables"], {"perPage": 3})

    def test_client_error_not_retried(self):
        calls = []

        def opener(request, timeout):
            calls.append(1)
            raise urllib.error.HTTPError(request.full_url, 400, "Bad", {}, None)

        with self.assertRaises(TrendFetchError):
            fetch_trending(opener=opener, sleep=lambda s: None)
        self.assertEqual(len(calls), 1)

    def test_limit_validated(self):
        with self.assertRaises(ValueError):
            fetch_trending(limit=0)


class CoverageTests(unittest.TestCase):
    def test_matches_by_id_and_alias(self):
        rows = annotate_coverage(parse_trending(PAYLOAD), load_metadata(FIXTURES), load_knowledge(FIXTURES))
        by_title = {r["title"]: r for r in rows}
        self.assertFalse(by_title["Shin Anime"]["has_knowledge"])
        self.assertIsNone(by_title["Shin Anime"]["knowledge_anime"])
        self.assertEqual(by_title["ONE PIECE"]["knowledge_anime"], "One Piece")  # by AniList id
        self.assertEqual(by_title["ONE PIECE"]["verified_records"], 3)
        self.assertEqual(by_title["Naruto Shippuden"]["knowledge_anime"], "Naruto")  # by alias
        self.assertTrue(by_title["Naruto Shippuden"]["has_knowledge"])


if __name__ == "__main__":
    unittest.main()
