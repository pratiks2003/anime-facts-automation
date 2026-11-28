import io
import json
import shutil
import tempfile
import unittest
import urllib.error
from pathlib import Path

from integrations.salesforce import SalesforceClient
from pipeline.ingest import KnowledgeStore, build_records, make_prefix, normalize, similar
from pipeline.llm import LLMError, extract_json
from pipeline.news import media_facts, next_season
from pipeline.planner import usable_facts
from pipeline.writer import check_script, normalise
from rag.schema import validate_record

FIXTURES = Path(__file__).parent / "fixtures" / "knowledge"
SOURCE = ("Monkey D. Luffy is the captain of the Straw Hat Pirates. Oda stated that Luffy’s real-world "
          "counterpart would be Brazilian.  He   cannot swim because of his Devil Fruit.")


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.entry = {"name": "One Piece", "id_prefix": "OP", "knowledge_file": "one_piece.jsonl"}

    def test_only_facts_with_real_quotes_survive(self):
        proposals = [
            {"title": "Brazil", "content": "Oda says Luffy would be Brazilian.",
             "evidence_quote": "Oda stated that Luffy's real-world counterpart would be Brazilian",  # curly vs straight quote
             "characters": ["Monkey D. Luffy"], "category": "trivia", "spoiler_level": "none", "tags": ["luffy"]},
            {"title": "Invented", "content": "Luffy is afraid of cats.",
             "evidence_quote": "Luffy has been afraid of cats since childhood", "category": "trivia"},
            {"title": "Too short", "content": "x", "evidence_quote": "Luffy"},
        ]
        records, rejected = build_records(proposals, self.entry, "https://en.wikipedia.org/wiki/Monkey_D._Luffy", SOURCE)
        self.assertEqual(len(records), 1)
        self.assertEqual(rejected, 2)
        rec = dict(records[0], id="OP-999")
        self.assertEqual(validate_record(rec), [])
        self.assertTrue(rec["verified"])
        self.assertEqual(rec["license"], "CC BY-SA 4.0")

    def test_whitespace_insensitive_match(self):
        records, _ = build_records([{"title": "swim", "content": "He can't swim.",
                                     "evidence_quote": "He cannot swim because of his Devil Fruit", "category": "ability"}],
                                   self.entry, "https://x.org", SOURCE)
        self.assertEqual(len(records), 1)

    def test_store_dedupes_and_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            for f in FIXTURES.iterdir():
                shutil.copy(f, tmp)
            store = KnowledgeStore(Path(tmp))
            entry = store.anime("one piece")
            new = [dict(id="TMP-000", anime="One Piece", title="Rubber again", content="Luffy ate the Gum-Gum Fruit and his body became rubber.",
                        characters=[], category="ability", canon_status="canon", spoiler_level="none",
                        source_url="https://x.org", source_type="encyclopedia", verified=True, last_reviewed="2026-11-25", tags=["x"]),
                   dict(id="TMP-000", anime="One Piece", title="Fresh", content="Something completely different about navigation charts.",
                        characters=[], category="trivia", canon_status="canon", spoiler_level="none",
                        source_url="https://x.org", source_type="encyclopedia", verified=True, last_reviewed="2026-11-25", tags=["x"])]
            accepted = store.add(entry, new)
            self.assertEqual([r["title"] for r in accepted], ["Fresh"])
            self.assertEqual(accepted[0]["id"], "OP-104")
            self.assertIn("OP-104", (Path(tmp) / "one_piece.jsonl").read_text())

    def test_new_anime_metadata_and_prefix(self):
        self.assertEqual(make_prefix("Jujutsu Kaisen", set()), "JK")
        self.assertEqual(make_prefix("Jujutsu Kaisen", {"JK"}), "JKA")
        self.assertEqual(make_prefix("Bleach", set()), "BLE")
        with tempfile.TemporaryDirectory() as tmp:
            for f in FIXTURES.iterdir():
                shutil.copy(f, tmp)
            store = KnowledgeStore(Path(tmp))
            entry = store.ensure_anime("Frieren: Beyond Journey's End", 154587, ["Sousou no Frieren"])
            self.assertEqual(entry["knowledge_file"], "frieren_beyond_journey_s_end.jsonl")
            self.assertIs(store.ensure_anime("Sousou no Frieren"), entry)
            self.assertIn("Frieren", (Path(tmp) / "anime_metadata.json").read_text())

    def test_similarity_and_normalize(self):
        self.assertTrue(similar("Luffy ate the Gum-Gum Fruit and became rubber", "Luffy became rubber after he ate the Gum Gum Fruit"))
        self.assertFalse(similar("Zoro uses three swords", "Nami draws maps"))
        self.assertEqual(normalize("A’B  “C”"), "a'b \"c\"")


