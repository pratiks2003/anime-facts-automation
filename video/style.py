"""Style Spec: every look-and-feel decision of a Reel as data.

Agents (Creative Strategist) choose or invent styles; the renderer only reads this spec.
Code keeps safe defaults and hard limits (clamps) so no style can break a Reel.
"""

from __future__ import annotations

from typing import Any, Dict

DEFAULT_STYLE: Dict[str, Any] = {
    "name": "Cinematic Story",
    "layout": "cinematic",        # cinematic (full-bleed shots) | classic (cards, headlines)
    "caption_mode": "word",       # word (one at a time) | chunk (2-3 words) | none
    "caption_size": 118,          # px
    "caption_y": 0.50,            # vertical position as a fraction of the height
    "keyword_color": True,        # highlight names / emphasis words in the accent colour
    "image_interval": 2.6,        # seconds per shot (cinematic)
    "motion": 0.7,                # 0 = still, 1 = strong zoom/pan
    "grading": "dark",            # none | dark | vivid
    "transition": "glitch",       # cut | flash | glitch
    "show_headline": False,
    "show_tags": False,
    "show_brand": False,
    "show_progress": False,
    "watermark": "SENPAI SECRETS",
    "voice_speed": 1.3,           # Kokoro speed (1.3 ~ 185-195 words/min)
    "music_gain_db": -7.0,        # music bed relative to the voice in pauses (reference Reels sit ~ -7 dB)
    "sfx_density": "medium",      # low | medium | high
    "outro_cta": True,
    "target_seconds": 45,
}

_CHOICES = {
    "layout": ("cinematic", "classic"),
    "caption_mode": ("word", "chunk", "none"),
    "grading": ("none", "dark", "vivid"),
    "transition": ("cut", "flash", "glitch"),
    "sfx_density": ("low", "medium", "high"),
}
_RANGES = {
    "caption_size": (60, 150),
    "caption_y": (0.30, 0.70),
    "image_interval": (1.2, 8.0),
    "motion": (0.0, 1.0),
    "voice_speed": (0.9, 1.5),
    "music_gain_db": (-20.0, -4.0),
    "target_seconds": (15, 90),
}
_BOOLS = ("keyword_color", "show_headline", "show_tags", "show_brand", "show_progress", "outro_cta")


def resolve_style(spec: Any) -> Dict[str, Any]:
    """Merge a (possibly partial, possibly agent-written) spec over the defaults, clamping every value."""
    style = dict(DEFAULT_STYLE)
    if not isinstance(spec, dict):
        return style
    for key, value in spec.items():
        if key in _CHOICES:
            if value in _CHOICES[key]:
                style[key] = value
        elif key in _RANGES:
            try:
                lo, hi = _RANGES[key]
                style[key] = type(DEFAULT_STYLE[key])(min(hi, max(lo, float(value))))
            except (TypeError, ValueError):
                pass
        elif key in _BOOLS:
            style[key] = bool(value)
        elif key in ("name", "watermark") and isinstance(value, str):
            style[key] = value.strip()[:40]
    return style


def style_limits() -> Dict[str, Any]:
    """What an agent may put in a spec (sent to the Strategist / Style Analyst)."""
    return {"choices": {k: list(v) for k, v in _CHOICES.items()}, "ranges": {k: list(v) for k, v in _RANGES.items()},
            "booleans": list(_BOOLS), "text": {"name": "max 40 chars", "watermark": "max 40 chars, '' for none"},
            "defaults": DEFAULT_STYLE}
