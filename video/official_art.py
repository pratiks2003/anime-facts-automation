"""Official anime artwork (key-visual posters, banners, character portraits) from AniList,
with Kitsu as a poster fallback, plus an image-quality gate.

The channel owner chose to use official promotional art (2026-11-25). Every image
must pass `verify_image` before use: resolution, not a placeholder, not blank, and,
when the Cloudflare vision model is available, a relevance + safety check.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
from io import BytesIO
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

ANILIST = "https://graphql.anilist.co"
KITSU = "https://kitsu.io/api/edge/anime"
UA = "anime-facts-automation/0.1"
VISION_MODEL = "@cf/meta/llama-3.2-11b-vision-instruct"

MEDIA_ART = """query ($search: String) { Media(search: $search, type: ANIME, isAdult: false) {
  id title { english romaji } coverImage { extraLarge color } bannerImage } }"""
CHARACTER_ART = """query ($search: String) { Character(search: $search) {
  name { full } image { large } media(perPage: 3) { nodes { title { english romaji } } } } }"""

MIN_SIZES = {"poster": (380, 520), "banner": (1200, 250), "portrait": (200, 300)}


def _get(url: str, opener: Callable, accept: str = "*/*") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with opener(req, timeout=45) as resp:
        return resp.read()


def _graphql(query: str, variables: dict, opener: Callable) -> dict:
    req = urllib.request.Request(ANILIST, data=json.dumps({"query": query, "variables": variables}).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": UA}, method="POST")
    with opener(req, timeout=40) as resp:
        return json.loads(resp.read().decode()).get("data") or {}


def _save(raw: bytes, cache: Path, key: str) -> Path:
    from PIL import Image

    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"official_{hashlib.sha1(key.encode()).hexdigest()[:16]}.jpg"
    with Image.open(BytesIO(raw)) as im:
        im.convert("RGB").save(path, quality=94)
    return path


def show_art(title: str, cache: Path, opener: Callable = urllib.request.urlopen) -> Dict[str, Optional[Path]]:
    """{"poster": path|None, "banner": path|None, "title": canonical title} for a show."""
    out: Dict[str, Optional[Path]] = {"poster": None, "banner": None, "title": title}
    try:
        media = _graphql(MEDIA_ART, {"search": title}, opener).get("Media")
    except Exception:
        media = None
    if media:
        out["title"] = media["title"].get("english") or media["title"].get("romaji") or title
        cover = (media.get("coverImage") or {}).get("extraLarge")
        if cover and "/default." not in cover:
            out["poster"] = _save(_get(cover, opener), cache, cover)
        if media.get("bannerImage"):
            out["banner"] = _save(_get(media["bannerImage"], opener), cache, media["bannerImage"])
    if out["poster"] is None or _size(out["poster"])[0] < MIN_SIZES["poster"][0]:
        try:  # Kitsu often has a slightly larger poster
            data = json.loads(_get(f"{KITSU}?{urllib.parse.urlencode({'filter[text]': title, 'page[limit]': 1})}",
                                   opener, "application/vnd.api+json"))
            poster = ((data.get("data") or [{}])[0].get("attributes") or {}).get("posterImage") or {}
            if poster.get("original"):
                out["poster"] = _save(_get(poster["original"], opener), cache, poster["original"])
        except Exception:
            pass
    return out


def character_art(name: str, anime: str, cache: Path, opener: Callable = urllib.request.urlopen) -> Optional[Path]:
    try:
        ch = _graphql(CHARACTER_ART, {"search": name}, opener).get("Character")
    except Exception:
        return None
    if not ch or not (ch.get("image") or {}).get("large") or "/default." in ch["image"]["large"]:
        return None
    titles = " ".join((n["title"].get("english") or "") + " " + (n["title"].get("romaji") or "")
                      for n in (ch.get("media") or {}).get("nodes", []))
    if anime and anime.split(":")[0].casefold() not in titles.casefold():
        return None  # same name, different series
    return _save(_get(ch["image"]["large"], opener), cache, ch["image"]["large"])


GALLERY = """query ($search: String) { Media(search: $search, type: ANIME, isAdult: false) {
  trailer { id site } streamingEpisodes { title thumbnail } externalLinks { site url }
  characters(sort: [ROLE, FAVOURITES_DESC], perPage: 12) { nodes { name { full alternative } image { large } } } } }"""


def official_instagram(links: list) -> list:
    """The show's official Instagram handles from AniList external links; English / international first
    (other-language accounts often burn in subtitles)."""
    handles = []
    for link in links:
        if (link.get("site") or "") != "Instagram":
            continue
        m = re.search(r"instagram\.com/([A-Za-z0-9._]+)", link.get("url") or "")
        if m and m.group(1).lower() not in handles:
            handles.append(m.group(1).lower())
    lang = re.compile(r"[._](it|es|fr|de|pt|br|la|jp|kr|tw|th|id|ar|ru)$")
    return sorted(handles, key=lambda h: (bool(lang.search(h)), not h.endswith("_en")))[:3]


def anilist_gallery(title: str, opener: Callable = urllib.request.urlopen) -> dict:
    """Image URLs for a show beyond the poster: episode screenshots, main-character portraits, trailer frame.
    {"episodes": [url], "characters": [(name, [aliases], url)], "trailer": url|None}"""
    out: dict = {"episodes": [], "characters": [], "trailer": None, "instagram": []}
    try:
        media = _graphql(GALLERY, {"search": title}, opener).get("Media") or {}
    except Exception:
        return out
    out["episodes"] = [e["thumbnail"] for e in media.get("streamingEpisodes") or [] if e.get("thumbnail")][:40]
    out["instagram"] = official_instagram(media.get("externalLinks") or [])
    for node in (media.get("characters") or {}).get("nodes") or []:
        url = (node.get("image") or {}).get("large")
        if url and "/default." not in url:
            out["characters"].append((node["name"]["full"], node["name"].get("alternative") or [], url))
    trailer = media.get("trailer") or {}
    if trailer.get("site") == "youtube" and trailer.get("id"):
        out["trailer"] = f"https://i.ytimg.com/vi/{trailer['id']}/maxresdefault.jpg"
        out["trailer_id"] = trailer["id"]
    return out


def download_art(url: str, cache: Path, opener: Callable = urllib.request.urlopen) -> Optional[Path]:
    try:
        return _save(_get(url, opener), cache, url)
    except Exception:
        return None


def _size(path: Path) -> Tuple[int, int]:
    from PIL import Image

    with Image.open(path) as im:
        return im.size


def basic_checks(path: Path, kind: str) -> Optional[str]:
    """Return a reason string if the image is unusable, else None."""
    from PIL import Image, ImageStat

    w, h = _size(path)
    min_w, min_h = MIN_SIZES[kind]
    if w < min_w or h < min_h:
        return f"too small ({w}x{h}, need {min_w}x{min_h})"
    with Image.open(path) as im:
        stat = ImageStat.Stat(im.convert("L").resize((64, 64)))
    if stat.stddev[0] < 18:
        return "nearly blank / flat image"
    return None


class VisionChecker:
    """Relevance + safety check with a Cloudflare vision model (free allocation)."""

    def __init__(self, account_id: str, token: str, opener: Callable = urllib.request.urlopen):
        self._url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{VISION_MODEL}"
        self._token = token
        self._opener = opener
        self._agreed = False

    @classmethod
    def from_env(cls) -> Optional["VisionChecker"]:
        a, t = os.environ.get("CF_ACCOUNT_ID"), os.environ.get("CF_API_TOKEN")
        return cls(a, t) if a and t else None

    def _run(self, body: dict) -> dict:
        req = urllib.request.Request(self._url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json", "User-Agent": UA})
        with self._opener(req, timeout=90) as resp:
            return json.loads(resp.read().decode())

    def check(self, path: Path, subject: str) -> Tuple[bool, str]:
        """(ok, reason). Raises on API problems so callers can fall back to basic checks."""
        from PIL import Image

        if not self._agreed:  # Meta's Llama 3.2 license must be accepted once per account
            try:
                self._run({"prompt": "agree"})
            except Exception:
                pass
            self._agreed = True
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((512, 512))
            buf = BytesIO()
            im.save(buf, "JPEG", quality=85)
        question = (f'This image will be used in an Instagram Reel about "{subject}". Answer ONLY JSON: '
                    '{"anime_art": true/false (is it anime/manga artwork), "matches": true/false (plausibly official art for that subject), '
                    '"safe": true/false (no nudity, no sexualised poses, no gore), "mostly_text": true/false}')
        image_url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        data = self._run({"messages": [{"role": "user", "content": [
            {"type": "text", "text": question}, {"type": "image_url", "image_url": {"url": image_url}}]}],
            "max_tokens": 120})
        text = str((data.get("result") or {}).get("response") or "")
        start, end = text.find("{"), text.rfind("}")
        verdict = json.loads(text[start:end + 1]) if start != -1 and end != -1 else {}
        if not verdict:
            raise RuntimeError("vision model gave no verdict")
        if not verdict.get("safe", False):
            return False, "vision: not safe"
        if not verdict.get("anime_art", False):
            return False, "vision: not anime artwork"
        if verdict.get("mostly_text"):
            return False, "vision: mostly text"
        return True, "vision: ok" + ("" if verdict.get("matches", True) else " (subject match uncertain)")


def verify_image(path: Path, kind: str, subject: str, vision: Optional[VisionChecker]) -> Tuple[bool, str]:
    reason = basic_checks(path, kind)
    if reason:
        return False, reason
    if vision is None:
        return True, "basic checks only"
    try:
        return vision.check(path, subject)
    except Exception as exc:  # quota / model unavailable: fall back to basic checks
        return True, f"basic checks only (vision unavailable: {type(exc).__name__})"
