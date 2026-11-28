import json
import unittest


class FakeSF:
    def __init__(self, rows):
        self.rows = rows
        self.upserts = []

    def query(self, q):
        key = q.split("Key__c = '")[1].split("'")[0]
        return [{"Value__c": json.dumps(self.rows[key])}] if key in self.rows else []

    def upsert(self, *a):
        self.upserts.append(a)


class FakeIG:
    def search_audio(self, query=None):
        if query is None:
            return [{"audio_id": "1", "title": "Hot", "display_artist": "A", "duration_in_ms": 60000},
                    {"audio_id": "2", "title": "Short", "display_artist": "B", "duration_in_ms": 5000}]
        return [{"audio_id": "9" + query[:2], "title": query, "display_artist": "C", "duration_in_ms": 90000}]

    def online_followers(self):
        return {"9": 40, "21": 120}


class GrowthTests(unittest.TestCase):
    def test_trending_sync_and_choice(self):
        from pipeline.growth import choose_trending, sync_audience_hours, sync_trending_audio
        sf = FakeSF({})
        n = sync_trending_audio(FakeIG(), sf)
        self.assertGreaterEqual(n, 2)  # the 5-second clip is skipped
        stored = json.loads(sf.upserts[0][3]["Value__c"])
        self.assertNotIn("2", [t["audio_id"] for t in stored["tracks"]])
        sf2 = FakeSF({"trending_audio": stored, "trending_audio_share": 1.0})
        pick = choose_trending(sf2, "battle", [], "seed")
        self.assertEqual(pick["mood"], "battle")
        used = choose_trending(sf2, "battle", [f"x [{pick['audio_id']}]"], "seed")
        self.assertNotEqual(used["audio_id"], pick["audio_id"])
        self.assertIsNone(choose_trending(FakeSF({"trending_audio": stored, "trending_audio_share": 0}), "battle", [], "s"))
        self.assertEqual(sync_audience_hours(FakeIG(), sf)["21"], 120)

    def test_first_comment_and_caption(self):
        from pipeline.growth import first_comment, search_caption
        script = {"anime": "One Piece", "scenes": [{"role": "outro", "narration": "Follow for more. Would Luffy beat him?"}]}
        self.assertEqual(first_comment(script, "Fact"), "Would Luffy beat him? 👇")
        self.assertIn("👇", first_comment({"scenes": []}, "Who Would Win"))
        self.assertTrue(search_caption("Gear 5 is wild\n#onepiece", "One Piece").startswith("Gear 5 is wild | One Piece"))
        self.assertEqual(search_caption("One Piece secret", "One Piece"), "One Piece secret")

    def test_audio_configuration_param(self):
        from integrations.instagram import InstagramClient
        c = InstagramClient("t", user_id="1")
        p = c._container_params("cap", True, None, 0, None, {"audio_id": 5, "audio_volume": 30})
        self.assertEqual(json.loads(p["audio_configuration"]), {"audio_id": "5", "audio_volume": 30, "video_volume": 100})
        self.assertIsNone(c._container_params("cap", True, None, 0)["audio_configuration"])


if __name__ == "__main__":
    unittest.main()


class FacebookTests(unittest.TestCase):
    def test_off_without_page_and_never_raises(self):
        import os
        from integrations.facebook import cross_post_reel
        os.environ.pop("FB_PAGE_ID", None)
        self.assertIsNone(cross_post_reel("https://x/v.mp4", "cap"))
        os.environ["FB_PAGE_ID"] = "123"; os.environ["IG_ACCESS_TOKEN"] = "t"
        def boom(*a, **k):
            raise OSError("no network")
        try:
            self.assertIsNone(cross_post_reel("https://x/v.mp4", "cap", opener=boom))
        finally:
            os.environ.pop("FB_PAGE_ID", None)
            os.environ.pop("IG_ACCESS_TOKEN", None)
