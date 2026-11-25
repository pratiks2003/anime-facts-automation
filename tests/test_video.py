import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import numpy as np

from video.script import full_caption, knowledge_ids, load_script, validate_script
from video.sfx import SR, build_sfx_track
from video.tts import (Word, apply_pronunciations, caption_chunks, distribute_words, find_pauses,
                       speech_span, split_clauses, split_sentences, time_sentence, to_srt, SceneTiming)
from video.validate import top_level_atoms, validate_video

SAMPLE = Path(__file__).resolve().parent.parent / "content" / "samples" / "one_piece_luffy.json"


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.script = json.loads(SAMPLE.read_text())

    def test_sample_is_valid(self):
        self.assertEqual(validate_script(self.script), [])
        load_script(str(SAMPLE))

    def test_structure_rules(self):
        s = copy.deepcopy(self.script)
        s["scenes"][0]["role"] = "fact"
        self.assertIn("first scene must have role 'hook'", validate_script(s))
        s = copy.deepcopy(self.script)
        s["scenes"] = s["scenes"][:2]
        self.assertTrue(validate_script(s))

    def test_theory_scene_requires_theory_mode(self):
        s = copy.deepcopy(self.script)
        s["scenes"][1]["role"] = "theory"
        self.assertTrue(any("theory" in e for e in validate_script(s)))
        s["mode"] = "theory"
        self.assertEqual(validate_script(s), [])

    def test_length_and_hashtags(self):
        s = copy.deepcopy(self.script)
        s["scenes"][1]["narration"] = "word " * 320
        self.assertTrue(any("words" in e for e in validate_script(s)))
        s = copy.deepcopy(self.script)
        s["hashtags"] = ["anime"]
        self.assertTrue(validate_script(s))

    def test_helpers(self):
        self.assertEqual(knowledge_ids(self.script), ["OP-002", "OP-004", "OP-003"])
        self.assertTrue(full_caption(self.script).endswith("#otaku"))


