"""Shot resolution for the cinematic layout: turn each scene's shot list (from the Visual
Director, or derived from the scene) into concrete image files. It never leaves a shot blank.

Chain per shot: fan wiki (screenshots / manga panels) -> official art -> the scene's primary
asset (official/AI/photo) -> show key art -> any other image of this Reel.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

from .wiki_art import FandomWiki


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def anime_entry(anime: str) -> dict:
    try:
        from rag.knowledge import load_metadata, normalize_name
        key = normalize_name(anime)
        for entry in load_metadata():
            names = [entry["name"], *entry.get("aliases", [])]
            if any(normalize_name(n) == key for n in names):
                return entry
    except Exception:
        pass
    return {}


def record_characters(scene: dict, extra_records: Optional[List[dict]] = None) -> List[str]:
    """Characters named by the facts this scene cites (good wiki subjects when none is given)."""
    ids = set(scene.get("knowledge_ids") or [])
    if not ids:
        return []
    try:
        from rag.knowledge import load_knowledge
        records = load_knowledge() + list(extra_records or [])
    except Exception:
        records = list(extra_records or [])
    out: List[str] = []
    for r in records:
        if r.get("id") in ids:
            out += [c for c in r.get("characters", []) if c not in out]
    return out


def shot_plan(scene: dict, script: dict, count: int, characters: List[str] = ()) -> List[dict]:
    """The Director's shots for a scene, padded/derived to `count` entries."""
    shots = [s for s in (scene.get("shots") or []) if isinstance(s, dict) and (s.get("subject") or s.get("prompt"))]
    if not shots:
        subjects = [x for x in [(scene.get("image_subject") or "").strip(), *characters] if x] or [script["anime"]]
        shots = [{"subject": x, "source": "wiki"} for x in subjects]
    while len(shots) < count:
        shots.append(dict(shots[len(shots) % len(shots)]))
    return shots[:max(count, 1)]


def resolve_shots(script: dict, narration, style: dict, primary: List[Optional[str]], cache: Path,
                  backup: List[str], extra_records: Optional[List[dict]] = None,
                  last_resort: List[str] = ()) -> List[List[str]]:
    """Image paths per scene. primary[i] is the scene's already-verified asset (or None)."""
    entry = anime_entry(script["anime"])
    wiki = None
    if script["anime"] != "Anime":
        wiki = FandomWiki.for_anime(script["anime"], entry.get("aliases", []), entry.get("fandom", ""))
    log(f"fan wiki: {wiki.subdomain + '.fandom.com' if wiki else 'none found'}")
    used: set = set()
    per_subject: dict = {}
    result: List[List[str]] = []
    for i, (scene, timing) in enumerate(zip(script["scenes"], narration.scenes)):
        count = max(1, round(max(0.5, timing.end - timing.start) / style["image_interval"]))
        paths: List[str] = []
        if primary[i]:
            paths.append(primary[i])
        characters = record_characters(scene, extra_records)
        plan = shot_plan(scene, script, count, characters)
        # wiki subjects to try for this scene: the planned ones first, then cited characters, then the show
        subjects = [s.get("subject") for s in plan if s.get("source", "wiki") in ("wiki", "official", "any", "")]
        subjects = [x for x in dict.fromkeys([*subjects, *characters, script["anime"]]) if x]
        for subject in subjects if wiki else []:
            if len(paths) >= count:
                break
            if subject not in per_subject:
                per_subject[subject] = [str(p) for p in wiki.images(subject, cache, limit=6)]
            for p in per_subject[subject]:
                if len(paths) >= count:
                    break
                if p not in used and p not in paths:
                    paths.append(p)
                    used.add(p)
        if len(paths) < count:  # never blank: reuse this scene's images, then the show's art, then the Reel's
            pool = (paths[:] or [p for p in backup if p] or [p for row in result for p in row]
                    or [p for p in last_resort if p])
            k = 0
            while len(paths) < count and pool:
                paths.append(pool[k % len(pool)])
                k += 1
        result.append(paths)
        log(f"scene {i + 1}: {len(paths)} shot(s) ({count} wanted)")
    return result