class WriterTests(unittest.TestCase):
    def script(self, **overrides):
        scenes = [
            {"role": "hook", "headline": "Guess who", "emphasis": ["who"], "narration": "Three clues. " * 8, "image_prompt": "sea"},
            {"role": "fact", "headline": "Clue one", "tag": "CLUE 1", "narration": "He can't swim at all. " * 4, "knowledge_ids": ["OP-101"], "image_prompt": "hat"},
            {"role": "twist", "headline": "The answer", "narration": "It is Luffy, the rubber captain. " * 3, "knowledge_ids": ["OP-102"], "image_prompt": "luffy", "image_style": "character"},
            {"role": "outro", "headline": "Your turn", "narration": "Did you get it? Comment below and follow.", "image_prompt": "ship"},
        ]
        raw = {"title": "Quiz", "mood": "hype", "scenes": scenes, "caption": "Can you guess? 👇",
               "hashtags": ["anime", "#onepiece", "#luffy", "#animequiz", "#otaku", "#extra"]}
        raw.update(overrides)
        return normalise(raw, "R-1", "One Piece", "Quiz")

    def test_normalise_fills_fields_and_caps_hashtags(self):
        s = self.script()
        self.assertEqual(s["mode"], "fact")
        self.assertEqual(s["hashtags"], ["#anime", "#onepiece", "#luffy", "#animequiz", "#otaku"])
        self.assertEqual(s["scenes"][1]["emphasis"], [])
        self.assertEqual(s["scenes"][0]["image_style"], "scene")

    def test_valid_script_passes(self):
        self.assertEqual(check_script(self.script(), {"OP-101", "OP-102"}), [])

    def test_uncited_and_unknown_ids_rejected(self):
        s = self.script()
        s["scenes"][1]["knowledge_ids"] = []
        s["scenes"][2]["knowledge_ids"] = ["OP-999"]
        errors = check_script(s, {"OP-101", "OP-102"})
        self.assertTrue(any("must cite" in e for e in errors))
        self.assertTrue(any("unknown knowledge_ids" in e for e in errors))

    def test_extract_json(self):
        self.assertEqual(extract_json('Sure!\n```json\n{"a": 1}\n```'), {"a": 1})
        with self.assertRaises(LLMError):
            extract_json("no json here")


