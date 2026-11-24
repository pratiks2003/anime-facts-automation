import copy
import json
import shutil
import subprocess
import tempfile
import unittest
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
        s["scenes"][1]["narration"] = "word " * 200
        self.assertTrue(any("words" in e for e in validate_script(s)))
        s = copy.deepcopy(self.script)
        s["hashtags"] = ["anime"]
        self.assertTrue(validate_script(s))

    def test_helpers(self):
        self.assertEqual(knowledge_ids(self.script), ["OP-002", "OP-004", "OP-003"])
        self.assertTrue(full_caption(self.script).endswith("#strawhat"))


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
