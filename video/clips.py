"""Anime video clips for the cinematic layout (owner's decision, 2026-11-25).

Sources: the show's official trailer (YouTube; works locally, usually blocked on cloud runners) and
public Reels from OFFICIAL accounts only (channel.json clip_accounts, e.g. crunchyroll; found via
business_discovery, downloaded with yt-dlp without login). Never fan-edit pages: their clips carry
their own captions and watermarks.

Edit style: 1.0-2.4 s cuts from inside a shot, cropped to 9:16, colour-graded, ORIGINAL AUDIO DROPPED
(our voiceover + music play over them). Downloaded videos are deleted right after the cuts are made.
Each clip's middle frame passes the Image Inspector (no title cards, text, logos).

A clip on disk is a folder `<cache>/clip_<hash>/` with frames 000.jpg... at 30 fps (1080x1920).
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

FPS = 30
MIN_CLIP, MAX_CLIP = 1.0, 2.4
GRADE = {"dark": "eq=contrast=1.18:brightness=-0.05:saturation=1.1,vignette=PI/5",
         "vivid": "eq=contrast=1.12:saturation=1.35", "none": "null"}


def log(msg: str) -> None:
    print(f"[clips] {msg}", file=sys.stderr, flush=True)


def is_clip(path: str) -> bool:
    return bool(path) and Path(path).is_dir() and Path(path).name.startswith("clip_")


def clip_frames(path: str) -> List[Path]:
    return sorted(Path(path).glob("*.jpg"))


# ------------------------------------------------------------------ sources

def mentions(caption: str, names: Sequence[str]) -> bool:
    text = (caption or "").casefold()
    for name in names:
        words = [w for w in re.findall(r"[a-z0-9]+", (name or "").casefold()) if len(w) > 3]
        if words and (name.casefold() in text or sum(w in text for w in words) >= min(2, len(words))):
            return True
    return False


# Official accounts also post ads, collabs and merch: those Reels are not story footage.
PROMO = re.compile(r"\b(fortnite|collab|collaboration|game|gaming|playstation|xbox|nintendo|merch|figure|figurine|"
                   r"pre-?order|shop|store|sale|giveaway|sponsored|ad\b|available now on|tickets?|concert|cafe|"
                   r"live[- ]action|netflix series|event|booth|convention|expo)", re.I)


def instagram_sources(names: Sequence[str], watchlist: Sequence[str], limit: int = 4,
                      require_mention: bool = True) -> List[str]:
    """Permalinks of recent Reels on official pages. Distributor pages (Crunchyroll...) post about many
    shows, so their captions must mention this one; a show's OWN account (require_mention=False) is all on-topic."""
    try:
        from integrations.instagram import client_from_env

        ig = client_from_env()
    except Exception as exc:
        log(f"Instagram unavailable for clip search ({type(exc).__name__})")
        return []
    out: List[str] = []
    for page in watchlist:
        try:
            posts = ig.discover(page, 30)["posts"]
        except Exception:
            continue
        for p in posts:
            caption = p.get("caption", "")
            if PROMO.search(caption):
                continue
            if p.get("type") == "REELS" and p.get("permalink") and (not require_mention or mentions(caption, names)):
                out.append(p["permalink"])
        if len(out) >= limit:
            break
    return out[:limit]