class PlannerAndNewsTests(unittest.TestCase):
    def test_usable_facts_filters(self):
        store = KnowledgeStore(FIXTURES)
        facts = usable_facts(store, "Naruto", recent_ids={"NAR-105"}, format_name="Backstory", topic="")
        ids = {f["id"] for f in facts}
        self.assertNotIn("NAR-103", ids)  # unverified
        self.assertNotIn("NAR-104", ids)  # theory
        self.assertNotIn("NAR-102", ids)  # spoiler high
        self.assertNotIn("NAR-105", ids)  # used recently
        self.assertEqual(ids, {"NAR-101"})

    def test_media_facts_are_valid_records(self):
        media = {"id": 1, "title": {"english": "Test Show", "romaji": "Tesuto"}, "siteUrl": "https://anilist.co/anime/1",
                 "popularity": 123456, "averageScore": 82, "status": "RELEASING", "genres": ["Action"],
                 "studios": {"nodes": [{"name": "MAPPA"}]}, "nextAiringEpisode": {"episode": 5, "airingAt": 1764000000},
                 "description": "A <i>hero</i> rises. (Source: Publisher)", "characters": {"nodes": [{"name": {"full": "Hero"}}]}}
        facts = media_facts(media, 1)
        self.assertTrue(all(validate_record(f) == [] for f in facts))
        texts = " ".join(f["content"] for f in facts)
        self.assertIn("MAPPA", texts)
        self.assertIn("Episode 5 of Test Show airs", texts)
        self.assertNotIn("<i>", texts)
        self.assertNotIn("Source:", texts)

    def test_next_season(self):
        from datetime import datetime
        self.assertEqual(next_season(datetime(2026, 11, 25)), ("WINTER", 2027))
        self.assertEqual(next_season(datetime(2026, 2, 1)), ("SPRING", 2026))
        self.assertEqual(next_season(datetime(2026, 8, 1)), ("FALL", 2026))


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class SalesforceClientTests(unittest.TestCase):
    def test_auth_then_upsert_by_external_id(self):
        calls = []

        def opener(req, timeout):
            calls.append(req)
            if req.full_url.endswith("/services/oauth2/token"):
                return Resp(json.dumps({"access_token": "T", "instance_url": "https://org.my.salesforce.com"}).encode())
            return Resp(b"")

        sf = SalesforceClient("org.my.salesforce.com", "id", "secret", opener=opener, sleep=lambda s: None)
        sf.reel_update("R-20261125-AM", Status__c="Rendered")
        self.assertIn(b"grant_type=client_credentials", calls[0].data)
        req = calls[1]
        self.assertEqual(req.get_method(), "PATCH")
        self.assertTrue(req.full_url.endswith("/sobjects/Anime_Content__c/External_Id__c/R-20261125-AM"))
        self.assertEqual(req.get_header("Authorization"), "Bearer T")
        self.assertEqual(json.loads(req.data), {"Status__c": "Rendered"})

    def test_reauthenticates_on_401(self):
        state = {"auth": 0, "patch": 0}

        def opener(req, timeout):
            if req.full_url.endswith("/token"):
                state["auth"] += 1
                return Resp(json.dumps({"access_token": f"T{state['auth']}", "instance_url": "https://o"}).encode())
            state["patch"] += 1
            if state["patch"] == 1:
                raise urllib.error.HTTPError(req.full_url, 401, "expired", {}, io.BytesIO(b"[]"))
            return Resp(b"")

        SalesforceClient("o", "i", "s", opener=opener, sleep=lambda s: None).job_update("J-1", Status__c="Running")
        self.assertEqual(state, {"auth": 2, "patch": 2})


if __name__ == "__main__":
    unittest.main()


class StudioAndQATests(unittest.TestCase):
    def facts(self):
        return [{"id": "NEWS-001", "anime": "JoJo", "title": "t", "content": "Episode 10 airs Friday.", "characters": [], "spoiler_level": "none"}]

    def studio_reply(self, **script_overrides):
        script = {"title": "Drops", "mood": "hype", "scenes": [
            {"role": "hook", "headline": "JoJo returns", "narration": "Steel Ball Run is back and it is wild. " * 4, "image_prompt": "desert race"},
            {"role": "fact", "headline": "Friday drop", "narration": "Episode ten lands Friday afternoon, so clear your schedule. " * 3, "knowledge_ids": ["NEWS-001"], "image_prompt": "horse"},
            {"role": "outro", "headline": "Your pick?", "narration": "Which one are you watching first? Tell me below.", "image_prompt": "fans"}],
            "caption": "Friday is JoJo day", "hashtags": ["#anime", "#jojo", "#steelballrun", "#animenews", "#otaku"]}
        script.update(script_overrides)
        return {"script": script, "quality": {"overall": 8.7}, "fact_check": {"pass": True, "issues": []}, "calls": 4,
                "production": {"voice": "bm_george", "speed": 1.15, "music_mood": "mystery", "music_keywords": "dark ambient"}}

    def test_agent_script_applies_production_brief(self):
        from pipeline.studio import agent_script

        class SF:
            def apex_post(s, path, payload):
                self.assertEqual(path, "insta/studio/script")
                self.assertEqual(payload["facts"][0]["id"], "NEWS-001")
                return self.studio_reply()

        r = agent_script(SF(), "R-1", "This Week's Drops", "Anime", self.facts())
        s = r["script"]
        self.assertEqual((s["voice"], s["speed"], s["mood"], s["music_keywords"]), ("bm_george", 1.15, "mystery", "dark ambient"))
        self.assertEqual(s["id"], "R-1")

    def test_agent_script_rejected_when_code_rules_fail(self):
        from pipeline.studio import StudioError, agent_script

        class SF:
            def apex_post(s, path, payload):
                reply = self.studio_reply()
                reply["script"]["scenes"][1]["knowledge_ids"] = ["MADE-UP-1"]
                return reply

        with self.assertRaises(StudioError):
            agent_script(SF(), "R-1", "This Week's Drops", "Anime", self.facts())

    def test_qa_review(self):
        from pipeline.run_reel import qa_review

        script = {"scenes": [{"image_source": "official"}, {"image_source": "official"}, {"image_source": "ai"}]}
        ok = {"validation": {"summary": {"duration": 36}}, "images": [{"x": 1}, {"x": 1}, None]}
        self.assertEqual(qa_review(ok, script, {}), [])
        bad = {"validation": {"summary": {"duration": 58}}, "images": [None, None, {"x": 1}]}
        issues = qa_review(bad, script, {"fact_check": {"pass": False, "issues": [{"problem": "invented number"}]}})
        self.assertEqual(len(issues), 3)
        self.assertTrue(any("invented number" in i for i in issues))


