"""Agentforce script studio client: Salesforce agents (Prompt Builder + Einstein GPT-4.1)
write, direct, fact-check and caption the script. See force-app ScriptStudio.cls."""

from __future__ import annotations

from typing import List, Optional

from .writer import FORMAT_GUIDE, check_script, normalise


class StudioError(RuntimeError):
    pass


def enforce_official_subjects(script: dict, anime: str, facts: List[dict]) -> None:
    """Official key art only exists for the series or a named character; anything else becomes AI art."""
    import re

    def norm(x: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (x or "").casefold()).strip()

    known = {norm(anime)} | {norm(f.get("anime", "")) for f in facts}
    for f in facts:
        known |= {norm(c) for c in f.get("characters") or []}
    known.discard("")
    for scene in script.get("scenes") or []:
        if scene.get("image_source") != "official":
            continue
        subject = norm(scene.get("image_subject", ""))
        if not subject or not any(subject == k or subject in k or k in subject for k in known):
            scene["image_source"] = "ai"


def agent_script(sf, content_id: str, format_name: str, anime: str, facts: List[dict], context: str = "",
                 topic: str = "", lessons: Optional[List[str]] = None) -> dict:
    """Return {"script", "quality", "production", "fact_check", "calls"} with a code-validated script."""
    payload = {
        "content_id": content_id, "format": format_name, "guide": FORMAT_GUIDE[format_name], "anime": anime,
        "context": context, "topic": topic, "lessons": lessons or [],
        "facts": [{k: f.get(k) for k in ("id", "anime", "title", "content", "characters", "spoiler_level")} for f in facts],
    }
    result = sf.apex_post("insta/studio/script", payload)
    if not result or "script" not in result:
        raise StudioError(f"studio returned no script: {str(result)[:300]}")
    raw = result["script"]
    for _ in range(3):  # tolerate {"script": {...}} wrapping from the model
        if isinstance(raw, dict) and "scenes" not in raw and isinstance(raw.get("script"), dict):
            raw = {**{k: v for k, v in raw.items() if k != "script"}, **raw["script"]}
    script = normalise(raw, content_id, anime, format_name)
    production = result.get("production") or {}
    if production.get("voice"):
        script["voice"] = production["voice"]
    try:
        speed = float(production.get("speed") or 0)
        if 0.9 <= speed <= 1.25:
            script["speed"] = speed
    except (TypeError, ValueError):
        pass
    if production.get("music_mood") in ("hype", "mystery", "sad", "default"):
        script["mood"] = production["music_mood"]
    if production.get("music_energy") in ("high", "medium", "low"):
        script["music_energy"] = production["music_energy"]
    if production.get("music_keywords"):
        script["music_keywords"] = str(production["music_keywords"])[:80]
    enforce_official_subjects(script, anime, facts)
    errors = check_script(script, {f["id"] for f in facts})
    if errors:
        raise StudioError("studio script failed code rules: " + "; ".join(errors))
    result["script"] = script
    return result
