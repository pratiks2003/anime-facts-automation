"""Google image search for the exact moment a sentence describes (Programmable Search JSON API).

Free tier: 100 queries/day. Needs GOOGLE_CSE_KEY (API key with "Custom Search API" enabled) and
GOOGLE_CSE_ID (a search engine set to search the entire web with image search on). Without them
this source is simply skipped. Every downloaded picture still goes through the Image Inspector.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request
from io import BytesIO
from pathlib import Path
from typing import List, Optional

UA = "Mozilla/5.0 (compatible; anime-facts-automation/0.3)"
SKIP_HOSTS = ("pinterest.", "pinimg.", "redbubble.", "amazon.", "ebay.", "etsy.", "aliexpress.", "tiktok.", "facebook.")
MAX_QUERIES_PER_REEL = 12


def log(msg: str) -> None:
    print(f"[google] {msg}", file=sys.stderr, flush=True)


class GoogleImages:
    def __init__(self, key: str, cx: str, opener=urllib.request.urlopen):
        self.key, self.cx, self.opener = key, cx, opener
        self.queries = 0
        self.disabled = False

    @classmethod
    def from_env(cls) -> Optional["GoogleImages"]:
        key, cx = os.environ.get("GOOGLE_CSE_KEY"), os.environ.get("GOOGLE_CSE_ID")
        return cls(key, cx) if key and cx else None

    def search(self, query: str, num: int = 8) -> List[str]:
        if self.disabled or self.queries >= MAX_QUERIES_PER_REEL:
            return []
        self.queries += 1
        params = urllib.parse.urlencode({"key": self.key, "cx": self.cx, "q": query, "searchType": "image",
                                         "imgSize": "large", "num": num, "safe": "active"})
        try:
            with self.opener(f"https://www.googleapis.com/customsearch/v1?{params}", timeout=20) as resp:
                items = json.loads(resp.read().decode()).get("items", [])
        except Exception as exc:
            if "429" in str(exc) or "403" in str(exc):
                self.disabled = True  # daily quota used up / key problem: stop for this run
            log(f"search failed ({str(exc)[:120]})")
            return []
        return [i["link"] for i in items if i.get("link") and not any(h in i["link"] for h in SKIP_HOSTS)]

    def images(self, query: str, cache: Path, limit: int = 3) -> List[Path]:
        from PIL import Image, ImageOps

        out: List[Path] = []
        for url in self.search(query):
            if len(out) >= limit:
                break
            path = cache / f"google_{hashlib.sha1(url.encode()).hexdigest()[:16]}.jpg"
            if not path.exists():
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": UA})
                    with self.opener(req, timeout=20) as resp:
                        raw = resp.read(15 * 1024 * 1024)
                    with Image.open(BytesIO(raw)) as im:
                        cache.mkdir(parents=True, exist_ok=True)
                        ImageOps.exif_transpose(im).convert("RGB").save(path, quality=92)
                except Exception:
                    continue
            out.append(path)
        return out
