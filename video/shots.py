"""Shot resolution for the cinematic layout: turn each scene's shot list (from the Visual
Director, or derived from the scene) into concrete image files. It never leaves a shot blank.

Chain per shot: the scene's own asset -> fan-wiki pictures of each subject -> AniList character
portrait -> the show's episode screenshots / trailer frame -> key art -> stock (last resort).
Every candidate passes the Image Inspector (video/image_check.py) first; nothing repeats.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterator, List, Optional

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


def named_in(text: str, characters: List[str]) -> List[str]:
    """Only characters the narration actually names (a fact about Robin's bounty also lists Doflamingo and
    Kaido, but showing them while the voice talks about Robin is wrong)."""
    words = {w for w in re.findall(r"[a-z0-9]+", (text or "").casefold())}
    out = []
    for c in characters:
        parts = [w for w in re.findall(r"[a-z0-9]+", c.casefold()) if len(w) > 2 and w not in ("the", "von")]
        if parts and any(w in words for w in parts):
            out.append(c)
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


def name_matches(subject: str, name: str, aliases: List[str] = ()) -> bool:
    """'Monkey D. Luffy' ~ AniList 'Luffy Monkey'; 'Rain' ~ 'Rain Garland'."""
    words = {w for w in re.findall(r"[a-z0-9]+", subject.casefold()) if len(w) > 2}
    for n in [name, *aliases]:
        if words & {w for w in re.findall(r"[a-z0-9]+", (n or "").casefold()) if len(w) > 2}:
            return True
    return False


class ShotSources:
    """Candidate pictures for a subject, best source first, downloaded lazily."""

    def __init__(self, script: dict, cache: Path):
        self.anime, self.cache = script["anime"], cache
        entry = anime_entry(self.anime)
        self.wiki = None
        self.gallery: dict = {"episodes": [], "characters": [], "trailer": None}
        if self.anime != "Anime":
            self.wiki = FandomWiki.for_anime(self.anime, entry.get("aliases", []), entry.get("fandom", ""))
            try:
                from .official_art import anilist_gallery

                self.gallery = anilist_gallery(self.anime)
            except Exception:
                pass
        self._wiki: dict = {}
        from .google_images import GoogleImages

        self.google = GoogleImages.from_env()
        log(f"sources: fan wiki {self.wiki.subdomain + '.fandom.com' if self.wiki else 'none'}; AniList "
            f"{len(self.gallery['episodes'])} episode shots, {len(self.gallery['characters'])} characters, "
            f"trailer {'yes' if self.gallery['trailer'] else 'no'}")

    def _download(self, url: str) -> Optional[str]:
        from .official_art import download_art

        path = download_art(url, self.cache)
        return str(path) if path else None

    def for_subject(self, subject: str) -> Iterator[str]:
        if self.wiki and subject:
            if subject not in self._wiki:
                self._wiki[subject] = [str(x) for x in self.wiki.images(subject, self.cache, limit=8)]
            yield from self._wiki[subject]
        for name, aliases, url in self.gallery["characters"]:
            if subject and name_matches(subject, name, aliases):
                path = self._download(url)
                if path:
                    yield path

    def for_scene(self, scene: str, subject: str = "") -> Iterator[str]:
        """The exact moment a sentence describes: fan-wiki event pictures, then Google image search."""
        if not scene:
            return
        if self.wiki:
            yield from (str(x) for x in self.wiki.scene_images(scene, self.cache, limit=3))
        if self.google:
            query = f"{self.anime} {scene} anime screenshot"
            yield from (str(x) for x in self.google.images(query, self.cache, limit=3))

    def general(self) -> Iterator[str]:
        """Pictures of the show itself: official-trailer stills, episode screenshots, main characters."""
        if self.gallery.get("trailer_id"):
            from .trailer_frames import trailer_frames

            frames = trailer_frames(self.gallery["trailer_id"], self.cache)
            yield from (str(f) for f in frames)
            if not frames:  # YouTube blocks downloads from cloud runners: its own frame thumbnails still work
                tid = self.gallery["trailer_id"]
                for n in ("maxres1", "maxres2", "maxres3"):
                    path = self._download(f"https://i.ytimg.com/vi/{tid}/{n}.jpg")
                    if path:
                        yield path
        urls = list(self.gallery["episodes"])
        if self.gallery["trailer"]:
            urls.insert(0, self.gallery["trailer"])
        urls += [url for _, _, url in self.gallery["characters"]]
        for url in urls:
            path = self._download(url)
            if path:
                yield path


def resolve_shots(script: dict, narration, style: dict, primary: List[Optional[str]], cache: Path,
                  backup: List[str], extra_records: Optional[List[dict]] = None,
                  last_resort: List[str] = (), inspector=None, clips: Optional[List[str]] = None,
                  banned: Optional[set] = None) -> List[List[str]]:
    """Image paths per scene. Every picture is inspected before use and never repeated;
    when a scene runs out of distinct good pictures it gets fewer, longer shots instead."""
    from .image_check import Inspector

    inspector = inspector or Inspector.create()
    sources = ShotSources(script, cache)
    reel_characters: List[str] = []  # everyone this Reel talks about: variety for hook/outro shots
    all_narration = " ".join(sc.get("narration", "") for sc in script["scenes"])
    for scene in script["scenes"]:
        reel_characters += [c for c in named_in(all_narration, record_characters(scene, extra_records))
                            if c not in reel_characters]
    general = sources.general()
    rejections: List[str] = []
    banned = set(banned or ())  # pictures the Visual QA agent rejected
    clip_pool = [c for c in (clips or []) if c not in banned]  # each clip is used once
    ratio = float(style.get("clip_ratio", 0.5))

    bad_paths: set = set()

    small: List[str] = []  # tiny pictures (e.g. 230px portraits): only if nothing better is left

    def take(path: Optional[str], subject: str, expect: str = "anime", description: str = "") -> bool:
        if not path or path in bad_paths or path in small or path in banned:
            return False
        try:
            from PIL import Image

            with Image.open(path) as im:
                if min(im.size) < 400:
                    small.append(path)
                    return False
        except Exception:
            pass
        verdict = inspector.inspect(Path(path), subject, expect, description)
        if not verdict.ok:
            bad_paths.add(path)
        if not verdict.ok and "duplicate" not in verdict.reason:
            rejections.append(f"{subject or 'show'}: {Path(path).name} - {verdict.reason}")
        return verdict.ok

    result: List[List[str]] = []
    for i, (scene, timing) in enumerate(zip(script["scenes"], narration.scenes)):
        count = max(1, round(max(0.5, timing.end - timing.start) / style["image_interval"]))
        paths: List[str] = []
        if primary[i]:
            ai = Path(primary[i]).name.startswith("ai_")
            if take(primary[i], scene.get("image_subject", ""), "scene" if ai else "anime", scene.get("image_prompt", "")):
                paths.append(primary[i])
        clip_slots = min(len(clip_pool), round(count * ratio))
        if i == 0 and clip_pool and ratio > 0:
            clip_slots = max(1, clip_slots)  # open on motion
        if i == 0 and clip_slots:
            paths.insert(0, clip_pool.pop(0))
            clip_slots -= 1
        still_target = count - clip_slots
        characters = named_in(scene.get("narration", ""), record_characters(scene, extra_records))
        plan = shot_plan(scene, script, count, characters)
        subjects = [s.get("subject") for s in plan if s.get("source", "wiki") in ("wiki", "official", "any", "")]
        subjects = [x for x in dict.fromkeys([*subjects, *characters, script["anime"], *reel_characters]) if x]
        for shot in plan:  # 1) the exact moment each sentence describes
            if len(paths) >= still_target:
                break
            for path in sources.for_scene(str(shot.get("scene") or ""), shot.get("subject", "")):
                if path not in paths and take(path, shot.get("subject", "")):
                    paths.append(path)
                    break  # one picture per described moment; the next shot gets its own
        for subject in subjects:  # 2) the people / things named
            for path in sources.for_subject(subject):
                if len(paths) >= still_target:
                    break
                if path not in paths and take(path, subject):
                    paths.append(path)
            if len(paths) >= still_target:
                break
        scenes_left = max(1, len(script["scenes"]) - i)
        fair_share = max(clip_slots, -(-len(clip_pool) // scenes_left))  # spread clips over the whole Reel
        added = 0
        while len(paths) < count and clip_pool and added < fair_share:  # anime motion between the stills
            paths.append(clip_pool.pop(0))
            added += 1
        for path in general:  # the show's own screenshots / trailer / characters
            if len(paths) >= count:
                break
            if take(path, script["anime"]):
                paths.append(path)
        if len(paths) < count:
            for path in [*backup, *last_resort]:
                if len(paths) >= count:
                    break
                if path and path not in paths and take(path, script["anime"], "anime" if path in backup else "scene",
                                                       scene.get("image_prompt", "")):
                    paths.append(path)
        while len(paths) < count and small:  # small but correct pictures, framed (not blown up)
            path = small.pop(0)
            if inspector.inspect(Path(path), scene.get("image_subject", "")).ok:
                paths.append(path)
        if not paths:  # nothing new left: reuse an earlier picture, never the one just shown
            earlier = [p for row in result for p in row]
            previous = result[-1][-1] if result and result[-1] else None
            reuse = [p for p in earlier if p != previous and p not in banned] or earlier
            if reuse:
                paths.append(reuse[i % len(reuse)])
        result.append(paths)
        n_clips = sum(1 for x in paths if Path(x).is_dir())
        log(f"scene {i + 1}: {len(paths)} distinct shot(s) ({count} wanted, {n_clips} clips)")
    log(f"image inspector: {inspector.accepted} accepted, {len(rejections)} rejected")
    for r in rejections[:12]:
        log(f"  rejected {r}")
    resolve_shots.rejections = rejections
    return result
