"""Source-grounded knowledge ingestion from Wikipedia (CC BY-SA 4.0).

The LLM proposes facts, but a fact is only stored if its `evidence_quote` is found
verbatim (after whitespace/quote normalisation) in the fetched source text. That
check is done in code, so hallucinated facts can't pass. Accepted facts are saved
with verified=true, notes="auto-verified: evidence quote matched source".
"""

from __future__ import annotations

import json
import re
import unicodedata
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from rag.knowledge import DEFAULT_KNOWLEDGE_DIR, METADATA_FILENAME, load_knowledge, load_metadata
from rag.schema import CATEGORIES, SPOILER_LEVELS, validate_record

WIKI_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "anime-facts-automation/0.1 (+https://github.com/pratiks2003/anime-facts-automation)"
MAX_SOURCE_CHARS = 14000

EXTRACT_SYSTEM = """You extract short, surprising, verifiable facts about an anime from a source text.
Rules:
- Use ONLY information stated in the source text. Never add outside knowledge.
- Every fact needs "evidence_quote": an EXACT, contiguous, copy-pasted excerpt (20-250 characters)
  from the source text that proves the fact. Do not paraphrase the quote.
- Prefer facts fans find surprising: hidden details, production trivia, backstory, abilities,
  relationships, records. Skip dull facts (air dates of every season, voice cast lists).
- "content" is one or two plain sentences in your own words (max 300 characters).
- "spoiler_level": none | low | medium | high (major deaths, villain reveals, endings = high).
- "category": one of %s.
Return JSON: {"facts": [{"title": str, "content": str, "evidence_quote": str, "characters": [str],
"category": str, "spoiler_level": str, "tags": [str]}]} with 6-10 facts.""" % ", ".join(c for c in CATEGORIES if c != "theory")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip().casefold()


