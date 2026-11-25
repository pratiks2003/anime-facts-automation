"""Fan-wiki (Fandom) images: anime screenshots, manga panels and infobox art for a character,
place or event. One page usually holds dozens of images, which feeds the 2-3 second shot
rhythm of the cinematic layout.

  wiki = FandomWiki.for_anime("One Piece")      # finds onepiece.fandom.com
  paths = wiki.images("Gecko Moria", cache, limit=4)
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request
from io import BytesIO
from pathlib import Path
from typing import Callable, Dict, List, Optional

UA = "anime-facts-automation/0.2 (fan-wiki image lookup)"
MIN_SIDE, MIN_LONG = 420, 700
# merch, games, UI chrome: never good Reel shots
SKIP = re.compile(r"(bounty rush|thousand storm|treasure cruise|pirate warriors|ultimate|storm \d|game|card|figure|"
                  r"merch|logo|icon|symbol|flag|signature|featured|wanted poster|jolly|button|banner ad|sprite|"
                  r"\bvol(ume)?\b.*cover|box ?art|funko|chibi|emoji|\.svg|\.gif)", re.I)
PREFER = re.compile(r"(anime|manga|infobox|episode|chapter|color|colour|portrait|full body|appearance|post|pre)", re.I)
_WIKI_CACHE: Dict[str, Optional[str]] = {}


def _get_json(url: str, opener: Callable) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with opener(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def slug_candidates(anime: str, aliases: List[str] = ()) -> List[str]:
    out: List[str] = []
    for name in [anime, *aliases]:
        if not name or not name.isascii():
            continue
        base = re.sub(r"\b(season|part|cour)\s*\d+.*$|\b\d+(st|nd|rd|th) season.*$|:.*$", "", name, flags=re.I).strip()
        base = re.sub(r"\s+\d+$", "", base)  # "Tensura 4" -> "Tensura"
        words = re.findall(r"[a-z0-9]+", base.lower())
        if not words:
            continue
        for slug in ("".join(words), "-".join(words)):
            if slug not in out:
                out.append(slug)
    return out[:6]


class FandomWiki:
    def __init__(self, subdomain: str, opener: Callable = urllib.request.urlopen):
        self.subdomain = subdomain
        self.api = f"https://{subdomain}.fandom.com/api.php"
        self.opener = opener

    @classmethod
    def for_anime(cls, anime: str, aliases: List[str] = (), explicit: str = "",
                  opener: Callable = urllib.request.urlopen) -> Optional["FandomWiki"]:
        key = explicit or anime
        if key in _WIKI_CACHE:
            sub = _WIKI_CACHE[key]
            return cls(sub, opener) if sub else None
        for sub in ([explicit] if explicit else []) + slug_candidates(anime, list(aliases)):
            try:
                data = _get_json(f"https://{sub}.fandom.com/api.php?action=query&meta=siteinfo&format=json", opener)
            except Exception:
                continue
            if data.get("query", {}).get("general"):
                _WIKI_CACHE[key] = sub
                return cls(sub, opener)
        _WIKI_CACHE[key] = None
        return None

    def _q(self, **params) -> dict:
        params.setdefault("format", "json")
        return _get_json(f"{self.api}?{urllib.parse.urlencode(params)}", self.opener)

    def find_page(self, subject: str) -> Optional[str]:
        data = self._q(action="query", list="search", srsearch=subject, srlimit=5)
        hits = data.get("query", {}).get("search", [])
        if not hits:
            return None
        subject_words = set(re.findall(r"\w+", subject.lower()))
        exact = [h["title"] for h in hits if h["title"].casefold() == subject.casefold()]
        overlap = [h["title"] for h in hits if subject_words & set(re.findall(r"\w+", h["title"].lower()))]
        main = [t for t in overlap if "/" not in t]  # "Luffy" over "Luffy/Abilities and Powers"
        return (exact or main or overlap or [None])[0]

    def page_images(self, title: str) -> List[dict]:
        data = self._q(action="query", titles=title, generator="images", gimlimit=100, redirects=1,
                       prop="imageinfo", iiprop="url|size|mime")
        out = []
        for page in (data.get("query", {}).get("pages") or {}).values():
            info = (page.get("imageinfo") or [{}])[0]
            name = page.get("title", "")
            w, h = info.get("width", 0), info.get("height", 0)
            if not info.get("url") or info.get("mime") not in ("image/png", "image/jpeg", "image/webp"):
                continue
            if min(w, h) < MIN_SIDE or max(w, h) < MIN_LONG or SKIP.search(name):
                continue
            out.append({"name": name, "url": info["url"], "width": w, "height": h})
        return out

    def images(self, subject: str, cache: Path, limit: int = 4, exclude: set = frozenset()) -> List[Path]:
        """Download up to `limit` good images about `subject`, best first."""
        try:
            title = self.find_page(subject)
            if not title:
                return []
            candidates = self.page_images(title)
            if len(candidates) < limit * 2:  # character pages keep most pictures on subpages
                seen = {c["url"] for c in candidates}
                for sub in ("Gallery", "Abilities and Powers", "History", "Personality and Relationships"):
                    try:
                        for c in self.page_images(f"{title}/{sub}"):
                            if c["url"] not in seen:
                                seen.add(c["url"])
                                candidates.append(c)
                    except Exception:
                        continue
                    if len(candidates) >= limit * 3:
                        break
        except Exception:
            return []
        words = [w for w in re.findall(r"\w+", subject.lower()) if len(w) > 2]

        def score(c: dict) -> float:
            name = c["name"].lower()
            s = 2.0 * sum(w in name for w in words) + (1.5 if PREFER.search(name) else 0)
            s += min(c["width"] * c["height"], 2_000_000) / 1_000_000  # bigger is better, capped
            s += 0.5 if c["height"] >= c["width"] else 0  # portrait suits 9:16
            return s

        out: List[Path] = []
        for c in sorted(candidates, key=score, reverse=True):
            if len(out) >= limit:
                break
            if c["url"] in exclude:
                continue
            path = self._download(c["url"], cache)
            if path:
                out.append(path)
        return out

    def scene_images(self, scene: str, cache: Path, limit: int = 3, exclude: set = frozenset()) -> List[Path]:
        """Pictures of a specific MOMENT: search the wiki for the scene phrase, prefer files whose names
        share its words (e.g. "Oars Shadow Revival" for "Moria puts shadow into Oars")."""
        words = [w for w in re.findall(r"[a-z0-9]+", scene.lower()) if len(w) > 3]
        if not words:
            return []
        try:
            hits = self._q(action="query", list="search", srsearch=scene, srlimit=3).get("query", {}).get("search", [])
            candidates = []
            for hit in hits[:2]:
                candidates += self.page_images(hit["title"])
        except Exception:
            return []
        scored = []
        for c in candidates:
            name = c["name"].lower()
            matches = sum(w in name for w in words)
            if matches:
                scored.append((matches + min(c["width"] * c["height"], 2_000_000) / 4_000_000, c))
        out: List[Path] = []
        for _, c in sorted(scored, key=lambda x: x[0], reverse=True):
            if len(out) >= limit:
                break
            if c["url"] in exclude:
                continue
            path = self._download(c["url"], cache)
            if path and path not in out:
                out.append(path)
        return out

    def _download(self, url: str, cache: Path) -> Optional[Path]:
        from PIL import Image, ImageOps

        cache.mkdir(parents=True, exist_ok=True)
        path = cache / f"wiki_{hashlib.sha1(url.encode()).hexdigest()[:16]}.jpg"
        if path.exists():
            return path
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with self.opener(req, timeout=45) as resp:
                raw = resp.read()
            with Image.open(BytesIO(raw)) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                if im.getextrema()[0][1] - im.getextrema()[0][0] < 20:  # flat / blank image
                    return None
                im.save(path, quality=92)
            return path
        except Exception:
            return None
