import json
import tempfile
import unittest
from pathlib import Path

from rag.knowledge import DEFAULT_KNOWLEDGE_DIR, KnowledgeValidationError, load_knowledge, load_metadata
from rag.schema import validate_record


def make_record(**overrides):
    record = {
        "id": "TST-001",
        "anime": "Test Anime",
        "title": "A title",
        "content": "Some content.",
        "characters": ["Someone"],
        "category": "trivia",
        "canon_status": "canon",
        "spoiler_level": "none",
        "source_url": "https://example.org/a",
        "source_type": "encyclopedia",
        "verified": False,
        "tags": ["test"],
    }
    record.update(overrides)
    return record


class SchemaTests(unittest.TestCase):
    def test_valid_record(self):
        self.assertEqual(validate_record(make_record()), [])

    def test_missing_source_url(self):
        record = make_record()
        del record["source_url"]
        self.assertIn("missing required field 'source_url'", validate_record(record))

    def test_source_must_be_https(self):
        self.assertTrue(validate_record(make_record(source_url="SOURCE_URL")))
        self.assertTrue(validate_record(make_record(source_url="http://example.org")))

    def test_verified_requires_review_date(self):
        errors = validate_record(make_record(verified=True))
        self.assertIn("verified records must have last_reviewed set", errors)
        self.assertEqual(validate_record(make_record(verified=True, last_reviewed="2026-11-24")), [])

    def test_bad_dates(self):
        self.assertTrue(validate_record(make_record(last_reviewed="24-11-2026")))
        self.assertTrue(validate_record(make_record(last_reviewed="2026-02-30")))

    def test_enums_enforced(self):
        self.assertTrue(validate_record(make_record(canon_status="confirmed")))
        self.assertTrue(validate_record(make_record(spoiler_level="huge")))
        self.assertTrue(validate_record(make_record(category="gossip")))

    def test_theory_must_use_theory_category(self):
        self.assertTrue(validate_record(make_record(canon_status="theory")))
        self.assertEqual(validate_record(make_record(canon_status="theory", category="theory")), [])

    def test_unknown_field_rejected(self):
        self.assertIn("unknown field(s): sourceurl", validate_record(make_record(sourceurl="x")))

    def test_bad_id_and_types(self):
        self.assertTrue(validate_record(make_record(id="nar1")))
        self.assertTrue(validate_record(make_record(verified="yes")))
        self.assertTrue(validate_record(make_record(tags=["ok", ""])))
        self.assertTrue(validate_record("not a dict"))


class LoaderTests(unittest.TestCase):
    def write(self, directory, name, lines):
        Path(directory, name).write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_duplicate_ids_across_files_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write(tmp, "a.jsonl", [json.dumps(make_record())])
            self.write(tmp, "b.jsonl", [json.dumps(make_record())])
            with self.assertRaises(KnowledgeValidationError) as ctx:
                load_knowledge(tmp)
            self.assertIn("duplicate id 'TST-001'", str(ctx.exception))

    def test_invalid_json_reported_with_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write(tmp, "a.jsonl", [json.dumps(make_record()), "{not json"])
            with self.assertRaises(KnowledgeValidationError) as ctx:
                load_knowledge(tmp)
            self.assertIn("a.jsonl:2: invalid JSON", str(ctx.exception))

    def test_blank_lines_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write(tmp, "a.jsonl", ["", json.dumps(make_record()), "   "])
            self.assertEqual(len(load_knowledge(tmp)), 1)

    def test_missing_directory(self):
        with self.assertRaises(FileNotFoundError):
            load_knowledge("/nonexistent/knowledge")


class RealKnowledgeTests(unittest.TestCase):
    """Guards the committed knowledge/ data so bad records fail CI."""

    def test_real_knowledge_is_valid(self):
        records = load_knowledge(DEFAULT_KNOWLEDGE_DIR)
        self.assertGreater(len(records), 0)

    def test_every_anime_has_metadata(self):
        names = {e["name"] for e in load_metadata(DEFAULT_KNOWLEDGE_DIR)}
        for record in load_knowledge(DEFAULT_KNOWLEDGE_DIR):
            self.assertIn(record["anime"], names, record["id"])

    def test_id_prefix_matches_anime(self):
        prefixes = {e["name"]: e["id_prefix"] for e in load_metadata(DEFAULT_KNOWLEDGE_DIR)}
        for record in load_knowledge(DEFAULT_KNOWLEDGE_DIR):
            self.assertTrue(record["id"].startswith(prefixes[record["anime"]] + "-"), record["id"])


if __name__ == "__main__":
    unittest.main()