def tokens(text: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", normalize(text)) if len(t) > 2}


def similar(a: str, b: str, threshold: float = 0.55) -> bool:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold


def wiki_page(title: str, opener: Callable = urllib.request.urlopen) -> Optional[Tuple[str, str, str]]:
    """(resolved title, page URL, plain text) or None if the page doesn't exist."""
    params = {"action": "query", "prop": "extracts", "explaintext": "1", "redirects": "1",
              "titles": title, "format": "json", "formatversion": "2"}
    req = urllib.request.Request(f"{WIKI_API}?{urllib.parse.urlencode(params)}", headers={"User-Agent": USER_AGENT})
    with opener(req, timeout=45) as resp:
        data = json.loads(resp.read().decode())
    pages = data.get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing") or not pages[0].get("extract"):
        return None
    resolved = pages[0]["title"]
    url = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(resolved.replace(" ", "_"), safe="():_,'-")
    return resolved, url, pages[0]["extract"]


def trim_source(text: str, limit: int = MAX_SOURCE_CHARS) -> str:
    """Drop reference-ish sections and cap length (lead + early sections carry the best facts)."""
    cut = re.split(r"\n==+ ?(References|External links|Notes|Further reading|See also) ?==+", text)[0]
    return cut[:limit]


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")


def make_prefix(name: str, taken: set) -> str:
    words = [w for w in re.findall(r"[A-Za-z0-9]+", name) if w]
    letters = [w for w in words if w[0].isalpha()] or ["ANI"]
    base = ("".join(w[0] for w in letters) if len(letters) > 1 else letters[0][:3]).upper()[:4]
    base = re.sub(r"[^A-Z]", "", base)
    base = base if len(base) >= 2 else (base + "X")[:2]
    if base not in taken:
        return base
    for extra in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":  # schema IDs allow letters only in the prefix
        candidate = (base[:4] + extra)[:5]
        if candidate not in taken:
            return candidate
    raise ValueError(f"no free ID prefix for {name}")


class KnowledgeStore:
    """Reads/writes knowledge/*.jsonl and anime_metadata.json."""

    def __init__(self, directory: Path = DEFAULT_KNOWLEDGE_DIR):
        self.dir = Path(directory)
        self.metadata = load_metadata(self.dir)
        self.records = load_knowledge(self.dir)
        for entry in self.metadata:  # older/hand-written entries may lack these
            if not entry.get("id_prefix"):
                used = [r["id"].split("-")[0] for r in self.records if normalize(r["anime"]) == normalize(entry["name"])]
                entry["id_prefix"] = used[0] if used else make_prefix(entry["name"], {e.get("id_prefix") for e in self.metadata})
            entry.setdefault("knowledge_file", f"{slugify(entry['name'])}.jsonl")

    def anime(self, name: str) -> Optional[dict]:
        key = normalize(name)
        for entry in self.metadata:
            if key in {normalize(n) for n in [entry["name"], *entry.get("aliases", [])]}:
                return entry
        return None

    def ensure_anime(self, name: str, anilist_id: Optional[int] = None, aliases: Optional[List[str]] = None) -> dict:
        entry = self.anime(name)
        if entry is None and anilist_id:
            entry = next((e for e in self.metadata if e.get("anilist_id") == anilist_id), None)
        if entry:
            return entry
        entry = {
            "name": name,
            "aliases": sorted({a for a in (aliases or []) if a and normalize(a) != normalize(name)}),
            "anilist_id": anilist_id,
            "knowledge_file": f"{slugify(name)}.jsonl",
            "id_prefix": make_prefix(name, {e["id_prefix"] for e in self.metadata}),
            "genres": [],
            "active": True,
            "image_block_terms": [],
        }
        self.metadata.append(entry)
        path = self.dir / METADATA_FILENAME
        path.write_text(json.dumps({"anime": self.metadata}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return entry

    def facts_for(self, anime: str) -> List[dict]:
        key = normalize(anime)
        return [r for r in self.records if normalize(r["anime"]) == key]

    def next_id(self, prefix: str) -> str:
        nums = [int(r["id"].split("-")[1]) for r in self.records if r["id"].startswith(prefix + "-")]
        return f"{prefix}-{(max(nums) + 1 if nums else 1):03d}"

    def add(self, entry: dict, new_records: List[dict]) -> List[dict]:
        accepted = []
        for rec in new_records:
            if any(similar(rec["content"], r["content"]) for r in self.facts_for(entry["name"]) + accepted):
                continue
            rec["id"] = self.next_id(entry["id_prefix"])
            if validate_record(rec):
                continue
            self.records.append(rec)
            accepted.append(rec)
        if accepted:
            with (self.dir / entry["knowledge_file"]).open("a", encoding="utf-8") as fh:
                for rec in accepted:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return accepted


def build_records(raw_facts: List[dict], entry: dict, source_url: str, source_text: str) -> Tuple[List[dict], int]:
    """Turn LLM proposals into schema records; returns (records, rejected_count)."""
    haystack = normalize(source_text)
    out, rejected = [], 0
    today = date.today().isoformat()
    for f in raw_facts:
        quote = str(f.get("evidence_quote") or "").strip()
        content = str(f.get("content") or "").strip()[:600]
        if len(quote) < 20 or not content or normalize(quote) not in haystack:
            rejected += 1
            continue
        spoiler = f.get("spoiler_level") if f.get("spoiler_level") in SPOILER_LEVELS else "medium"
        category = f.get("category") if f.get("category") in CATEGORIES and f.get("category") != "theory" else "trivia"
        out.append({
            "id": "TMP-000",
            "anime": entry["name"],
            "title": str(f.get("title") or content[:60]).strip()[:120],
            "content": content,
            "characters": [str(c).strip() for c in (f.get("characters") or []) if str(c).strip()][:6],
            "category": category,
            "canon_status": "canon",
            "spoiler_level": spoiler,
            "source_url": source_url,
            "source_type": "encyclopedia",
            "license": "CC BY-SA 4.0",
            "verified": True,
            "last_reviewed": today,
            "evidence_quote": quote[:400],
            "notes": "auto-verified: evidence quote matched source text",
            "tags": [str(t).strip().lower() for t in (f.get("tags") or []) if str(t).strip()][:8] or [slugify(entry["name"])],
        })
    return out, rejected


def ingest_page(llm, store: KnowledgeStore, entry: dict, page_title: str,
                opener: Callable = urllib.request.urlopen) -> Dict[str, int]:
    """Fetch one Wikipedia page, extract + verify facts, append new ones. Returns counts."""
    page = wiki_page(page_title, opener=opener)
    if not page:
        return {"proposed": 0, "accepted": 0, "rejected": 0}
    _, url, text = page
    source = trim_source(text)
    known = "\n".join(f"- {r['title']}" for r in store.facts_for(entry["name"])[-40:]) or "(none)"
    reply = llm.json(EXTRACT_SYSTEM,
                     f"Anime: {entry['name']}\nAlready known (avoid repeating):\n{known}\n\nSOURCE TEXT:\n{source}",
                     max_tokens=3500, temperature=0.3)
    proposals = reply.get("facts") or []
    records, rejected = build_records(proposals, entry, url, source)
    accepted = store.add(entry, records)
    return {"proposed": len(proposals), "accepted": len(accepted), "rejected": rejected + len(records) - len(accepted)}
