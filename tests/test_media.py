import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

from video.media import (MediaAsset, Openverse, build_blocklist, credits_text, find_image, find_music,
                         license_label, mentions_blocked)


def jpeg_bytes(w=1200, h=800):
    buf = BytesIO()
    Image.new("RGB", (w, h), (10, 120, 200)).save(buf, "JPEG")
    return buf.getvalue()


class FakeOpenverse(Openverse):
    def __init__(self, results, files=None):
        self.results, self.files, self.searches = results, files or {}, []

    def search(self, kind, query, page_size=20, **filters):
        self.searches.append((kind, query, filters))
        return self.results.get(query, [])

    def download(self, url):
        if url not in self.files:
            raise OSError("404")
        return self.files[url]


def result(i, title, w=1200, h=800, lic="by", ver="2.0", tags=(), **extra):
    return {"id": f"id{i}", "title": title, "width": w, "height": h, "license": lic, "license_version": ver,
            "creator": f"creator{i}", "url": f"https://img/{i}.jpg", "foreign_landing_url": f"https://flickr/{i}",
            "provider": "flickr", "tags": [{"name": t} for t in tags], **extra}


BLOCK = build_blocklist(["One Piece", "straw hat pirates", "devil fruit"], ["Monkey D. Luffy"])


class BlocklistTests(unittest.TestCase):
    def test_phrases_not_split_but_names_are(self):
        self.assertIsNone(mentions_blocked("fresh fruit bowl", BLOCK))
        self.assertIsNone(mentions_blocked("straw hat on a beach", BLOCK))
        self.assertEqual(mentions_blocked("Devil Fruit cosplay", BLOCK) in ("devil fruit", "cosplay"), True)
        self.assertEqual(mentions_blocked("luffy figure", BLOCK) in ("luffy", "figure"), True)
        self.assertEqual(mentions_blocked("episode 5 screenshot", BLOCK) in ("episode", "screenshot"), True)

    def test_word_boundaries(self):
        self.assertIsNone(mentions_blocked("animated clouds", BLOCK))  # "anime" inside "animated"


