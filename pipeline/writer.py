"""Script writer: turns verified facts into a fun, catchy Reel script for a given format.

The LLM writes; this module enforces the rules in code (fact citations, labels,
lengths, hashtags) and asks for one revision with the exact errors if needed.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

from video.script import HEADLINE_MAX, validate_script
from video.visuals import clean_text

FACT_ROLES = {"fact", "twist", "rank"}

FORMAT_GUIDE: Dict[str, Dict[str, str]] = {
    "Fact": {"mode": "fact", "shape": "hook -> 3 'fact' scenes (most surprising last) -> outro",
             "tone": "mind-blown trivia"},
    "Hidden Details": {"mode": "fact", "shape": "hook ('you missed this') -> 3 'fact' scenes with tag 'DETAIL #1..3' -> outro",
                       "tone": "detective, 'rewatch it and you'll see'"},
    "Backstory": {"mode": "fact", "shape": "hook -> 2-3 'fact' scenes told as a story in time order -> 'twist' scene (the emotional reveal) -> outro",
                  "tone": "cinematic, emotional storytelling"},
    "Fan Theory": {"mode": "theory", "shape": "hook -> 1-2 'fact' scenes (real evidence) -> 1-2 'theory' scenes (your speculation, clearly a theory) -> outro asking 'do you buy it?'",
                   "tone": "conspiracy-board energy, but honest that it's a theory"},
    "What If": {"mode": "what_if", "shape": "hook -> 1 'fact' scene (the real setup) -> 2 'what_if' scenes (pure fiction) -> outro",
                "tone": "playful fan-fiction, obviously not canon"},
    "Who Would Win": {"mode": "fact", "shape": "hook (A vs B) -> 'fact' scene on fighter A's abilities -> 'fact' scene on fighter B -> 'what_if' scene imagining the clash and your verdict -> outro: comment your pick",
                      "tone": "hype fight-commentator"},
    "Ranking": {"mode": "ranking", "shape": "hook -> 3 'rank' scenes as a countdown (#3, #2, #1; the renderer numbers them) -> outro asking for their #1",
                "tone": "confident list-video energy"},
    "Quiz": {"mode": "fact", "shape": "hook ('guess the character in 3 clues') -> 3 'fact' scenes with tags 'CLUE 1..3' (vaguest first, never say the name) -> 'twist' scene with tag 'THE ANSWER' revealing it -> outro: 'did you get it before clue 3?'",
             "tone": "game-show"},
    "Behind the Scenes": {"mode": "fact", "shape": "hook -> 3 'fact' scenes about creation/production -> outro",
                          "tone": "insider secrets"},
    "This Week's Drops": {"mode": "fact", "shape": "hook -> one 'fact' scene per show (3-4) with tag like 'AIRS THU' -> outro: 'which one are you watching?'",
                          "tone": "weekly hype roundup"},
    "Why It's Trending": {"mode": "fact", "shape": "hook -> 3 'fact' scenes on the #1 trending show (premise, fan numbers, what makes it different) -> outro",
                          "tone": "'everyone is talking about this' FOMO"},
    "Hype Check": {"mode": "fact", "shape": "hook ('is it worth the hype?') -> 2-3 'fact' scenes with real numbers -> 'what_if' scene tagged 'OUR TAKE' with your opinion -> outro",
                   "tone": "honest reviewer"},
    "Season Preview": {"mode": "fact", "shape": "hook -> one 'fact' scene per upcoming show (3-4) -> outro: 'which are you most hyped for?'",
                       "tone": "season hype trailer"},
    "Just Announced": {"mode": "fact", "shape": "hook -> 2-3 'fact' scenes about upcoming shows with buzz -> outro",
                       "tone": "breaking news excitement"},
}

SYSTEM = """You write viral Instagram Reel scripts for "Senpai Secrets", a fun, catchy anime channel.
Voice: energetic anime fan, punchy short sentences, Gen-Z humor, zero fluff. The first 3 seconds must stop the scroll.

HARD RULES (violations are rejected):
1. Factual claims may ONLY come from the provided FACTS. Every 'fact', 'twist' and 'rank' scene must list the
   ids it uses in "knowledge_ids". Never invent dates, numbers, quotes, power levels or events.
2. 'theory' and 'what_if' scenes are speculation/fiction: phrase them as such ("my theory:", "imagine if").
3. Total narration across all scenes: 85-120 words (about 35-42 seconds). Each scene 1-3 short sentences.
4. "headline": max 5 words, max 40 characters, ALL-CAPS-friendly. "emphasis": 1-2 words copied from the headline.
5. Optional "tag": max 26 characters, shown as a label pill (e.g. "CLUE 2", "AIRS FRI", "OUR TAKE").
6. "image_prompt": a vivid anime illustration of that moment. "image_style": "character" if a named
   character is visible (write the full character name, the anime title and their signature outfit/features),
   otherwise "scene" (scenery, objects, silhouettes; no names needed).
6b. "image_source": "official" to show the show's real official key-art poster or a character's official
   portrait (set "image_subject" to the EXACT show title or character name from the FACTS), or "ai" for an
   illustrated moment. Use "official" for every show scene in news/hype/preview formats, for a hook about a
   whole series, and for a quiz answer reveal; use "ai" for action moments, emotions, what-ifs and theories.
   Always also write "image_prompt" (used as fallback if no good official image exists).