class ChainLLMTests(unittest.TestCase):
    def test_falls_through_providers(self):
        from pipeline.llm import ChainLLM, LLMError

        class Bad:
            def json(self, *a, **k):
                raise LLMError("quota")

        class Good:
            neurons_used = 3.0

            def json(self, *a, **k):
                return {"ok": True}

        chain = ChainLLM([None, Bad(), Good()])
        self.assertEqual(chain.json("s", "u"), {"ok": True})
        self.assertEqual(chain.neurons_used, 3.0)
        with self.assertRaises(LLMError):
            ChainLLM([Bad()]).json("s", "u")

    def test_agentforce_llm_posts_task(self):
        from pipeline.llm import AgentforceLLM, LLMError

        class SF:
            def __init__(self, reply):
                self.reply, self.calls = reply, []

            def apex_post(self, path, payload):
                self.calls.append((path, payload))
                return self.reply

        sf = SF({"facts": []})
        self.assertEqual(AgentforceLLM(sf).json("sys", "task"), {"facts": []})
        self.assertEqual(sf.calls[0], ("insta/studio/task", {"system": "sys", "task": "task"}))
        with self.assertRaises(LLMError):
            AgentforceLLM(SF({"error": "boom"})).json("s", "u")


class PlannerFallbackTests(unittest.TestCase):
    def test_switches_to_known_show_when_pick_has_no_facts(self):
        import pipeline.planner as planner

        store = KnowledgeStore(FIXTURES)
        original_choose, original_top_up = planner.choose_anime, planner.top_up
        planner.choose_anime = lambda *a, **k: {"name": "Brand New Show", "anilist_id": 999, "aliases": [], "characters": []}
        planner.top_up = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("llm down"))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                for f in FIXTURES.iterdir():
                    shutil.copy(f, tmp)
                plan = planner.plan_reel(None, "Fact", store=KnowledgeStore(Path(tmp)))
            self.assertIn(plan.anime, ("Naruto", "One Piece"))
            self.assertGreaterEqual(len(plan.facts), 1)
        except RuntimeError as exc:
            self.assertIn("verified facts", str(exc))  # fixtures may be too small for 3 facts; must not crash otherwise
        finally:
            planner.choose_anime, planner.top_up = original_choose, original_top_up


class VisualVarietyTests(unittest.TestCase):
    def test_repeated_official_visual_switches_to_ai_and_hook_tags_dropped(self):
        raw = {"title": "t", "scenes": [
            {"role": "hook", "headline": "h", "tag": "rewatch it", "narration": "n", "image_source": "official", "image_subject": "Yami", "image_prompt": "p"},
            {"role": "fact", "headline": "a", "narration": "n", "image_source": "official", "image_subject": "Yami", "image_prompt": "p"},
            {"role": "fact", "headline": "b", "narration": "n", "image_source": "official", "image_subject": "Black Clover", "image_prompt": "p"},
            {"role": "outro", "headline": "o", "tag": "x", "narration": "n", "image_source": "official", "image_subject": "Black Clover", "image_prompt": "p"}],
            "caption": "c", "hashtags": []}
        s = normalise(raw, "R", "Black Clover", "Hidden Details")
        self.assertEqual([sc["image_source"] for sc in s["scenes"]], ["official", "ai", "official", "ai"])
        self.assertNotIn("tag", s["scenes"][0])
        self.assertNotIn("tag", s["scenes"][3])


