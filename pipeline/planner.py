"""Decide what a Reel is about and gather the verified facts to write it from.

Evergreen formats: pick an anime (trending + popular, not used in the last few Reels),
take its verified facts that weren't used recently, and top up the knowledge base
from Wikipedia (quote-verified) when it runs low.
News formats: live AniList data (pipeline.news).
"""

from __future__ import annotations

import hashlib
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Dict, List, Optional, Set

from rag.retriever import SPOILER_RANK, AnimeRAG

from .ingest import KnowledgeStore, ingest_page, normalize, wiki_page
from .news import character_titles, news_facts, search_media, title_of, trending_popular

NEWS_FORMATS = {"This Week's Drops", "Why It's Trending", "Hype Check", "Season Preview", "Just Announced"}
CATEGORY_PREFS: Dict[str, Set[str]] = {
    "Backstory": {"backstory", "character_profile", "relationship", "character_goal"},
    "Behind the Scenes": {"production", "trivia"},
    "Hidden Details": {"trivia", "world_building", "ability"},
    "Who Would Win": {"ability", "character_profile"},
    "Quiz": {"character_profile", "ability", "backstory", "character_goal"},
}
MIN_FACTS = 8
MAX_FACTS = 14
MAX_SPOILER = "medium"


@dataclass
class Plan:
    format: str
    anime: str
    anilist_id: Optional[int]
    facts: List[dict]
    context: str = ""
    extra_records: List[dict] = field(default_factory=list)  # live news facts (not in knowledge files)
    ingested: Dict[str, int] = field(default_factory=dict)
    media: List[dict] = field(default_factory=list)  # AniList media for news formats


def _daily_seed(*parts: str) -> int:
    return int(hashlib.sha1("|".join((date.today().isoformat(),) + parts).encode()).hexdigest(), 16)


def choose_anime(store: KnowledgeStore, recent_anime: List[str], hint: str, slot_seed: str,
                 opener: Callable = urllib.request.urlopen) -> dict:
    """Return {"name", "anilist_id", "aliases", "characters"}."""
    if hint:
        media = search_media(hint, opener)
        if media:
            return {"name": title_of(media), "anilist_id": media["id"],
                    "aliases": [t for t in [media["title"].get("romaji"), media["title"].get("english"), *(media.get("synonyms") or [])] if t],
                    "characters": [character_titles(c) for c in media["characters"]["nodes"]]}
    recent = {normalize(a) for a in recent_anime[:4]}
    candidates: List[dict] = []
    try:
        for m in trending_popular(opener):
            candidates.append({"name": title_of(m), "anilist_id": m["id"],
                               "aliases": [t for t in [m["title"].get("romaji"), m["title"].get("english"), *(m.get("synonyms") or [])] if t],
                               "characters": [character_titles(c) for c in m["characters"]["nodes"]]})
    except Exception:
        pass  # AniList down: fall back to the anime we already cover
    for e in store.metadata:
        if e.get("active", True) and not any(normalize(c["name"]) == normalize(e["name"]) or c["anilist_id"] == e.get("anilist_id") for c in candidates):
            candidates.append({"name": e["name"], "anilist_id": e.get("anilist_id"), "aliases": e.get("aliases", []), "characters": []})
    fresh = [c for c in candidates if normalize(c["name"]) not in recent] or candidates
    # Rotate deterministically per day+slot among the top few so it isn't always the #1 trend.
    top = fresh[:5]
    return top[_daily_seed(slot_seed) % len(top)]


def usable_facts(store: KnowledgeStore, anime: str, recent_ids: Set[str], format_name: str, topic: str) -> List[dict]:
    limit = SPOILER_RANK[MAX_SPOILER]
    facts = [r for r in store.facts_for(anime)
             if r["verified"] and r["canon_status"] != "theory" and SPOILER_RANK[r["spoiler_level"]] <= limit
             and r["id"] not in recent_ids]
    prefs = CATEGORY_PREFS.get(format_name, set())
    if topic:
        rag = AnimeRAG(records=facts) if facts else None
        hits = rag.retrieve(topic, top_k=MAX_FACTS, max_spoiler=MAX_SPOILER).results if rag else []
        order = {h.knowledge_id: i for i, h in enumerate(hits)}
        facts.sort(key=lambda r: order.get(r["id"], len(order)))
    else:
        seed = _daily_seed(anime, format_name)
        facts.sort(key=lambda r: (r["category"] not in prefs, hashlib.sha1(f"{seed}{r['id']}".encode()).hexdigest()))
    return facts[:MAX_FACTS]