7. "caption": 2-4 short lines with emojis, ending in a question that invites comments. No hashtags in it.
8. "hashtags": exactly 5, most effective mix: #anime, the show (e.g. #onepiece), a character tag, 2 niche/format tags.
9. Avoid major spoilers unless the fact is already marked spoiler low/none.
10. Times: only mention a time if it matters, and write it as "Friday 9 PM IST" (never "India time").
Return ONLY JSON matching this shape:
{"title": str, "mood": "hype"|"mystery"|"sad"|"default",
 "scenes": [{"role": "hook"|"fact"|"twist"|"rank"|"theory"|"what_if"|"outro", "headline": str, "emphasis": [str],
             "tag": str (optional), "narration": str, "knowledge_ids": [str], "image_style": "character"|"scene",
             "image_source": "official"|"ai", "image_subject": str, "image_prompt": str}],
 "caption": str, "hashtags": [str]}
The first scene's role is "hook" and the last is "outro"."""


def build_user_prompt(format_name: str, anime: str, facts: List[dict], topic_hint: str = "",
                      context: str = "") -> str:
    guide = FORMAT_GUIDE[format_name]
    lines = []
    for f in facts:
        spoiler = f.get("spoiler_level", "none")
        chars = ", ".join(f.get("characters") or [])
        lines.append(f"[{f['id']}] ({f['anime']}; spoiler {spoiler}; {chars}) {f['title']}: {f['content']}")
    parts = [
        f"FORMAT: {format_name} (mode {guide['mode']})",
        f"SHAPE: {guide['shape']}",
        f"TONE: {guide['tone']}",
        f"MAIN ANIME: {anime}",
    ]
    if context:
        parts.append(f"CONTEXT: {context}")
    if topic_hint:
        parts.append(f"TOPIC REQUESTED BY THE CHANNEL OWNER: {topic_hint}")
    parts.append("FACTS (cite by id; pick the most surprising ones, you don't need all):\n" + "\n".join(lines))
    return "\n".join(parts)


def check_script(script: dict, allowed_ids: set) -> List[str]:
    errors = validate_script(script)
    for i, scene in enumerate(script.get("scenes", []), start=1):
        role = scene.get("role")
        ids = scene.get("knowledge_ids") or []
        unknown = [k for k in ids if k not in allowed_ids]
        if unknown:
            errors.append(f"scene {i}: unknown knowledge_ids {unknown} (only cite the provided FACTS)")
        if role in FACT_ROLES and not ids:
            errors.append(f"scene {i}: '{role}' scenes must cite at least one knowledge id")
        if not scene.get("image_prompt"):
            errors.append(f"scene {i}: image_prompt is required")
        if len(str(scene.get("tag", ""))) > 26:
            errors.append(f"scene {i}: tag longer than 26 characters")
    if len(script.get("hashtags") or []) != 5:
        errors.append("hashtags must contain exactly 5 tags")
    return errors


def normalise(raw: dict, script_id: str, anime: str, format_name: str) -> dict:
    """Fill in fields the model doesn't own and tidy small issues before validation."""
    script = dict(raw)
    script["id"] = script_id
    script["anime"] = anime
    script["mode"] = FORMAT_GUIDE[format_name]["mode"]
    script["mood"] = script.get("mood") if script.get("mood") in ("hype", "mystery", "sad", "default") else "hype"
    script["format"] = format_name
    tags = []
    for tag in script.get("hashtags") or []:
        tag = "#" + str(tag).lstrip("#").replace(" ", "")
        if tag.lower() not in [t.lower() for t in tags] and len(tag) > 1:
            tags.append(tag)
    script["hashtags"] = tags[:5]
    script["caption"] = clean_text(script.get("caption", ""))
    for scene in script.get("scenes") or []:
        for key in ("headline", "narration", "tag"):
            if key in scene:
                scene[key] = clean_text(scene[key])
        headline = str(scene.get("headline", "")).strip()
        scene["headline"] = headline[:HEADLINE_MAX]
        words = {w.strip(".,!?'\"").upper() for w in headline.split()}
        scene["emphasis"] = [e for e in (scene.get("emphasis") or []) if str(e).strip(".,!?'\"").upper() in words][:2]
        scene["knowledge_ids"] = [str(k) for k in (scene.get("knowledge_ids") or [])]
        if scene.get("image_style") not in ("character", "scene"):
            scene["image_style"] = "scene"
        if scene.get("image_source") not in ("ai", "official"):
            scene["image_source"] = "ai"
        scene["image_subject"] = str(scene.get("image_subject") or "")[:120]
        if "tag" in scene and not scene["tag"]:
            scene.pop("tag")
    return script


def write_script(llm, script_id: str, format_name: str, anime: str, facts: List[dict],
                 topic_hint: str = "", context: str = "") -> dict:
    if format_name not in FORMAT_GUIDE:
        raise ValueError(f"unknown format {format_name}")
    if not facts:
        raise ValueError("no verified facts available to write from")
    allowed = {f["id"] for f in facts}
    user = build_user_prompt(format_name, anime, facts, topic_hint, context)
    raw = llm.json(SYSTEM, user, max_tokens=3500, temperature=0.9)
    script = normalise(raw, script_id, anime, format_name)
    errors = check_script(script, allowed)
    if errors:
        revision = (user + "\n\nYOUR PREVIOUS SCRIPT:\n" + json.dumps(raw, ensure_ascii=False)
                    + "\n\nFIX THESE PROBLEMS and return the full corrected JSON:\n- " + "\n- ".join(errors))
        raw = llm.json(SYSTEM, revision, max_tokens=3500, temperature=0.6)
        script = normalise(raw, script_id, anime, format_name)
        errors = check_script(script, allowed)
        if errors:
            raise ValueError("script failed validation after revision: " + "; ".join(errors))
    return script
