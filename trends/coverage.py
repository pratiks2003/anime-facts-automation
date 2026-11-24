"""Match trending anime against the knowledge base to find coverage gaps."""

from __future__ import annotations

from collections import Counter
from typing import Dict, List

from rag.knowledge import build_alias_index, normalize_name

from .anilist import TrendingAnime


def annotate_coverage(trending: List[TrendingAnime], metadata: List[dict], records: List[dict]) -> List[dict]:
    """Attach knowledge-base coverage to each trending anime.

    Matching uses the AniList ID first (exact), then title/alias names.
    """
    by_id: Dict[int, str] = {e["anilist_id"]: e["name"] for e in metadata if e.get("anilist_id")}
    alias_index = build_alias_index(metadata)
    total = Counter(r["anime"] for r in records)
    verified = Counter(r["anime"] for r in records if r["verified"])

    annotated = []
    for item in trending:
        name = by_id.get(item.anilist_id)
        if name is None:
            for candidate in (item.title, item.title_romaji):
                if candidate and normalize_name(candidate) in alias_index:
                    name = alias_index[normalize_name(candidate)]
                    break
        row = item.to_dict()
        row["knowledge_anime"] = name
        row["knowledge_records"] = total[name] if name else 0
        row["verified_records"] = verified[name] if name else 0
        row["has_knowledge"] = bool(name and verified[name])
        annotated.append(row)
    return annotated