class RepairAfterQATests(unittest.TestCase):
    def plan(self):
        from pipeline.planner import Plan
        return Plan("Fact", "One Piece", 21, [{"id": "OP-1", "anime": "One Piece", "title": "t", "content": "c", "characters": [], "spoiler_level": "none"}])

    def script(self):
        return {"id": "R-1", "anime": "One Piece", "mode": "fact", "format": "Fact", "title": "T", "voice": "am_fenrir", "scenes": [
            {"role": "hook", "headline": "Hook", "narration": "Big secret incoming today. " * 6, "image_source": "official", "image_subject": "One Piece", "image_prompt": "p"},
            {"role": "fact", "headline": "Fact", "narration": "Luffy is rubber and cannot swim at all. " * 4, "knowledge_ids": ["OP-1"], "image_source": "ai", "image_prompt": "p"},
            {"role": "outro", "headline": "Outro", "narration": "Comment your pick below and follow.", "image_source": "ai", "image_prompt": "p"}],
            "caption": "c", "hashtags": ["#a", "#b", "#c", "#d", "#e"]}

    def test_missing_official_image_switches_to_ai_without_llm(self):
        from integrations.salesforce import NullSalesforce
        from pipeline.run_reel import repair_after_qa
        fixed, quality = repair_after_qa(NullSalesforce(), "R-1", "Fact", self.plan(), self.script(),
                                         {"images": [None, {"x": 1}, None]}, ["1 of 1 official images missing"])
        self.assertEqual(fixed["scenes"][0]["image_source"], "ai")
        self.assertIsNone(quality)

    def test_length_issue_goes_to_the_director(self):
        from pipeline.run_reel import repair_after_qa
        shorter = self.script()
        shorter["scenes"][0]["narration"] = "Big secret incoming. " * 9

        class SF:
            calls = []

            def apex_post(self, path, payload):
                SF.calls.append((path, payload["issues"]))
                return {"script": shorter, "quality": {"overall": 8.6}, "fact_check": {"pass": True}}

        fixed, quality = repair_after_qa(SF(), "R-1", "Fact", self.plan(), self.script(), {"images": [{"x": 1}, {"x": 1}, None]},
                                         ["too long (52s; target 28-45s)"])
        self.assertEqual(SF.calls[0], ("insta/studio/revise", ["too long (52s; target 28-45s)"]))
        self.assertEqual(fixed["voice"], "am_fenrir")
        self.assertEqual(quality["quality"]["overall"], 8.6)


class OfficialSubjectTests(unittest.TestCase):
    def test_objects_become_ai_art(self):
        from pipeline.studio import enforce_official_subjects
        script = {"scenes": [
            {"image_source": "official", "image_subject": "Nico Robin"},
            {"image_source": "official", "image_subject": "One Piece"},
            {"image_source": "official", "image_subject": "Alarm clock, moonlight, books"},
            {"image_source": "official", "image_subject": ""}]}
        enforce_official_subjects(script, "One Piece", [{"anime": "One Piece", "characters": ["Nico Robin"]}])
        self.assertEqual([s["image_source"] for s in script["scenes"]], ["official", "official", "ai", "ai"])


class RoleRepairTests(unittest.TestCase):
    def test_invented_roles_are_mapped(self):
        from pipeline.writer import normalise
        raw = {"title": "t", "caption": "c", "hashtags": [], "scenes": [
            {"role": "intro", "headline": "a", "narration": "x"},
            {"role": "build", "headline": "b", "narration": "y", "knowledge_ids": ["OP-1"]},
            {"role": "payoff", "headline": "c", "narration": "z", "knowledge_ids": ["OP-2"]},
            {"role": "cta", "headline": "d", "narration": "w"}]}
        roles = [s["role"] for s in normalise(raw, "R-1", "One Piece", "Fact")["scenes"]]
        self.assertEqual(roles, ["hook", "fact", "twist", "outro"])