class ImageTests(unittest.TestCase):
    def test_skips_blocked_small_and_broken_results(self):
        results = {"ocean": [
            result(1, "Luffy statue"),                       # blocked title
            result(2, "Waves", tags=["screenshot"]),         # blocked tag
            result(3, "Tiny waves", w=400, h=300),           # too small
            result(4, "Broken link"),                        # download fails
            result(5, "Stormy sea", lic="cc0", ver=None),    # good
        ]}
        client = FakeOpenverse(results, {"https://img/5.jpg": jpeg_bytes()})
        with tempfile.TemporaryDirectory() as tmp:
            asset = find_image(client, ["ocean"], Path(tmp), BLOCK, set())
            self.assertEqual(asset.openverse_id, "id5")
            self.assertEqual(asset.license, "CC0")
            self.assertTrue(Path(asset.path).exists())
        self.assertEqual(client.searches[0][0], "images")

    def test_excludes_already_used_and_falls_back_to_next_query(self):
        results = {"a": [result(1, "Sea")], "b": [result(2, "Ship")]}
        client = FakeOpenverse(results, {"https://img/1.jpg": jpeg_bytes(), "https://img/2.jpg": jpeg_bytes()})
        with tempfile.TemporaryDirectory() as tmp:
            asset = find_image(client, ["a", "b"], Path(tmp), BLOCK, {"id1"})
        self.assertEqual(asset.openverse_id, "id2")

    def test_query_with_anime_name_rejected(self):
        with self.assertRaises(ValueError):
            find_image(FakeOpenverse({}), ["one piece luffy"], Path("."), BLOCK, set())

    def test_none_when_nothing_suitable(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(find_image(FakeOpenverse({"x": []}), ["x"], Path(tmp), BLOCK, set()))


class MusicTests(unittest.TestCase):
    def test_picks_long_enough_track_deterministically(self):
        tracks = [result(1, "Short", duration=20000, filetype="mp3"),
                  result(2, "Epic A", duration=120000, filetype="mp3"),
                  result(3, "Epic B", duration=150000, filetype="mp32")]
        results = {q: tracks for q in ("epic cinematic", "epic orchestral", "action trailer")}
        files = {"https://img/2.jpg": b"x" * 200_000, "https://img/3.jpg": b"x" * 200_000}
        with tempfile.TemporaryDirectory() as tmp:
            a = find_music(FakeOpenverse(results, files), "hype", "seed-1", Path(tmp))
            b = find_music(FakeOpenverse(results, files), "hype", "seed-1", Path(tmp))
        self.assertIn(a.title, ("Epic A", "Epic B"))
        self.assertEqual(a.openverse_id, b.openverse_id)


class CreditTests(unittest.TestCase):
    def test_license_labels(self):
        self.assertEqual(license_label("by", "2.0"), "CC BY 2.0")
        self.assertEqual(license_label("cc0", "1.0"), "CC0")
        self.assertEqual(license_label("pdm", None), "Public Domain")

    def test_credits_text_truncates_titles(self):
        img = MediaAsset("image", "p", "A" * 80, "Ann", "CC BY 2.0", "u", "flickr", "1")
        mus = MediaAsset("audio", "p", "Song", "Bob", "CC BY 3.0", "u", "jamendo", "2")
        text = credits_text([img], mus)
        self.assertIn("…", text)
        self.assertIn('Music: "Song" by Bob (CC BY 3.0, jamendo)', text)


if __name__ == "__main__":
    unittest.main()


class MusicSupervisorTests(unittest.TestCase):
    def test_picker_choice_wins_and_bad_picker_falls_back(self):
        tracks = [result(i, f"Track {i}", duration=120000, filetype="mp3") for i in range(1, 5)]
        results = {q: tracks for q in ("epic cinematic", "epic orchestral", "action trailer")}
        files = {f"https://img/{i}.jpg": b"x" * 200_000 for i in range(1, 5)}
        seen = {}
        with tempfile.TemporaryDirectory() as tmp:
            chosen = find_music(FakeOpenverse(results, files), "hype", "seed", Path(tmp),
                                picker=lambda c: (seen.setdefault("n", len(c)), "id3")[1])
            self.assertEqual(chosen.openverse_id, "id3")
            self.assertEqual(seen["n"], 4)  # de-duplicated candidates
            boom = find_music(FakeOpenverse(results, files), "hype", "seed", Path(tmp), picker=lambda c: 1 / 0)
            self.assertIsNotNone(boom)


class MusicTrainingTests(unittest.TestCase):
    def test_none_means_no_music_and_banned_genres_filtered(self):
        tracks = [result(1, "Trance Etude 2", duration=120000, filetype="mp3"),
                  result(2, "Taiko Storm", duration=120000, filetype="mp3", tags=["epic", "drums"])]
        results = {q: tracks for q in ("epic orchestral battle", "taiko drums", "cinematic action", "epic cinematic", "epic orchestral", "action trailer")}
        files = {"https://img/1.jpg": b"x" * 200_000, "https://img/2.jpg": b"x" * 200_000}
        offered = {}
        with tempfile.TemporaryDirectory() as tmp:
            none = find_music(FakeOpenverse(results, files), "hype", "s", Path(tmp), format_name="Who Would Win",
                              picker=lambda c: (offered.setdefault("titles", [x["title"] for x in c]), None)[1])
            self.assertIsNone(none)
            self.assertEqual(offered["titles"], ["Taiko Storm"])  # trance never offered
            picked = find_music(FakeOpenverse(results, files), "hype", "s", Path(tmp), format_name="Who Would Win", picker=lambda c: "id2")
            self.assertEqual(picked.title, "Taiko Storm")
