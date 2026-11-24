"""BM25 retrieval over source-backed anime knowledge records."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Iterable, List, Optional, Sequence

from rank_bm25 import BM25Okapi

from .knowledge import (
    DEFAULT_KNOWLEDGE_DIR,
    PathLike,
    build_alias_index,
    load_knowledge,
    load_metadata,
    normalize_name,
    resolve_anime,
)
from .schema import CANON_STATUSES, CATEGORIES, SPOILER_LEVELS

SPOILER_RANK = {level: rank for rank, level in enumerate(SPOILER_LEVELS)}

# Which canon statuses each content mode may draw on.
# "theory" videos may cite theories, but must label them on screen.
CONTENT_MODES = {
    "fact": ("canon", "anime_original"),
    "ranking": ("canon", "anime_original"),
    "what_if": ("canon", "anime_original"),  # the fiction is written on top of real facts
    "theory": ("canon", "anime_original", "theory"),
}

NO_KNOWLEDGE_MESSAGE = (
    "No approved knowledge records match this request. "
    "Do not generate content for this topic until records are added and verified."
)

_STOPWORDS = frozenset(
    "a an and are as at be by did do does for from has have he her his how in is it "
    "its of on or she that the their them they this to was were what when where which "
    "who why with".split()
)


def tokenize(text: str) -> List[str]:
    """Lowercase, strip accents (Shōnen -> shonen), split, drop stopwords, light plural stemming."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    tokens = []
    for token in re.findall(r"[a-z0-9]+", text):
        if len(token) < 2 or token in _STOPWORDS:
            continue
        if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        tokens.append(token)
    return tokens


def _record_text(record: dict) -> str:
    return " ".join(
        [
            record["anime"],
            record["title"],
            record["title"],  # title counted twice: it summarises the fact
            record["content"],
            " ".join(record["characters"]),
            " ".join(record["tags"]),
            record["category"].replace("_", " "),
        ]
    )


@dataclass
class RetrievedFact:
    knowledge_id: str
    anime: str
    title: str
    content: str
    characters: List[str]
    category: str
    canon_status: str
    spoiler_level: str
    source_url: str
    source_type: str
    verified: bool
    last_reviewed: Optional[str]
    score: float

    @classmethod
    def from_record(cls, record: dict, score: float) -> "RetrievedFact":
        return cls(
            knowledge_id=record["id"],
            anime=record["anime"],
            title=record["title"],
            content=record["content"],
            characters=list(record["characters"]),
            category=record["category"],
            canon_status=record["canon_status"],
            spoiler_level=record["spoiler_level"],
            source_url=record["source_url"],
            source_type=record["source_type"],
            verified=record["verified"],
            last_reviewed=record.get("last_reviewed"),
            score=round(float(score), 4),
        )


@dataclass
class RetrievalResponse:
    query: str
    found: bool
    message: str
    filters: dict
    results: List[RetrievedFact] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def build_context(self) -> str:
        """Source-attributed context block to hand to the script-writing LLM."""
        if not self.found:
            return f"NO_APPROVED_KNOWLEDGE: {self.message}"
        blocks = []
        for fact in self.results:
            label = "FAN THEORY - NOT CONFIRMED" if fact.canon_status == "theory" else fact.canon_status
            blocks.append(
                f"[{fact.knowledge_id}] {fact.anime} | {label} | spoiler: {fact.spoiler_level}\n"
                f"{fact.title}: {fact.content}\n"
                f"Source: {fact.source_url}"
            )
        return "\n\n".join(blocks)


