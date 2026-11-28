import unittest


class TokenHealthTests(unittest.TestCase):
    def test_token_states(self):
        from pipeline.health import token_problem
        now = 1_000_000_000
        self.assertIsNone(token_problem({"data": {"is_valid": True, "expires_at": 0}}, now))
        self.assertIsNone(token_problem({"data": {"is_valid": True, "expires_at": now + 40 * 86400}}, now))
        self.assertEqual(token_problem({"data": {"is_valid": True, "expires_at": now + 7 * 86400}}, now)[0], "warning")
        self.assertEqual(token_problem({"data": {"is_valid": True, "expires_at": now + 2 * 86400}}, now)[0], "critical")
        self.assertEqual(token_problem({"data": {"is_valid": False}}, now)[0], "critical")


class KnowledgeSyncTests(unittest.TestCase):
    def test_one_document_per_show_and_change_plan(self):
        from tools.sync_knowledge import build_documents, plan
        recs = [{"id": "A-2", "anime": "One Piece", "title": "t", "content": "c2"},
                {"id": "A-1", "anime": "One Piece", "title": "t", "content": "c1"},
                {"id": "B-1", "anime": "Fool Night", "title": "t", "content": "x"}]
        docs = build_documents(recs)
        self.assertEqual(set(docs), {"One_Piece.txt", "Fool_Night.txt"})
        text = docs["One_Piece.txt"].decode()
        self.assertLess(text.index("A-1"), text.index("A-2"))
        existing = {"One_Piece.txt": {"fileSize": len(docs["One_Piece.txt"])}, "Fool_Night.txt": {"fileSize": 1}}
        self.assertEqual(plan(docs, existing), {"new": [], "changed": ["Fool_Night.txt"]})
        self.assertEqual(plan(docs, {})["new"], ["Fool_Night.txt", "One_Piece.txt"])


if __name__ == "__main__":
    unittest.main()


class EditorCapabilityTests(unittest.TestCase):
    def test_requests_are_validated_and_unknowns_reported(self):
        from video.editing import apply_edits, normalize_edits, style_overrides
        from video.style import resolve_style
        edits, unsupported = normalize_edits(
            '{"hook": ["impact_text", {"fx": "impact text", "text": "nobody noticed", "color": "red"}],'
            ' "twist": ["freeze", "hologram"], "climax": ["flash"], "all": ["letterbox"]}')
        self.assertEqual([f["fx"] for f in edits["hook"]], ["impact_text", "impact_text"])
        self.assertEqual(edits["hook"][1], {"fx": "impact_text", "text": "nobody noticed", "color": "red"})
        self.assertTrue(any("hologram" in u for u in unsupported) and any("climax" in u for u in unsupported))
        script = {"scenes": [{"role": "hook"}, {"role": "twist"}, {"role": "outro"}]}
        self.assertEqual(apply_edits(script, edits), 3 + 2 + 1)  # hook: letterbox + 2 texts; twist: letterbox + freeze; outro
        self.assertIn({"fx": "letterbox"}, script["scenes"][2]["fx"])
        style, unknown = style_overrides(resolve_style(None), {"transition": "flash", "motion": 9, "neon": True})
        self.assertEqual((style["transition"], style["motion"], unknown), ("flash", 1.0, ["style.neon"]))

    def test_effects_render(self):
        from PIL import Image
        from video.cinematic import CinematicRenderer
        from video.tts import Narration, SceneTiming, Word
        import numpy as np, tempfile, os
        d = tempfile.mkdtemp()
        paths = []
        for k, c in enumerate([(200, 30, 30), (30, 30, 200), (30, 200, 30)]):
            pth = os.path.join(d, f"{k}.png"); Image.new("RGB", (1080, 1920), c).save(pth); paths.append(pth)
        scenes = [SceneTiming(0, "hook", "x", 0.0, 2.0, [Word("Luffy", 0.1, 0.5)]),
                  SceneTiming(1, "fact", "y", 2.0, 4.0, [Word("wins", 2.1, 2.5)])]
        narration = Narration(np.zeros(96000), 24000, scenes)
        script = {"id": "t", "anime": "One Piece", "scenes": [
            {"role": "hook", "headline": "x", "fx": [{"fx": "impact_text", "text": "EXPOSED"}, {"fx": "freeze"}, {"fx": "bw"}]},
            {"role": "fact", "headline": "y", "fx": ["versus", "rank_badge", "letterbox", "red_tint", "zoom_punch", "glitch"]}]}
        from video.style import resolve_style
        r = CinematicRenderer(script, narration, resolve_style(None), [[paths[0]], [paths[1], paths[2]]])
        for t in (0.05, 0.5, 1.5, 2.05, 3.0):
            self.assertEqual(r.frame(t).size, (1080, 1920))
        self.assertIn("versus", r.fx_used)


class CapabilityExportTests(unittest.TestCase):
    def test_salesforce_copy_matches_the_editor(self):
        from tools.export_capabilities import TARGET, render
        self.assertEqual(TARGET.read_text(), render(),
                         "editor capabilities changed: run python -m tools.export_capabilities and deploy the static resource")