def download(url: str, work: Path) -> Optional[Path]:
    try:
        import yt_dlp
    except ImportError:
        return None
    key = hashlib.sha1(url.encode()).hexdigest()[:10]
    opts = {"outtmpl": str(work / f"src_{key}.%(ext)s"), "format": "bv*[height<=1280][ext=mp4]/b[height<=1280]/best",
            "quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True, "max_filesize": 200 * 1024 * 1024}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as exc:
        log(f"download failed for {url[:60]} ({str(exc).splitlines()[0][:120]})")
        return None
    return next((p for p in work.glob(f"src_{key}.*") if p.suffix not in (".part", ".ytdl")), None)


# ------------------------------------------------------------------ cutting

def duration_of(video: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def segments(video: Path, skip_head: float, skip_tail: float, max_segments: int) -> List[tuple]:
    """(start, length) of clean cuts: inside one shot, away from cut boundaries."""
    from research.analyze_reel import scene_cuts

    total = duration_of(video)
    cuts = [0.0, *scene_cuts(video, threshold=0.32), total]
    out = []
    for a, b in zip(cuts, cuts[1:]):
        a, b = a + 0.12, b - 0.08
        if b - a < MIN_CLIP or a < skip_head or b > total - skip_tail:
            continue
        out.append((round(a, 2), round(min(MAX_CLIP, b - a), 2)))
    if len(out) > max_segments:  # spread over the whole video
        step = len(out) / max_segments
        out = [out[int(k * step)] for k in range(max_segments)]
    return out


def cut(video: Path, start: float, length: float, cache: Path, grading: str) -> Optional[Path]:
    key = hashlib.sha1(f"{video.name}|{start}|{length}|{grading}".encode()).hexdigest()[:12]
    folder = cache / f"clip_{key}"
    if folder.exists() and any(folder.glob("*.jpg")):
        return folder
    folder.mkdir(parents=True, exist_ok=True)
    vf = (f"fps={FPS},scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920,"
          f"{GRADE.get(grading, 'null')}")
    result = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(length), "-i", str(video), "-an",
                             "-vf", vf, "-q:v", "3", str(folder / "%03d.jpg")], capture_output=True)
    if result.returncode != 0 or len(list(folder.glob("*.jpg"))) < int(MIN_CLIP * FPS * 0.8):
        shutil.rmtree(folder, ignore_errors=True)
        return None
    return folder


def collect_clips(urls: Iterable[str], cache: Path, grading: str, inspector=None, max_clips: int = 16,
                  per_video: int = 14) -> List[str]:
    """Download each source, cut clean clips, inspect them, delete the source video."""
    clips: List[str] = []
    rejected = 0
    for url in urls:
        if len(clips) >= max_clips:
            break
        work = Path(tempfile.mkdtemp(prefix="clipsrc_"))
        try:
            video = download(url, work)
            if not video:
                continue
            trailer = "youtube" in url
            per = per_video if trailer else max(4, per_video // 2)  # several sources: take a few cuts from each
            for start, length in segments(video, 3.0 if trailer else 0.4, 8.0 if trailer else 1.2, per):
                if len(clips) >= max_clips:
                    break
                folder = cut(video, start, length, cache, grading)
                if not folder:
                    continue
                frames = clip_frames(str(folder))
                if inspector is not None:
                    verdict = inspector.inspect(frames[len(frames) // 2], expect="anime")
                    if not verdict.ok:
                        rejected += 1
                        shutil.rmtree(folder, ignore_errors=True)
                        continue
                clips.append(str(folder))
        finally:
            shutil.rmtree(work, ignore_errors=True)  # source videos are never kept
    log(f"{len(clips)} clips ready ({rejected} rejected by the inspector)")
    return clips


def find_clips(anime: str, cache: Path, grading: str, inspector=None, watchlist: Sequence[str] = (),
               trailer_id: Optional[str] = None, names: Sequence[str] = (), max_clips: int = 16,
               show_accounts: Sequence[str] = ()) -> List[str]:
    urls: List[str] = []
    if show_accounts:  # the show's own official Instagram (from AniList): every Reel is on-topic
        own = instagram_sources([anime, *names], show_accounts, limit=6, require_mention=False)
        log(f"official show accounts {list(show_accounts)}: {len(own)} Reels")
        urls += own
    if trailer_id:
        urls.append(f"https://www.youtube.com/watch?v={trailer_id}")
    urls += instagram_sources([anime, *names], watchlist)
    if not urls:
        log("no clip sources found")
        return []
    return collect_clips(urls, cache, grading, inspector, max_clips)
