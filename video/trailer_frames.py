"""Still frames from a show's official trailer (the YouTube PV linked on AniList).

For new shows without a fan wiki or episode screenshots this is the richest source of real
anime screenshots. One sharp frame from the middle of each shot; the video is deleted right
after. Frames still pass the Image Inspector (title / date cards are rejected as text).
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List


def log(msg: str) -> None:
    print(f"[trailer] {msg}", file=sys.stderr, flush=True)


def shot_midpoints(video: Path, duration: float, skip_head: float = 3.0, skip_tail: float = 8.0,
                   min_len: float = 0.7) -> List[float]:
    from research.analyze_reel import scene_cuts

    cuts = [0.0, *scene_cuts(video, threshold=0.32), duration]
    mids = []
    for a, b in zip(cuts, cuts[1:]):
        if b - a >= min_len and skip_head <= (a + b) / 2 <= duration - skip_tail:
            mids.append(round(a + (b - a) * 0.55, 2))
    return mids


def trailer_frames(youtube_id: str, cache: Path, limit: int = 24) -> List[Path]:
    out_dir = cache / f"trailer_{youtube_id}"
    done = sorted(out_dir.glob("frame_*.jpg")) if out_dir.exists() else []
    if done:
        return done[:limit]
    try:
        import yt_dlp
    except ImportError:
        return []
    work = Path(tempfile.mkdtemp(prefix="pv_"))
    try:
        opts = {"outtmpl": str(work / "pv.%(ext)s"), "format": "bv*[height<=720][ext=mp4]/b[height<=720]",
                "quiet": True, "no_warnings": True, "noprogress": True, "max_filesize": 150 * 1024 * 1024}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={youtube_id}", download=True)
        video = next(p for p in work.glob("pv.*") if p.suffix != ".part")
        duration = float(info.get("duration") or 0)
        mids = shot_midpoints(video, duration)
        if len(mids) > limit:  # spread across the whole trailer
            step = len(mids) / limit
            mids = [mids[int(k * step)] for k in range(limit)]
        out_dir.mkdir(parents=True, exist_ok=True)
        for k, t in enumerate(mids):
            dest = out_dir / f"frame_{k:02d}.jpg"
            subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1", "-q:v", "2", str(dest)],
                           capture_output=True)
        frames = sorted(out_dir.glob("frame_*.jpg"))
        log(f"{len(frames)} frames from the official trailer ({duration:.0f}s)")
        return frames
    except Exception as exc:
        log(f"trailer unavailable ({type(exc).__name__}: {str(exc)[:160]})")
        return []
    finally:
        shutil.rmtree(work, ignore_errors=True)  # the video itself is never kept