class AnimeRAG:
    """Keyword (BM25) retriever with metadata filters.

    rag = AnimeRAG()
    response = rag.retrieve("Itachi and Sasuke relationship", anime="Naruto", top_k=5)
    """

    def __init__(self, knowledge_dir: PathLike = DEFAULT_KNOWLEDGE_DIR, records: Optional[List[dict]] = None):
        self.records: List[dict] = records if records is not None else load_knowledge(knowledge_dir)
        self.alias_index = build_alias_index(load_metadata(knowledge_dir))
        corpus = [tokenize(_record_text(r)) for r in self.records]
        # A record "matches" if it shares at least one query term. We don't rely on
        # score > 0 for this: BM25 gives terms present in >= half the corpus an IDF
        # of zero (or a tiny floor), which would otherwise hide genuine matches such
        # as "itachi" in a Naruto-heavy knowledge base.
        self._token_sets = [set(tokens) for tokens in corpus]
        self._bm25 = BM25Okapi(corpus) if self.records else None

    def retrieve(
        self,
        query: str,
        anime: Optional[str] = None,
        characters: Optional[Iterable[str]] = None,
        category: Optional[str] = None,
        mode: str = "fact",
        canon_statuses: Optional[Sequence[str]] = None,
        max_spoiler: str = "medium",
        verified_only: bool = True,
        top_k: int = 5,
    ) -> RetrievalResponse:
        """Return the top_k matching records, or an explicit no-information response.

        canon_statuses overrides the statuses implied by `mode` when given.
        """
        if top_k < 1:
            raise ValueError("top_k must be >= 1")
        if max_spoiler not in SPOILER_RANK:
            raise ValueError(f"max_spoiler must be one of {list(SPOILER_LEVELS)}")
        if category is not None and category not in CATEGORIES:
            raise ValueError(f"category must be one of {list(CATEGORIES)}")
        if canon_statuses is None:
            if mode not in CONTENT_MODES:
                raise ValueError(f"mode must be one of {list(CONTENT_MODES)}")
            canon_statuses = CONTENT_MODES[mode]
        bad = [s for s in canon_statuses if s not in CANON_STATUSES]
        if bad:
            raise ValueError(f"unknown canon status(es): {bad}")

        anime_name = resolve_anime(anime, self.alias_index)
        wanted_characters = {normalize_name(c) for c in characters or [] if c.strip()}
        filters = {
            "anime": anime_name,
            "characters": sorted(wanted_characters),
            "category": category,
            "mode": mode,
            "canon_statuses": list(canon_statuses),
            "max_spoiler": max_spoiler,
            "verified_only": verified_only,
            "top_k": top_k,
        }

        query_tokens = tokenize(query or "")
        if not query_tokens:
            return RetrievalResponse(query, False, "Query is empty or has no searchable words.", filters)
        if self._bm25 is None:
            return RetrievalResponse(query, False, NO_KNOWLEDGE_MESSAGE, filters)

        allowed_statuses = set(canon_statuses)
        spoiler_limit = SPOILER_RANK[max_spoiler]
        anime_key = normalize_name(anime_name) if anime_name else None

        def passes(record: dict) -> bool:
            if verified_only and not record["verified"]:
                return False
            if anime_key and normalize_name(record["anime"]) != anime_key:
                return False
            if record["canon_status"] not in allowed_statuses:
                return False
            if SPOILER_RANK[record["spoiler_level"]] > spoiler_limit:
                return False
            if category and record["category"] != category:
                return False
            if wanted_characters:
                have = {normalize_name(c) for c in record["characters"]}
                if not wanted_characters & have:
                    return False
            return True

        query_set = set(query_tokens)
        scores = self._bm25.get_scores(query_tokens)
        ranked = sorted(
            (
                (score, idx)
                for idx, score in enumerate(scores)
                if query_set & self._token_sets[idx] and passes(self.records[idx])
            ),
            key=lambda pair: (-pair[0], self.records[pair[1]]["id"]),
        )[:top_k]

        if not ranked:
            return RetrievalResponse(query, False, NO_KNOWLEDGE_MESSAGE, filters)

        results = [RetrievedFact.from_record(self.records[idx], score) for score, idx in ranked]
        return RetrievalResponse(query, True, f"{len(results)} record(s) found.", filters, results)
