import unittest
from pathlib import Path

from rag import AnimeRAG
from rag.retriever import NO_KNOWLEDGE_MESSAGE, tokenize

FIXTURES = Path(__file__).parent / "fixtures" / "knowledge"


def ids(response):
    return [fact.knowledge_id for fact in response.results]


class RetrieverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rag = AnimeRAG(FIXTURES)

    def test_loads_all_fixture_records(self):
        self.assertEqual(len(self.rag.records), 8)

    def test_relevant_record_ranks_first(self):
        response = self.rag.retrieve("Itachi and Sasuke relationship", anime="Naruto")
        self.assertTrue(response.found)
        self.assertEqual(ids(response)[0], "NAR-101")
        scores = [f.score for f in response.results]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_unverified_records_excluded_by_default(self):
        response = self.rag.retrieve("crow summon")
        self.assertNotIn("NAR-103", ids(response))
        self.assertTrue(all(f.verified for f in response.results))

    def test_unverified_records_available_when_requested(self):
        response = self.rag.retrieve("crow summon", verified_only=False)
        self.assertEqual(ids(response)[0], "NAR-103")

    def test_anime_filter_and_aliases(self):
        for name in ("One Piece", "ONE PIECE", "op", "one-piece"):
            response = self.rag.retrieve("rubber swords filler luffy zoro", anime=name)
            self.assertTrue(response.found, name)
            self.assertTrue(all(f.anime == "One Piece" for f in response.results), name)

    def test_anime_filter_excludes_other_anime(self):
        response = self.rag.retrieve("ramen", anime="One Piece")
        self.assertFalse(response.found)

    def test_character_filter(self):
        response = self.rag.retrieve("fruit filler swords", characters=["monkey d. luffy"])
        self.assertTrue(response.found)
        self.assertTrue(all("Monkey D. Luffy" in f.characters for f in response.results))

    def test_category_filter(self):
        response = self.rag.retrieve("itachi", category="relationship")
        self.assertEqual(ids(response), ["NAR-101"])

    def test_theories_excluded_in_fact_mode(self):
        response = self.rag.retrieve("itachi eyes illness")
        self.assertNotIn("NAR-104", ids(response))

    def test_theories_included_in_theory_mode(self):
        response = self.rag.retrieve("itachi eyes illness", mode="theory")
        self.assertEqual(ids(response)[0], "NAR-104")
        self.assertIn("FAN THEORY - NOT CONFIRMED", response.build_context())

    def test_anime_original_counts_as_fact(self):
        response = self.rag.retrieve("G-8 filler marine base")
        self.assertEqual(ids(response), ["OP-103"])
        response = self.rag.retrieve("G-8 filler marine base", canon_statuses=["canon"])
        self.assertFalse(response.found)

    def test_spoiler_filter(self):
        default = self.rag.retrieve("uchiha massacre")
        self.assertNotIn("NAR-102", ids(default))  # default max_spoiler is "medium"
        allowed = self.rag.retrieve("uchiha massacre", max_spoiler="high")
        self.assertEqual(ids(allowed)[0], "NAR-102")

    def test_very_common_term_still_matches(self):
        # "itachi" is in exactly half the corpus -> BM25 IDF is 0. Must still match.
        response = self.rag.retrieve("itachi", max_spoiler="high", mode="theory")
        self.assertEqual(set(ids(response)), {"NAR-101", "NAR-102", "NAR-104"})

    def test_top_k_limits_results(self):
        response = self.rag.retrieve("itachi sasuke", top_k=1, max_spoiler="high")
        self.assertEqual(len(response.results), 1)

    def test_every_result_has_source(self):
        response = self.rag.retrieve("itachi luffy zoro naruto ramen", top_k=10, max_spoiler="high")
        self.assertTrue(response.found)
        for fact in response.results:
            self.assertTrue(fact.source_url.startswith("https://"), fact.knowledge_id)

    def test_empty_query_returns_no_results(self):
        for query in ("", "   ", "the and of"):
            response = self.rag.retrieve(query)
            self.assertFalse(response.found, repr(query))
            self.assertEqual(response.results, [])

    def test_unknown_topic_returns_explicit_no_information(self):
        response = self.rag.retrieve("Goku super saiyan transformation")
        self.assertFalse(response.found)
        self.assertEqual(response.results, [])
        self.assertEqual(response.message, NO_KNOWLEDGE_MESSAGE)
        self.assertTrue(response.build_context().startswith("NO_APPROVED_KNOWLEDGE"))

    def test_context_contains_ids_and_sources(self):
        context = self.rag.retrieve("Itachi and Sasuke relationship").build_context()
        self.assertIn("[NAR-101]", context)
        self.assertIn("Source: https://example.org/test/itachi", context)

    def test_to_dict_is_json_friendly(self):
        import json
        data = self.rag.retrieve("luffy rubber").to_dict()
        self.assertEqual(json.loads(json.dumps(data))["results"][0]["knowledge_id"], "OP-101")

    def test_invalid_arguments_raise(self):
        with self.assertRaises(ValueError):
            self.rag.retrieve("itachi", top_k=0)
        with self.assertRaises(ValueError):
            self.rag.retrieve("itachi", max_spoiler="extreme")
        with self.assertRaises(ValueError):
            self.rag.retrieve("itachi", mode="fanfic")
        with self.assertRaises(ValueError):
            self.rag.retrieve("itachi", category="gossip")
        with self.assertRaises(ValueError):
            self.rag.retrieve("itachi", canon_statuses=["rumour"])

    def test_empty_knowledge_base(self):
        rag = AnimeRAG(FIXTURES, records=[])
        response = rag.retrieve("itachi")
        self.assertFalse(response.found)


class TokenizerTests(unittest.TestCase):
    def test_accents_case_and_punctuation(self):
        self.assertEqual(tokenize("Shōnen JUMP!"), ["shonen", "jump"])

    def test_stopwords_and_plurals(self):
        self.assertEqual(tokenize("The swords of Zoro"), ["sword", "zoro"])
        self.assertEqual(tokenize("boss"), ["boss"])

    def test_possessive(self):
        self.assertEqual(tokenize("Naruto's dream"), ["naruto", "dream"])


if __name__ == "__main__":
    unittest.main()