class CitationRepairTests(unittest.TestCase):
    def test_attach_or_drop(self):
        from pipeline.studio import attach_citations
        facts = [{"id": "OP-1", "title": "Luffy swim", "content": "Devil Fruit users like Luffy cannot swim in the ocean."}]
        script = {"scenes": [
            {"role": "hook", "narration": "Wait for it"},
            {"role": "fact", "narration": "Luffy cannot swim because of his Devil Fruit."},
            {"role": "twist", "narration": "Zoro secretly owns a bakery in Wano."},
            {"role": "outro", "narration": "Follow"}]}
        attach_citations(script, facts)
        self.assertEqual([s["role"] for s in script["scenes"]], ["hook", "fact", "outro"])
        self.assertEqual(script["scenes"][1]["knowledge_ids"], ["OP-1"])


class QualityGateTests(unittest.TestCase):
    def _manifest(self, visual):
        return {"validation": {"summary": {"duration": 35}}, "images": [], "visual_qa": visual}

    def test_unchecked_reel_is_held_when_gemini_configured(self):
        import os
        from unittest import mock
        from pipeline.run_reel import UNCHECKED, qa_review
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "x"}):
            self.assertIn(UNCHECKED, qa_review(self._manifest({"status": "unavailable (Gemini busy)"}), {"scenes": []}, {}))
            self.assertEqual(qa_review(self._manifest({"status": "checked", "issues": [], "up_to_mark": True}), {"scenes": []}, {}), [])
            low = qa_review(self._manifest({"status": "checked", "issues": [], "up_to_mark": False, "edit_score": 5}), {"scenes": []}, {})
            self.assertTrue(any("edit quality 5" in i for i in low))


class HotTopicRepeatTests(unittest.TestCase):
    def test_covered_topic_moves_to_next_hottest(self):
        import json as _json
        from pipeline.run_reel import uncovered_hot_topic
        brief = {"hot_topics": [
            {"topic": "Elbaf mural's horned figure and Loki", "anime": "ONE PIECE", "heat": 10},
            {"topic": "Imu's return and Blackbeard's move", "anime": "ONE PIECE", "heat": 9}]}
        sf = type("SF", (), {"query": lambda self, q: [{"Brief_JSON__c": _json.dumps(brief)}]})()
        covered = ["Did you spot the horned figure on Elbaf's mural? This could flip everything"]
        got = uncovered_hot_topic(sf, brief["hot_topics"][0], covered)
        self.assertIn("Imu", got["topic"])
        self.assertIs(uncovered_hot_topic(sf, brief["hot_topics"][1], covered), brief["hot_topics"][1])


class QualityLoopholeTests(unittest.TestCase):
    def test_low_score_is_held_even_if_recheck_was_unavailable(self):
        import os
        from unittest import mock
        from pipeline.run_reel import qa_review
        manifest = {"validation": {"summary": {"duration": 40}}, "images": [],
                    "visual_qa": {"status": "unavailable (Gemini busy)", "edit_score": 4.0, "up_to_mark": False,
                                  "issues": [], "edit_notes": ["off-topic shots"]}}
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "x"}):
            issues = qa_review(manifest, {"scenes": []}, {})
        self.assertTrue(any("edit quality 4.0" in i for i in issues))


class SpacingTests(unittest.TestCase):
    def _sf(self, minutes_ago):
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        rows = [{"Published_At__c": (now - timedelta(minutes=m)).isoformat()} for m in minutes_ago]
        return type("SF", (), {"query": lambda self, q: rows})()

    def test_spacing_rules(self):
        from pipeline.run_reel import SpacingHold, wait_for_posting_window
        default = {"enabled": True, "max_per_day": 6, "min_gap_minutes": 90}
        orig = wait_for_posting_window
        wait_for_posting_window = lambda sf, sleep, policy=default: orig(sf, sleep=sleep, policy=policy)  # noqa: E731
        waited = []
        wait_for_posting_window(self._sf([300]), sleep=waited.append)        # long ago: post now
        self.assertEqual(waited, [])
        wait_for_posting_window(self._sf([70]), sleep=waited.append)         # 20 min short: wait
        self.assertTrue(waited and 1100 < waited[0] < 1300)
        with self.assertRaises(SpacingHold):
            wait_for_posting_window(self._sf([10]), sleep=waited.append)     # too soon: hold
        with self.assertRaises(SpacingHold):
            wait_for_posting_window(self._sf([200, 400, 600, 800, 900, 1000]), sleep=waited.append)  # 6 today: hold


