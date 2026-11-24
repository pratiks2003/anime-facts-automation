"""Openly licensed images and music from Openverse (api.openverse.org, free, no key).

Why not Google Images: results are mostly copyrighted official art/screenshots, and
scraping Google violates its terms. Openverse indexes CC-licensed media with
creator + license metadata, which we keep for attribution.

Safety rules (license labels on fan uploads are unreliable):
  * only CC0, Public Domain Mark and CC BY (no NC/ND/SA) are requested;
  * scene image queries must be generic (objects/scenery), never anime/character names;
  * results whose title/tags mention anime names, characters, "screenshot", "episode",
    "anime", "manga", "cosplay", "fanart" ... are rejected;
  * every asset's title/creator/license/source is recorded and credited in the caption.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Set

API = "https://api.openverse.org/v1"
USER_AGENT = "anime-facts-automation/0.1 (+https://github.com/pratiks2003/anime-facts-automation)"
LICENSES = "cc0,pdm,by"
GENERIC_BLOCK = {
    "anime", "manga", "screenshot", "screencap", "screen cap", "episode", "cosplay", "cosplayer",
    "fanart", "fan art", "figure", "figurine", "funko", "lego", "crunchyroll", "toei", "shueisha",
    "shonen jump", "shounen", "otaku", "comic con", "comiccon",
}
MOOD_QUERIES = {
    "hype": ["epic cinematic", "epic orchestral", "action trailer"],
    "mystery": ["dark ambient", "mystery cinematic"],
    "sad": ["emotional piano", "sad cinematic"],
    "default": ["cinematic", "epic cinematic"],
}


@dataclass
class MediaAsset:
    kind: str           # image | audio
    path: str
    title: str
    creator: str
    license: str        # e.g. "CC BY 2.0"
    source_url: str     # landing page (for attribution)
    provider: str
    openverse_id: str

    def credit(self, max_title: int = 40) -> str:
        title = self.title if len(self.title) <= max_title else self.title[: max_title - 1].rstrip(" ,:;") + "…"
        who = f" by {self.creator}" if self.creator else ""
        return f"\"{title}\"{who} ({self.license}, {self.provider})"

    def to_dict(self) -> dict:
        return asdict(self)


def license_label(code: str, version: Optional[str]) -> str:
    code = (code or "").lower()
    if code == "cc0":
        return "CC0"
    if code == "pdm":
        return "Public Domain"
    return f"CC {code.upper()} {version}".strip() if version else f"CC {code.upper()}"


def build_blocklist(phrases: Iterable[str], names: Iterable[str] = ()) -> Set[str]:
    """phrases are blocked as whole phrases ("devil fruit" must not block "fruit");
    character names are also blocked word by word ("Monkey D. Luffy" blocks "luffy")."""
    terms = set(GENERIC_BLOCK)
    for term in phrases:
        term = term.casefold().strip()
        if len(term) >= 3:
            terms.add(term)
    for name in names:
        name = name.casefold().strip()
        if len(name) >= 3:
            terms.add(name)
            terms.update(p for p in re.split(r"[\s.\-]+", name) if len(p) >= 4)
    return terms


def mentions_blocked(text: str, blocklist: Set[str]) -> Optional[str]:
    low = f" {text.casefold()} "
    for term in blocklist:
        if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", low):
            return term
    return None


class Openverse:
    def __init__(self, opener: Callable = urllib.request.urlopen, sleep: Callable[[float], None] = time.sleep):
        self._opener = opener
        self._sleep = sleep

    def _get(self, url: str, binary: bool = False, attempts: int = 3):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        for attempt in range(1, attempts + 1):
            try:
                with self._opener(request, timeout=45) as response:
                    data = response.read()
                    return data if binary else json.loads(data.decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < attempts:
                    self._sleep(5 * attempt)
                    continue
                raise
            except urllib.error.URLError:
                if attempt < attempts:
                    self._sleep(3 * attempt)
                    continue
                raise

    def search(self, kind: str, query: str, page_size: int = 20, **filters) -> List[dict]:
        params = {"q": query, "license": LICENSES, "mature": "false", "page_size": str(page_size), **filters}
        data = self._get(f"{API}/{kind}/?{urllib.parse.urlencode(params)}")
        return data.get("results", [])

    def download(self, url: str) -> bytes:
        return self._get(url, binary=True)


def _result_text(r: dict) -> str:
    tags = " ".join(t.get("name", "") for t in r.get("tags") or [])
    return f"{r.get('title', '')} {tags} {r.get('creator', '')}"


def find_image(client: Openverse, queries: List[str], cache_dir: Path, blocklist: Set[str],
               exclude_ids: Set[str], min_side: int = 900) -> Optional[MediaAsset]:
    from PIL import Image  # local import keeps module light for tests

    for query in queries:
        bad = mentions_blocked(query, blocklist)
        if bad:
            raise ValueError(f"image query '{query}' mentions blocked term '{bad}'; use generic objects/scenery")
        for r in client.search("images", query):
            if r["id"] in exclude_ids:
                continue
            if max(r.get("width") or 0, r.get("height") or 0) < min_side:
                continue
            if mentions_blocked(_result_text(r), blocklist):
                continue
            path = cache_dir / f"img_{r['id']}.jpg"
            if not path.exists():
                try:
                    raw = client.download(r["url"])
                    with Image.open(BytesIO(raw)) as im:
                        im = im.convert("RGB")
                        if min(im.size) < 500:
                            continue
                        path.parent.mkdir(parents=True, exist_ok=True)
                        im.save(path, quality=92)
                except Exception:
                    continue  # dead link / not an image: try the next result
            return MediaAsset("image", str(path), r.get("title") or "Untitled", r.get("creator") or "",
                              license_label(r.get("license"), r.get("license_version")),
                              r.get("foreign_landing_url") or r.get("url"), r.get("provider") or r.get("source") or "",
                              r["id"])
    return None


def find_music(client: Openverse, mood: str, seed: str, cache_dir: Path, min_seconds: int = 45,
               keywords: str = "") -> Optional[MediaAsset]:
    candidates = []
    queries = ([k.strip() for k in keywords.split(",") if k.strip()][:2] if keywords else []) + MOOD_QUERIES.get(mood, MOOD_QUERIES["default"])
    for query in queries:
        for r in client.search("audio", query, category="music"):
            if (r.get("duration") or 0) < min_seconds * 1000:
                continue
            if r.get("filetype") not in (None, "mp3", "mp32", "ogg", "wav"):
                continue
            candidates.append(r)
        if len(candidates) >= 8:
            break
    if not candidates:
        return None
    start = int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(candidates)
    for r in candidates[start:] + candidates[:start]:
        path = cache_dir / f"music_{r['id']}.mp3"
        if not path.exists():
            try:
                raw = client.download(r["url"])
                if len(raw) < 100_000:
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            except Exception:
                continue
        return MediaAsset("audio", str(path), r.get("title") or "Untitled", r.get("creator") or "",
                          license_label(r.get("license"), r.get("license_version")),
                          r.get("foreign_landing_url") or r.get("url"), r.get("provider") or r.get("source") or "",
                          r["id"])
    return None


def credits_text(images: List[MediaAsset], music: Optional[MediaAsset]) -> str:
    lines = []
    if images:
        lines.append("Images: " + "; ".join(a.credit() for a in images))
    if music:
        lines.append("Music: " + music.credit())
    return "\n".join(lines)
