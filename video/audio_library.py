"""Licensed audio library (assets/audio_library.json; files live in GitHub release assets).

- Music: per-mood CC BY / CC0 tracks. The Music Supervisor agent picks among candidates that fit the
  format; tracks used in recent Reels are excluded so the channel never sounds the same twice in a row.
- SFX: CC0 effects per role (whoosh, glitch, impact, boom, riser, drop, sparkle, ding), placed on the
  actual shot changes with a density chosen by the style.
- Beats: a light numpy beat tracker so shot changes can land on the music's beat.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np

from .media import MediaAsset

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "assets" / "audio_library.json"
DEFAULT_REPO = "pratiks2003/anime-facts-automation"

# Which library moods fit each format (first = best). The Supervisor still makes the final call.
FORMAT_MOODS: Dict[str, List[str]] = {
    "Who Would Win": ["battle", "phonk", "trailer", "epic"],
    "Ranking": ["epic", "trailer", "phonk", "battle"],
    "Backstory": ["emotional", "epic"],
    "Fan Theory": ["mystery", "trailer"],
    "Hidden Details": ["mystery", "playful"],
    "Quiz": ["playful", "mystery"],
    "What If": ["playful", "epic", "phonk"],
    "Behind the Scenes": ["epic", "emotional", "news"],
    "Fact": ["epic", "hype", "phonk", "mystery"],
    "This Week's Drops": ["news", "hype", "phonk"],
    "Why It's Trending": ["news", "hype", "phonk"],
    "Hype Check": ["hype", "phonk", "trailer"],
    "Season Preview": ["trailer", "epic", "hype"],
    "Just Announced": ["news", "hype"],
}
MOOD_ALIASES = {"sad": "emotional", "default": "epic"}


def load_index(path: Path = INDEX) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"music": [], "sfx": []}


def release_url(tag: str, file: str, repo: Optional[str] = None) -> str:
    repo = repo or os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPO
    return f"https://github.com/{repo}/releases/download/{tag}/{file}"


def fetch(entry: dict, tag: str, cache: Path, opener: Callable = urllib.request.urlopen) -> Optional[Path]:
    """Local copy of a library file (build output, cache, or download from the release)."""
    for local in (ROOT / "output" / "audio_library" / entry["file"], cache / entry["file"]):
        if local.exists() and local.stat().st_size > 2000:
            return local
    try:
        req = urllib.request.Request(release_url(tag, entry["file"]), headers={"User-Agent": "anime-facts-automation"})
        with opener(req, timeout=60) as resp:
            raw = resp.read()
        if len(raw) < 2000:
            return None
        cache.mkdir(parents=True, exist_ok=True)
        (cache / entry["file"]).write_bytes(raw)
        return cache / entry["file"]
    except Exception:
        return None


def is_recent(entry: dict, recent: Sequence[str]) -> bool:
    title = (entry.get("title") or "").casefold()
    return bool(title) and any(title in (r or "").casefold() for r in recent)


def music_candidates(format_name: str, mood: str, recent: Sequence[str] = (), index: Optional[dict] = None,
                     limit: int = 12) -> List[dict]:
    index = index if index is not None else load_index()
    moods = list(FORMAT_MOODS.get(format_name, []))
    mood = MOOD_ALIASES.get(mood, mood)
    if mood and mood not in moods:
        moods.append(mood)
    moods = moods or ["epic"]
    out: List[dict] = []
    for rank, m in enumerate(moods):
        for e in index.get("music", []):
            if e.get("key") == m and not is_recent(e, recent) and e not in out:
                out.append(e)
    return out[:limit]


def pick_music(format_name: str, mood: str, seed: str, cache: Path, recent: Sequence[str] = (),
               picker=None, index: Optional[dict] = None) -> Optional[MediaAsset]:
    """Library track for this Reel; None when the Supervisor says nothing fits (or the library is empty)."""
    index = index if index is not None else load_index()
    candidates = music_candidates(format_name, mood, recent, index)
    if not candidates:
        return None
    start = int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(candidates)
    order = candidates[start:] + candidates[:start]
    if picker is not None:
        try:
            chosen = picker([{"id": e["id"], "title": e.get("title"), "tags": e.get("tags", [])[:8],
                              "genres": [e.get("key")], "duration_seconds": int(e.get("duration") or 0)} for e in candidates])
        except Exception:
            chosen = "__error__"
        if chosen is None:
            return None
        if chosen != "__error__":
            order = [e for e in candidates if e["id"] == chosen] + [e for e in order if e["id"] != chosen]
    tag = index.get("music_release", "music-library")
    for e in order:
        path = fetch(e, tag, cache)
        if path:
            return MediaAsset("audio", str(path), e.get("title") or "Untitled", e.get("creator") or "", e.get("license") or "",
                              e.get("source_url") or "", e.get("provider") or "", e["id"])
    return None


# ------------------------------------------------------------------ SFX

DENSITY_EVERY = {"low": 0, "medium": 2, "high": 1}  # a cut effect on every Nth shot change (0 = scene changes only)
CUT_ROLES = {"glitch": "glitch", "flash": "whoosh", "cut": "whoosh"}


def decode(path: Path, sr: int) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def sfx_events(scene_starts: List[float], roles: List[str], shot_starts: List[float], total: float,
               style: dict) -> List[tuple]:
    """[(time, role, gain)] for the whole Reel."""
    events = []
    if scene_starts:
        events.append((0.0, "boom", 0.9))
        events.append((0.02, "impact", 0.6))
    every = DENSITY_EVERY.get(style.get("sfx_density", "medium"), 2)
    cut = CUT_ROLES.get(style.get("transition", "glitch"), "whoosh")
    scene_set = {round(s, 2) for s in scene_starts[1:]}
    for i, t in enumerate(shot_starts[1:], start=1):
        if round(t, 2) in scene_set:
            events.append((max(0.0, t - 0.08), "whoosh", 0.55))
        elif every and i % every == 0:
            events.append((max(0.0, t - 0.03), cut, 0.35))
    for start, role in zip(scene_starts, roles):
        if role in ("rank", "twist"):
            events.append((start + 0.05, "impact", 0.55))
        if role == "outro":
            events.append((max(0.0, start - 1.2), "riser", 0.4))
            events.append((start, "sparkle", 0.45))
    return sorted(e for e in events if e[0] < total - 0.2)


def build_sfx_track(total: float, events: List[tuple], seed: str, cache: Path, sr: int = 48000,
                    index: Optional[dict] = None) -> Optional[np.ndarray]:
    """Mix library SFX at the event times; None if the library is unavailable (caller falls back to synth SFX)."""
    index = index if index is not None else load_index()
    by_role: Dict[str, List[dict]] = {}
    for e in index.get("sfx", []):
        by_role.setdefault(e["key"], []).append(e)
    if not by_role:
        return None
    rng = random.Random(seed)
    tag = index.get("sfx_release", "sfx-library")
    clips: Dict[str, np.ndarray] = {}
    track = np.zeros(int(total * sr) + sr, dtype=np.float32)
    placed = 0
    last_variant: Dict[str, str] = {}
    for t, role, gain in events:
        options = by_role.get(role) or by_role.get("whoosh") or []
        if not options:
            continue
        choices = [o for o in options if o["file"] != last_variant.get(role)] or options
        entry = rng.choice(choices)
        last_variant[role] = entry["file"]
        if entry["file"] not in clips:
            path = fetch(entry, tag, cache)
            if not path:
                continue
            try:
                clip = decode(path, sr)[: int(2.5 * sr)]
            except Exception:
                continue
            peak = float(np.max(np.abs(clip))) or 1.0
            fade = min(len(clip), int(0.25 * sr))
            clip = clip / peak
            clip[-fade:] *= np.linspace(1, 0, fade)
            clips[entry["file"]] = clip
        clip = clips[entry["file"]]
        a = int(t * sr)
        b = min(len(track), a + len(clip))
        track[a:b] += clip[: b - a] * gain * 0.5
        placed += 1
    if not placed:
        return None
    return np.clip(track[: int(total * sr)], -1, 1)


# ------------------------------------------------------------------ beats

def beat_times(path: Path, sr: int = 11025, hop: int = 256) -> List[float]:
    """Beat grid of a music file (onset autocorrelation tempo + best phase). Empty list if unclear."""
    try:
        y = decode(path, sr)[: sr * 120]
    except Exception:
        return []
    if y.size < sr * 5:
        return []
    frames = y[: y.size // hop * hop].reshape(-1, hop)
    energy = np.log1p(np.sqrt(np.mean(frames ** 2, axis=1)) * 100)
    onset = np.maximum(0, np.diff(energy, prepend=energy[0]))
    onset -= onset.mean()
    fps = sr / hop
    lo, hi = int(fps * 60 / 180), int(fps * 60 / 70)  # 70-180 BPM
    ac = np.correlate(onset, onset, mode="full")[onset.size - 1:]
    if ac[0] <= 0 or hi >= ac.size:
        return []
    lag = lo + int(np.argmax(ac[lo:hi]))
    if ac[lag] / ac[0] < 0.05:
        return []  # no steady pulse (ambient / rubato)
    phase = int(np.argmax([onset[p::lag].sum() for p in range(lag)]))
    return [round(float(f / fps), 3) for f in range(phase, onset.size, lag)]


def snap_to_beats(times: List[float], beats: List[float], window: float = 0.3, min_gap: float = 0.8) -> List[float]:
    """Move each boundary to the nearest beat within `window`, keeping shots at least `min_gap` long."""
    if not beats:
        return list(times)
    arr = np.array(beats)
    out: List[float] = []
    for t in times:
        nearest = float(arr[np.argmin(np.abs(arr - t))])
        candidate = nearest if abs(nearest - t) <= window else t
        if out and candidate - out[-1] < min_gap:
            candidate = t if t - out[-1] >= min_gap else out[-1] + min_gap
        out.append(round(candidate, 3))
    return out
