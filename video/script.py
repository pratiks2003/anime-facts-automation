"""Video script format (the JSON the content agent produces) and validation.

{
  "id": "OP-REEL-0001",
  "anime": "One Piece",
  "mode": "fact",                      # fact | theory | what_if | ranking
  "title": "Luffy can't do this",
  "voice": "am_fenrir", "speed": 1.1,  # optional
  "scenes": [
    {"role": "hook", "headline": "...", "narration": "...", "emphasis": ["word"]},
    {"role": "fact", "headline": "...", "narration": "...", "knowledge_ids": ["OP-002"]},
    {"role": "outro", "headline": "...", "narration": "..."}
  ],
  "caption": "...", "hashtags": ["#onepiece"]
}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

MODES = ("fact", "theory", "what_if", "ranking")
ROLES = ("hook", "fact", "twist", "theory", "what_if", "rank", "outro")
HEADLINE_MAX = 48
MIN_WORDS, MAX_WORDS = 60, 140  # roughly 25-50 s at speed 1.1


class ScriptError(ValueError):
    pass


def validate_script(script: dict) -> List[str]:
    errors: List[str] = []
    for key in ("id", "anime", "mode", "title", "scenes", "caption", "hashtags"):
        if key not in script:
            errors.append(f"missing '{key}'")
    if errors:
        return errors

    if script["mode"] not in MODES:
        errors.append(f"mode must be one of {MODES}")
    scenes = script["scenes"]
    if not isinstance(scenes, list) or not 3 <= len(scenes) <= 8:
        return errors + ["scenes must be a list of 3-8 scenes"]
    if scenes[0].get("role") != "hook":
        errors.append("first scene must have role 'hook'")
    if scenes[-1].get("role") != "outro":
        errors.append("last scene must have role 'outro'")

    words = 0
    for i, scene in enumerate(scenes):
        where = f"scene {i + 1}"
        if scene.get("role") not in ROLES:
            errors.append(f"{where}: role must be one of {ROLES}")
        headline = scene.get("headline", "")
        narration = scene.get("narration", "")
        if not headline.strip():
            errors.append(f"{where}: headline is empty")
        elif len(headline) > HEADLINE_MAX:
            errors.append(f"{where}: headline longer than {HEADLINE_MAX} chars")
        if not narration.strip():
            errors.append(f"{where}: narration is empty")
        words += len(narration.split())
        queries = scene.get("visual_queries", [])
        if not isinstance(queries, list) or not all(isinstance(q, str) and q.strip() for q in queries):
            errors.append(f"{where}: visual_queries must be a list of non-empty strings")
        if scene.get("image_style", "scene") not in ("scene", "character"):
            errors.append(f"{where}: image_style must be 'scene' or 'character'")
        if scene.get("image_source", "ai") not in ("ai", "official"):
            errors.append(f"{where}: image_source must be 'ai' or 'official'")
        if len(str(scene.get("image_subject", ""))) > 120:
            errors.append(f"{where}: image_subject longer than 120 characters")
        prompt = scene.get("image_prompt", "")
        if not isinstance(prompt, str) or len(prompt) > 1500:
            errors.append(f"{where}: image_prompt must be a string under 1500 chars")
        if scene.get("role") == "theory" and script["mode"] != "theory":
            errors.append(f"{where}: theory scenes are only allowed in mode 'theory'")

    if not MIN_WORDS <= words <= MAX_WORDS:
        errors.append(f"narration has {words} words; keep it between {MIN_WORDS} and {MAX_WORDS} (~30-45 s)")
    hashtags = script["hashtags"]
    if not isinstance(hashtags, list) or not all(isinstance(h, str) and h.startswith("#") for h in hashtags):
        errors.append("hashtags must be a list of strings starting with '#'")
    elif len(hashtags) > 10:
        errors.append("use at most 10 hashtags")
    return errors


def load_script(path: str) -> dict:
    script = json.loads(Path(path).read_text(encoding="utf-8"))
    errors = validate_script(script)
    if errors:
        raise ScriptError(f"{path}: " + "; ".join(errors))
    return script


def knowledge_ids(script: dict) -> List[str]:
    ids: List[str] = []
    for scene in script["scenes"]:
        for kid in scene.get("knowledge_ids", []):
            if kid not in ids:
                ids.append(kid)
    return ids


def full_caption(script: dict) -> str:
    return f"{script['caption'].rstrip()}\n\n{' '.join(script['hashtags'])}"