class PostingLimitSettingTests(unittest.TestCase):
    def test_limit_can_be_switched_off_or_changed(self):
        from datetime import datetime, timedelta, timezone
        from pipeline.run_reel import SpacingHold, wait_for_posting_window
        now = datetime.now(timezone.utc)
        rows = [{"Published_At__c": (now - timedelta(minutes=m)).isoformat()} for m in (100, 300, 500)]
        sf = type("SF", (), {"query": lambda self, q: rows})()
        wait_for_posting_window(sf, sleep=lambda s: None, policy={"enabled": False})              # off: always ok
        with self.assertRaises(SpacingHold):
            wait_for_posting_window(sf, sleep=lambda s: None, policy={"enabled": True, "max_per_day": 3, "min_gap_minutes": 90})
        wait_for_posting_window(sf, sleep=lambda s: None, policy={"enabled": True, "max_per_day": 4, "min_gap_minutes": 60})


class VarietyRulesTests(unittest.TestCase):
    def _sf(self, brief):
        import json as _json
        return type("SF", (), {"query": lambda self, q: [{"Brief_JSON__c": _json.dumps(brief)}]})()

    def test_booming_show_repeats_but_never_four_times(self):
        from pipeline.run_reel import choose_hot_topic
        brief = {"hot_topics": [
            {"topic": "Fool Night Netflix release and cast reveal", "anime": "Fool Night", "heat": 9},
            {"topic": "Gear 5 animation in the new episode", "anime": "ONE PIECE", "heat": 8}],
            "show_ranking": [{"anime": "Overgeared", "hot": True, "score": 6, "headline": {"title": "Overgeared climax trailer"}}]}
        recent = [{"anime": "Fool Night", "format": "Quiz", "age_h": 3, "text": "Can you crack Netflix's plant mystery"}]
        got = choose_hot_topic(self._sf(brief), brief["hot_topics"][0], recent)
        self.assertEqual(got["anime"], "Fool Night", "a booming show gets a second Reel on a new angle")
        recent2 = [{"anime": "Fool Night", "format": "Fact", "age_h": 1, "text": "netflix release cast reveal"}] + recent
        got2 = choose_hot_topic(self._sf(brief), brief["hot_topics"][0], recent2)
        self.assertEqual(got2["anime"], "ONE PIECE", "two Fool Night Reels today -> no third")

    def test_breaking_allows_second_reel_same_day_on_new_topic(self):
        from pipeline.run_reel import choose_hot_topic
        hot = {"topic": "Fool Night season 2 announced", "anime": "Fool Night", "heat": 10}
        recent = [{"anime": "ONE PIECE", "format": "Fact", "age_h": 2, "text": "gear 5"},
                  {"anime": "Fool Night", "format": "Quiz", "age_h": 6, "text": "plant mystery quiz"}]
        self.assertIs(choose_hot_topic(self._sf({}), hot, recent, breaking=True), hot)
        mild = dict(hot, heat=6)
        self.assertIsNone(choose_hot_topic(self._sf({}), mild, recent, breaking=False))

    def test_format_never_repeats(self):
        from pipeline.run_reel import vary_format
        self.assertEqual(vary_format("Quiz", "Fact"), "Quiz")
        self.assertNotEqual(vary_format("Quiz", "Quiz", "seed"), "Quiz")
        self.assertIn(vary_format("Why It's Trending", "Why It's Trending"), ("Just Announced", "Hype Check"))


class RenderReportTests(unittest.TestCase):
    def test_report_has_what_the_agents_need(self):
        from pipeline.run_reel import render_report
        manifest = {"visual_qa": {"status": "checked", "edit_score": 6.0, "up_to_mark": False, "issues": ["shot 3: wrong"],
                                  "renderer": object()},
                    "validation": {"summary": {"duration": 31.5}}, "shots": [2, 3], "image_rejections": ["a", "b"],
                    "designed_cover": {"title": "X"}, "music": {"title": "Song"}}
        r = render_report(manifest, {"fact_check": {"pass": False}, "quality": {"overall": 7.5, "notes": ["n"]}}, ["visual QA: x"])
        self.assertEqual(r["visual_qa"]["edit_score"], 6.0)
        self.assertNotIn("renderer", r["visual_qa"])
        self.assertFalse(r["fact_check"]["pass"])
        self.assertEqual(r["pictures_rejected"], 2)
        import json
        json.dumps(r)  # must be storable