def wiki_candidates(anime: dict) -> List[str]:
    titles = [anime["name"], *anime.get("aliases", [])]
    return list(dict.fromkeys(titles + [f"{anime['name']} (manga)"]))


def top_up(llm, store: KnowledgeStore, anime: dict, entry: dict, opener: Callable, max_pages: int = 2) -> Dict[str, int]:
    """Ingest up to max_pages new Wikipedia pages (main article, then popular characters).
    Only pages that already produced *verified* facts count as done."""
    done_urls = {r["source_url"] for r in store.facts_for(entry["name"]) if r["verified"]}
    totals = {"pages": 0, "accepted": 0, "rejected": 0}
    anime_key = normalize(entry["name"]).split(":")[0]
    candidates: List[tuple] = []  # (page title, is_main)
    for title in wiki_candidates(anime):
        page = wiki_page(title, opener)
        if page and ("anime" in normalize(page[2][:3000]) or "manga" in normalize(page[2][:3000])):
            candidates.append((page[0], True))
            break
    for titles in anime.get("characters", [])[:6]:
        for title in (titles if isinstance(titles, list) else [titles]):
            page = wiki_page(title, opener)
            if page and anime_key in normalize(page[2][:2500]):
                candidates.append((page[0], False))
                break
    for title, _ in candidates:
        if totals["pages"] >= max_pages:
            break
        page = wiki_page(title, opener)
        if not page or page[1] in done_urls:
            continue
        result = ingest_page(llm, store, entry, page[0], opener)
        totals["pages"] += 1
        totals["accepted"] += result["accepted"]
        totals["rejected"] += result["rejected"]
    return totals


def plan_reel(llm, format_name: str, anime_hint: str = "", topic: str = "", recent_ids: Optional[Set[str]] = None,
              recent_anime: Optional[List[str]] = None, slot_seed: str = "", store: Optional[KnowledgeStore] = None,
              opener: Callable = urllib.request.urlopen, strict_anime: bool = True) -> Plan:
    recent_ids = recent_ids or set()
    if format_name in NEWS_FORMATS:
        news = news_facts(format_name, opener)
        if not news["facts"]:
            raise RuntimeError(f"AniList returned nothing for {format_name}")
        main = news["shows"][0] if format_name in ("Why It's Trending", "Hype Check") else "Anime"
        return Plan(format_name, main, None, news["facts"], news["headline_context"], extra_records=news["facts"],
                    media=news["media"])

    store = store or KnowledgeStore()
    if not anime_hint and topic and store.anime(topic):
        anime_hint, topic = store.anime(topic)["name"], ""  # the "topic" is really an anime name
    anime = choose_anime(store, recent_anime or [], anime_hint, slot_seed or format_name, opener)
    entry = store.ensure_anime(anime["name"], anime.get("anilist_id"), anime.get("aliases"))
    facts = usable_facts(store, entry["name"], recent_ids, format_name, topic)
    ingested: Dict[str, int] = {}
    if len(facts) < MIN_FACTS:
        try:
            ingested = top_up(llm, store, anime, entry, opener)
        except Exception as exc:  # LLM quota / Wikipedia down: write from what we already have
            ingested = {"error": 1}
            print(f"[planner] knowledge top-up skipped: {type(exc).__name__}: {str(exc)[:160]}")
        facts = usable_facts(store, entry["name"], recent_ids, format_name, topic)
    if len(facts) < 3 and (not anime_hint or not strict_anime):
        # Couldn't learn enough about the trending / suggested pick: fall back to the best-covered show we know.
        # (Only an anime the owner asked for explicitly is strict.)
        known = sorted(store.metadata, key=lambda e: -len(usable_facts(store, e["name"], recent_ids, format_name, "")))
        for alt in known:
            alt_facts = usable_facts(store, alt["name"], recent_ids, format_name, "")
            if len(alt_facts) >= 3:
                print(f"[planner] {entry['name']} has too few verified facts; switching to {alt['name']}")
                entry, facts = alt, alt_facts
                break
    if len(facts) < 3:
        raise RuntimeError(f"only {len(facts)} verified facts available for {entry['name']}")
    return Plan(format_name, entry["name"], entry.get("anilist_id"), facts, ingested=ingested)