class TimingTests(unittest.TestCase):
    def test_sentence_and_clause_split(self):
        self.assertEqual(split_sentences("One. Two? Three!"), ["One.", "Two?", "Three!"])
        self.assertEqual(split_clauses("The price? Like every user, he can't swim"), ["The price? Like every user,", "he can't swim"])

    def test_distribute_words_is_monotonic_and_bounded(self):
        words = distribute_words("a longer sentence here".split(), 1.0, 3.0)
        self.assertEqual(words[0].start, 1.0)
        self.assertAlmostEqual(words[-1].end, 3.0, places=2)
        for a, b in zip(words, words[1:]):
            self.assertLessEqual(a.end, b.start + 1e-6)
        self.assertGreater(words[1].end - words[1].start, words[0].end - words[0].start)  # longer word, longer time

    def test_speech_span_and_pauses(self):
        sr = 24000
        tone = np.sin(np.linspace(0, 2000, sr // 2)).astype(np.float32)
        audio = np.concatenate([np.zeros(sr // 4), tone, np.zeros(sr // 5), tone, np.zeros(sr // 4)]).astype(np.float32)
        s, e = speech_span(audio, sr)
        self.assertAlmostEqual(s / sr, 0.25, delta=0.02)
        self.assertAlmostEqual(e / sr, 0.25 + 0.5 + 0.2 + 0.5, delta=0.02)
        self.assertEqual(len(find_pauses(audio, sr, s, e)), 1)
        words = time_sentence("Hello there, general kenobi", audio, sr, offset=10.0)
        self.assertEqual([w.text for w in words], ["Hello", "there,", "general", "kenobi"])
        self.assertAlmostEqual(words[2].start, 10.0 + 0.95, delta=0.03)  # second clause starts after the pause

    def test_caption_chunks(self):
        words = [Word(t, i, i + 1) for i, t in enumerate("He can't swim. When Luffy ate the fruit".split())]
        chunks = [[w.text for w in c] for c in caption_chunks(words)]
        self.assertEqual(chunks[0], ["He", "can't", "swim."])
        self.assertTrue(all(len(c) <= 3 for c in chunks))

    def test_srt(self):
        scene = SceneTiming(0, "hook", "x", 0, 2, [Word("Hello", 0.0, 0.5), Word("world.", 0.5, 1.25)])
        self.assertIn("00:00:00,000 --> 00:00:01,250\nHello world.", to_srt([scene]))

    def test_pronunciations_whole_word_only(self):
        respell = {"Oda": "Oh-dah"}
        self.assertEqual(apply_pronunciations("Oda drew it. Odaiba is a place.", respell), "Oh-dah drew it. Odaiba is a place.")


class SfxTests(unittest.TestCase):
    def test_track_length_and_level(self):
        track = build_sfx_track(10.0, [0.15, 3.0, 7.0], ["hook", "fact", "outro"])
        self.assertEqual(len(track), 10 * SR)
        self.assertLess(np.abs(track).max(), 1.5)
        self.assertGreater(np.abs(track[: SR // 2]).max(), 0.1)  # impact at the start


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class ValidateVideoTests(unittest.TestCase):
    def make(self, tmp, name, size="1080x1920", extra=()):
        path = Path(tmp) / name
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=s={size}:r=30:d=4",
                        "-f", "lavfi", "-i", "sine=sample_rate=48000:duration=4",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", *extra, "-shortest", str(path)], check=True)
        return path

    def test_good_video_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self.make(tmp, "ok.mp4", extra=("-movflags", "+faststart"))
            errors, warnings, summary = validate_video(str(p), target=(3, 5))
            self.assertEqual(errors, [])
            self.assertEqual((summary["width"], summary["height"]), (1080, 1920))
            self.assertLess(top_level_atoms(p).index("moov"), top_level_atoms(p).index("mdat"))

    def test_wrong_resolution_and_no_faststart(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self.make(tmp, "bad.mp4", size="1280x720")
            errors, _, _ = validate_video(str(p))
            self.assertTrue(any("resolution" in e for e in errors))
            self.assertTrue(any("moov" in e for e in errors))


if __name__ == "__main__":
    unittest.main()


class VoiceTests(unittest.TestCase):
    def test_pick_voice_is_stable_and_varies(self):
        from video.tts import AURA_VOICES, pick_voice

        self.assertEqual(pick_voice(AURA_VOICES, "R-1"), pick_voice(AURA_VOICES, "R-1"))
        picks = {pick_voice(AURA_VOICES, f"R-{i}") for i in range(40)}
        self.assertGreater(len(picks), 6)

    def test_aura_failure_falls_back_to_kokoro_for_whole_reel(self):
        from video.tts import KOKORO_VOICES, Narrator

        class BrokenAura:
            def synthesize(self, text, voice):
                raise RuntimeError("429 daily allocation")

        n = Narrator(aura=BrokenAura())
        calls = []
        def fake(text, voice, speed):
            calls.append((n.backend, voice))
            if n.backend == "aura":
                raise RuntimeError("429 daily allocation")
            return np.ones(2400, dtype=np.float32) * 0.5

        n.synthesize = fake
        scenes = [{"role": "hook", "headline": "h", "narration": "One. Two."}, {"role": "outro", "headline": "o", "narration": "Three."}]
        narration = n.narrate_random(scenes, seed="R-9")
        self.assertEqual(n.backend, "kokoro")
        self.assertIn(n.voice_used, KOKORO_VOICES)
        self.assertTrue(all(b == "kokoro" for b, _ in calls[-3:]))
        self.assertGreater(narration.duration, 0)


# ------------------------------------------------------------------ v2: style spec, cinematic, shots, wiki

class StyleSpecTests(unittest.TestCase):
    def test_defaults_and_clamps(self):
        from video.style import DEFAULT_STYLE, resolve_style
        self.assertEqual(resolve_style(None), DEFAULT_STYLE)
        s = resolve_style({"image_interval": 0.1, "voice_speed": "9", "grading": "neon", "motion": -3,
                           "keyword_color": 0, "watermark": "x" * 99, "unknown": 1})
        self.assertEqual(s["image_interval"], 1.2)
        self.assertEqual(s["voice_speed"], 1.5)
        self.assertEqual(s["grading"], DEFAULT_STYLE["grading"])  # invalid choice ignored
        self.assertEqual(s["motion"], 0.0)
        self.assertFalse(s["keyword_color"])
        self.assertEqual(len(s["watermark"]), 40)
        self.assertNotIn("unknown", s)
        self.assertIsInstance(resolve_style({"target_seconds": 30.7})["target_seconds"], int)


class WikiSlugTests(unittest.TestCase):
    def test_slug_candidates(self):
        from video.wiki_art import slug_candidates
        self.assertEqual(slug_candidates("One Piece")[:2], ["onepiece", "one-piece"])
        self.assertIn("naruto", slug_candidates("Naruto: Shippuden"))
        self.assertIn("tensura", slug_candidates("That Time I Got Reincarnated as a Slime Season 4", ["Tensura 4"]))
        self.assertEqual(slug_candidates("転スラ"), [])

    def test_page_images_filters_junk(self):
        from video import wiki_art
        payload = {"query": {"pages": {
            "1": {"title": "File:Luffy Anime Infobox.png", "imageinfo": [{"url": "u1", "width": 900, "height": 1400, "mime": "image/png"}]},
            "2": {"title": "File:Luffy Bounty Rush.png", "imageinfo": [{"url": "u2", "width": 900, "height": 1400, "mime": "image/png"}]},
            "3": {"title": "File:Tiny.png", "imageinfo": [{"url": "u3", "width": 100, "height": 120, "mime": "image/png"}]},
            "4": {"title": "File:Anim.gif", "imageinfo": [{"url": "u4", "width": 900, "height": 900, "mime": "image/gif"}]}}}}
        wiki = wiki_art.FandomWiki("onepiece")
        with mock.patch.object(wiki_art, "_get_json", return_value=payload):
            self.assertEqual([c["url"] for c in wiki.page_images("Monkey D. Luffy")], ["u1"])


class ShotPlanTests(unittest.TestCase):
    def test_derived_and_padded(self):
        from video.shots import shot_plan
        script = {"anime": "One Piece"}
        plan = shot_plan({"image_subject": "Gecko Moria"}, script, 3, ["Luffy"])
        self.assertEqual(len(plan), 3)
        self.assertEqual(plan[0]["subject"], "Gecko Moria")
        self.assertEqual(shot_plan({}, script, 1)[0]["subject"], "One Piece")
        director = shot_plan({"shots": [{"subject": "Zoro", "source": "wiki"}, "junk"]}, script, 2)
        self.assertEqual([s["subject"] for s in director], ["Zoro", "Zoro"])


class CinematicRendererTests(unittest.TestCase):
    def test_frames_render_with_and_without_images(self):
        import tempfile
        from PIL import Image
        from video.cinematic import CinematicRenderer
        from video.style import resolve_style
        from video.tts import Narration, SceneTiming, Word
        with tempfile.TemporaryDirectory() as tmp:
            wide = Path(tmp) / "wide.jpg"
            Image.new("RGB", (1600, 900), (200, 40, 40)).save(wide)
            tall = Path(tmp) / "tall.jpg"
            Image.new("RGB", (700, 1200), (40, 200, 40)).save(tall)
            scenes = [SceneTiming(0, "hook", "Luffy can not swim", 0.0, 3.0,
                                  [Word("Luffy", 0.1, 0.5), Word("can", 0.5, 0.8), Word("not", 0.8, 1.1), Word("swim", 1.1, 1.6)]),
                      SceneTiming(1, "outro", "Follow", 3.0, 6.0, [Word("Follow", 3.1, 3.6)])]
            narration = Narration(audio=np.zeros(6 * 24000), sample_rate=24000, scenes=scenes)
            script = {"id": "T-1", "anime": "One Piece", "scenes": [{"emphasis": ["swim"]}, {}]}
            r = CinematicRenderer(script, narration, resolve_style({"image_interval": 1.5}), [[str(wide), str(tall)], []],
                                  handle="@x")
            self.assertGreaterEqual(len(r.shots), 4)
            for t in (0.0, 0.05, 1.3, 2.9, 4.0, 5.9):
                frame = r.frame(t)
                self.assertEqual(frame.size, (1080, 1920))
            self.assertTrue(dict((w.text, k) for w, k in r.words)["Luffy"] is False)  # first word isn't "proper noun" coloured
            self.assertTrue(dict((w.text, k) for w, k in r.words)["swim"])


class AudioLibraryTests(unittest.TestCase):
    INDEX = {"music": [
        {"id": "a", "key": "battle", "title": "War Drums", "file": "a.mp3"},
        {"id": "b", "key": "phonk", "title": "Night Drift", "file": "b.mp3"},
        {"id": "c", "key": "emotional", "title": "Rain Piano", "file": "c.mp3"}], "sfx": []}

    def test_candidates_follow_format_and_skip_recent(self):
        from video.audio_library import music_candidates
        ids = [e["id"] for e in music_candidates("Who Would Win", "hype", [], self.INDEX)]
        self.assertEqual(ids, ["a", "b"])
        ids = [e["id"] for e in music_candidates("Who Would Win", "hype", ["War Drums by X - stable pick"], self.INDEX)]
        self.assertEqual(ids, ["b"])
        self.assertEqual([e["id"] for e in music_candidates("Backstory", "sad", [], self.INDEX)], ["c"])

    def test_supervisor_none_means_no_music(self):
        from video.audio_library import pick_music
        self.assertIsNone(pick_music("Who Would Win", "", "seed", Path("/nonexistent"), picker=lambda c: None, index=self.INDEX))

    def test_sfx_events_follow_density(self):
        from video.audio_library import sfx_events
        shots = [0.0, 2.5, 5.0, 7.5, 10.0, 12.5]
        low = sfx_events([0.0, 5.0, 10.0], ["hook", "fact", "outro"], shots, 15.0, {"sfx_density": "low", "transition": "glitch"})
        high = sfx_events([0.0, 5.0, 10.0], ["hook", "fact", "outro"], shots, 15.0, {"sfx_density": "high", "transition": "glitch"})
        self.assertGreater(len(high), len(low))
        self.assertIn("glitch", [r for _, r, _ in high])
        self.assertIn("riser", [r for _, r, _ in low])
        self.assertEqual(low[0][1], "boom")

    def test_snap_to_beats(self):
        from video.audio_library import snap_to_beats
        beats = [i * 0.5 for i in range(40)]
        self.assertEqual(snap_to_beats([2.4, 4.9, 5.3], beats), [2.5, 5.0, 5.8])
        self.assertEqual(snap_to_beats([2.4], []), [2.4])

    @unittest.skipUnless(shutil.which("ffmpeg"), "needs FFmpeg")
    def test_beat_times_on_click_track(self):
        import tempfile
        import soundfile
        from video.audio_library import beat_times
        sr = 22050
        y = np.zeros(sr * 12, dtype=np.float32)
        for k in range(int(12 / 0.5)):  # 120 BPM clicks
            a = int(k * 0.5 * sr)
            y[a:a + 400] = np.hanning(400) * 0.9
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clicks.wav"
            soundfile.write(path, y, sr)
            beats = beat_times(path)
        self.assertGreater(len(beats), 15)
        gaps = np.diff(beats)
        self.assertAlmostEqual(float(np.median(gaps)), 0.5, delta=0.05)


class ImageInspectorTests(unittest.TestCase):
    def _img(self, tmp, name, size, noise=True, seed=0):
        from PIL import Image
        y, x = np.mgrid[0:size[1], 0:size[0]]
        grad = ((x * (3 + seed) + y * 2) % 256).astype(np.uint8)  # structured picture (not flat)
        arr = np.stack([grad, grad[::-1], 255 - grad], axis=-1) if noise else np.full((size[1], size[0], 3), 128, np.uint8)
        path = Path(tmp) / name
        Image.fromarray(arr).save(path)
        return path

    def test_technical_and_duplicates_without_clip(self):
        import tempfile
        from video.image_check import Inspector, full_bleed_ok
        with tempfile.TemporaryDirectory() as tmp:
            ins = Inspector(clip=None)
            self.assertIn("too small", ins.inspect(self._img(tmp, "a.png", (150, 150))).reason)
            self.assertIn("flat", ins.inspect(self._img(tmp, "b.png", (800, 800), noise=False)).reason)
            self.assertIn("aspect", ins.inspect(self._img(tmp, "c.png", (1900, 400))).reason)
            good = self._img(tmp, "d.png", (900, 1400), seed=3)
            self.assertTrue(ins.inspect(good).ok)
            self.assertIn("duplicate", ins.inspect(good).reason)
        self.assertTrue(full_bleed_ok(1080, 1920))
        self.assertFalse(full_bleed_ok(230, 345))

    def test_name_matches(self):
        from video.shots import name_matches
        self.assertTrue(name_matches("Monkey D. Luffy", "Luffy Monkey"))
        self.assertTrue(name_matches("Rain", "Rain Garland"))
        self.assertFalse(name_matches("Roronoa Zoro", "Luffy Monkey"))


class ClipTests(unittest.TestCase):
    def test_mentions(self):
        from video.clips import mentions
        self.assertTrue(mentions("Carry on, Basen, nothing to see here", ["Basen"]))
        self.assertTrue(mentions("The Apothecary Diaries S3 is here!", ["The Apothecary Diaries Season 3"]))
        self.assertFalse(mentions("Beam appreciation post", ["Black Clover"]))

    def test_renderer_plays_clip_frames(self):
        import tempfile
        from PIL import Image
        from video.cinematic import CinematicRenderer
        from video.style import resolve_style
        from video.tts import Narration, SceneTiming, Word
        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / "clip_test"
            clip.mkdir()
            for k in range(36):
                Image.new("RGB", (1080, 1920), (k * 7, 50, 90)).save(clip / f"{k:03d}.jpg")
            still = Path(tmp) / "s.jpg"
            Image.new("RGB", (1200, 1900), (10, 200, 10)).save(still)
            scenes = [SceneTiming(0, "hook", "x", 0.0, 3.0, [Word("Hi", 0.1, 0.5)])]
            narration = Narration(audio=np.zeros(3 * 24000), sample_rate=24000, scenes=scenes)
            r = CinematicRenderer({"id": "C", "anime": "X", "scenes": [{}]}, narration,
                                  resolve_style({"image_interval": 1.5}), [[str(clip), str(still)]])
            self.assertIsNotNone(r.shots[0].clip)
            early, late = r.frame(0.2), r.frame(1.0)
            self.assertNotEqual(early.getpixel((540, 200))[0], late.getpixel((540, 200))[0])  # it moves
            self.assertEqual(r.frame(2.5).size, (1080, 1920))


class NamedInTests(unittest.TestCase):
    def test_only_characters_the_narration_names(self):
        from video.shots import named_in
        cast = ["Nico Robin", "Donquixote Doflamingo", "Kaido"]
        self.assertEqual(named_in("Her bounty exploded after the Onigashima raid", cast), [])
        self.assertEqual(named_in("Robin can sprout arms anywhere", cast), ["Nico Robin"])


class VisualQATests(unittest.TestCase):
    def test_review_flags_major_mismatches(self):
        from types import SimpleNamespace
        from PIL import Image
        from video.visual_qa import review
        from video.tts import SceneTiming, Word
        shots = [SimpleNamespace(start=0.0, end=2.0, clip=None), SimpleNamespace(start=2.0, end=4.0, clip=None)]
        renderer = SimpleNamespace(shots=shots, frame=lambda t, captions=False: Image.new("RGB", (108, 192)))
        narration = SimpleNamespace(scenes=[SceneTiming(0, "fact", "x", 0, 4, [Word("Robin", 0.2, 0.6), Word("smiles", 2.2, 2.6)])])
        gemini = SimpleNamespace(json=lambda parts: {"shots": [
            {"shot": 1, "on_screen": "Nico Robin", "matches": True},
            {"shot": 2, "on_screen": "Doflamingo", "matches": False, "severity": "major", "problem": "wrong character"}],
            "summary": "one wrong"})
        r = review(renderer, narration, {"anime": "One Piece"}, gemini, ["a.jpg", "b.jpg"])
        self.assertEqual(r["bad"], [1])
        self.assertEqual(r["bad_sources"], ["b.jpg"])
        self.assertIn("Doflamingo", r["issues"][0])

    def test_secrets_are_redacted(self):
        from video.visual_qa import SECRET_RE
        self.assertEqual(SECRET_RE.sub("[x]", "token EAAB" + "a" * 30 + " ok"), "token [x] ok")


class CoverTests(unittest.TestCase):
    def test_design_fits_grid_and_title(self):
        import tempfile
        from types import SimpleNamespace
        from PIL import Image
        from video.cover import GRID_BOTTOM, GRID_TOP, cover_title, grid_view, make_cover
        script = {"anime": "One Piece", "title": "t", "scenes": [{"headline": "Robin's secret power revealed today now"}]}
        self.assertEqual(cover_title(script), "Robin's secret power revealed today")
        with tempfile.TemporaryDirectory() as tmp:
            pic = Path(tmp) / "p.jpg"
            Image.new("RGB", (900, 1400), (120, 40, 200)).save(pic)
            info = make_cover(SimpleNamespace(shots=[SimpleNamespace(source=str(pic))]), script, Path(tmp))
            cover = Image.open(Path(tmp) / "cover.jpg")
            self.assertEqual(cover.size, (1080, 1920))
            self.assertEqual(grid_view(cover).size, (1080, GRID_BOTTOM - GRID_TOP))
            self.assertEqual(info["source"], str(pic))
