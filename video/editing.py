"""Editor capabilities: what the video editor can do, as data the agents can read and request.

The Showrunner agents (Planner / Editor) send:
  "style": {any Style Spec key: value}               -> whole-Reel look (video/style.py, clamped)
  "edits": {"hook" | "fact" | "twist" | "theory" | "what_if" | "rank" | "outro" | "all":
            [ "effect_name" or {"fx": "effect_name", "text": "...", "color": "..."} ]}
Anything the editor cannot do is reported back as an unsupported request (a feature request), never silently dropped.

  python -m video.editing   # print the capability manifest (JSON)
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Tuple

from .style import style_limits

ROLES = ("hook", "fact", "twist", "theory", "what_if", "rank", "outro", "all")

# name -> what it does (shown to the agents); params: optional keys the effect understands
EFFECTS: Dict[str, Dict[str, Any]] = {
    "impact_text": {"does": "Huge slam-in text over the scene's first ~1.4 s (e.g. 'EXPOSED', 'NOBODY NOTICED'). "
                            "Uses 'text', else the scene headline.", "params": ["text", "color"]},
    "versus": {"does": "Split screen: the scene's first two pictures stacked top/bottom with a VS badge "
                       "(for Who Would Win / comparisons). Needs 2+ pictures in the scene.", "params": []},
    "freeze": {"does": "Freeze-frame record-scratch: the first 0.7 s holds still, desaturated, with a white flash "
                       "and a slow push-in (for 'wait... what?' moments).", "params": []},
    "rank_badge": {"does": "Big rank number badge in the top corner for the whole scene (e.g. '#3').",
                   "params": ["text"]},
    "bw": {"does": "Black-and-white grade for the whole scene (flashback, death, past).", "params": []},
    "red_tint": {"does": "Blood-red danger grade for the whole scene (villain, threat, horror).", "params": []},
    "blue_tint": {"does": "Cold blue grade for the whole scene (sad, lonely, mystery).", "params": []},
    "letterbox": {"does": "Cinematic black bars for the whole scene (epic / backstory moments).", "params": []},
    "zoom_punch": {"does": "Hard zoom punch as the scene starts.", "params": []},
    "glitch": {"does": "Glitch hit (RGB split + slices) as the scene starts, whatever the global transition.",
               "params": []},
    "flash": {"does": "White flash as the scene starts.", "params": []},
    "shake": {"does": "Strong camera shake as the scene starts.", "params": []},
    "slow_zoom": {"does": "Strong continuous push-in on every picture of the scene (tension).", "params": []},
}
COLORS = {"red": (235, 40, 40), "yellow": (255, 214, 0), "white": (255, 255, 255), "cyan": (0, 220, 255),
          "green": (60, 230, 90), "pink": (255, 70, 170), "orange": (255, 140, 0)}


def manifest() -> Dict[str, Any]:
    """Everything the agents may ask for, with limits. Sent to Salesforce for the Showrunner agents."""
    return {
        "how_to_request": "submit_plan / remake_reel take 'style' (JSON object of Style Spec keys) and 'edits' "
                          "(JSON object: role -> list of effects). Roles: " + ", ".join(ROLES) + ".",
        "effects": {k: v["does"] + (f" Params: {', '.join(v['params'])}." if v["params"] else "") for k, v in EFFECTS.items()},
        "effect_colors": sorted(COLORS),
        "style": style_limits(),
        "always_on": ["word-by-word captions synced to the voice", "cuts snapped to the music beat",
                      "official clips mixed with stills (clip_ratio)", "every picture checked for wrong show / "
                      "duplicates / text / photos before use", "designed cover picked by the quality checker",
                      "AI voice (speed from style.voice_speed)", "music from the licensed library, ducked under the voice"],
        "cannot_do_yet": ["custom fonts per Reel", "user-uploaded footage", "lip-sync", "music chosen by name",
                          "audio effects beyond whoosh/impact/pop", "any request not listed here: ask anyway - "
                          "it is logged as a feature request for the developers"],
    }


def parse_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def normalize_edits(edits: Any) -> Tuple[Dict[str, List[dict]], List[str]]:
    """-> (role -> [{"fx", "text"?, "color"?}], unsupported requests as readable strings)."""
    edits = parse_json(edits)
    out: Dict[str, List[dict]] = {}
    unsupported: List[str] = []
    if not isinstance(edits, dict):
        if edits:
            unsupported.append(f"edits not understood: {str(edits)[:200]}")
        return out, unsupported
    for role, items in edits.items():
        role_key = str(role).strip().lower().replace(" ", "_")
        if role_key not in ROLES:
            unsupported.append(f"unknown scene role '{role}' (use {', '.join(ROLES)})")
            continue
        for item in items if isinstance(items, list) else [items]:
            fx = dict(item) if isinstance(item, dict) else {"fx": item}
            name = str(fx.get("fx") or fx.get("effect") or "").strip().lower().replace(" ", "_").replace("-", "_")
            if name not in EFFECTS:
                unsupported.append(f"{role_key}: '{fx.get('fx') or fx.get('effect') or item}'")
                continue
            clean = {"fx": name}
            if fx.get("text"):
                clean["text"] = str(fx["text"]).strip()[:40]
            if str(fx.get("color") or "").lower() in COLORS:
                clean["color"] = str(fx["color"]).lower()
            out.setdefault(role_key, []).append(clean)
    return out, unsupported


def style_overrides(style: dict, requested: Any) -> Tuple[dict, List[str]]:
    """Merge agent style requests over the chosen style (clamped); unknown keys become feature requests."""
    from .style import DEFAULT_STYLE, resolve_style

    requested = parse_json(requested)
    if not isinstance(requested, dict) or not requested:
        return style, []
    unknown = [f"style.{k}" for k in requested if k not in DEFAULT_STYLE]
    merged = resolve_style({**style, **{k: v for k, v in requested.items() if k in DEFAULT_STYLE}})
    return merged, unknown


def apply_edits(script: dict, edits: Dict[str, List[dict]]) -> int:
    """Attach effects to scenes by role ('all' = every scene). Returns how many were placed."""
    placed = 0
    for scene in script.get("scenes") or []:
        wanted = list(edits.get("all", [])) + list(edits.get(str(scene.get("role")), []))
        if wanted:
            existing = scene.setdefault("fx", [])
            for fx in wanted:
                if fx not in existing:
                    existing.append(fx)
                    placed += 1
    return placed


def scene_fx(scene: dict) -> Dict[str, dict]:
    """fx name -> params for one scene (writer-chosen 'fx' entries are validated the same way)."""
    out: Dict[str, dict] = {}
    for item in scene.get("fx") or []:
        fx = item if isinstance(item, dict) else {"fx": item}
        name = str(fx.get("fx") or "").lower()
        if name in EFFECTS:
            out[name] = fx
    return out


if __name__ == "__main__":
    json.dump(manifest(), sys.stdout, indent=2)
    sys.exit(0)
