"""Learn from a Reel: download a PUBLIC Reel (yt-dlp, no login), measure what makes it work, delete it.

  python -m research.analyze_reel --url https://www.instagram.com/reel/XXXX/ [--reference-id a0X...]
  python -m research.analyze_reel --file local.mp4            # analyse a file you already have

Measured fingerprint (no video is kept): duration, cuts and seconds per shot, words per minute,
speech ratio, loudness, music bed level under the voice, hook sentence, transcript. The Style Analyst
agent (Salesforce) turns it into lessons + a Style Spec.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

SR = 16000
URL_RE = re.compile(r"^https://(www\.)?(instagram\.com/(reel|reels|p)/[\w-]+|youtube\.com/shorts/[\w-]+|youtu\.be/[\w-]+|tiktok\.com/@[\w.]+/video/\d+)")


def log(msg: str) -> None:
    print(f"[analyze] {msg}", file=sys.stderr, flush=True)


def valid_url(url: str) -> bool:
    return bool(URL_RE.match((url or "").strip()))


def download(url: str, folder: Path) -> Tuple[Path, dict]:
    """Public post only; no cookies/login. Returns (video path, metadata)."""
    if not valid_url(url):
        raise ValueError("only public Instagram Reel, YouTube Shorts or TikTok video links are supported")
    import yt_dlp

    opts = {"outtmpl": str(folder / "reel.%(ext)s"), "format": "best[height<=1280]/best", "quiet": True,
            "no_warnings": True, "noplaylist": True, "max_filesize": 200 * 1024 * 1024, "socket_timeout": 30}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    files = [p for p in folder.glob("reel.*") if p.suffix not in (".part", ".json")]
    if not files:
        raise RuntimeError("download produced no file")
    meta = {k: info.get(k) for k in ("title", "description", "uploader", "channel", "like_count", "view_count",
                                     "comment_count", "duration", "timestamp") if info.get(k) is not None}
    return files[0], meta


def ffprobe_duration(video: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def scene_cuts(video: Path, threshold: float = 0.3) -> List[float]:
    """Timestamps of visual changes (cuts / image swaps)."""
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(video), "-an", "-vf",
                           f"scale=270:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
                          capture_output=True, text=True)
    return [float(m) for m in re.findall(r"pts_time:([\d.]+)", proc.stderr)]


def loudness(video: Path) -> Optional[float]:
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(video), "-vn", "-af", "ebur128", "-f", "null", "-"],
                          capture_output=True, text=True)
    hits = re.findall(r"I:\s+(-?[\d.]+) LUFS", proc.stderr)
    return float(hits[-1]) if hits else None


def audio_16k(video: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def transcribe(audio: np.ndarray, model_size: str = "base.en") -> List[dict]:
    """Word timings [{word, start, end}] (faster-whisper, CPU, free)."""
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(audio, word_timestamps=True, vad_filter=True, language="en")
    words = []
    for seg in segments:
        for w in seg.words or []:
            words.append({"word": w.word.strip(), "start": round(w.start, 2), "end": round(w.end, 2)})
    return words


def speech_mask(words: List[dict], n: int, pad: float = 0.08) -> np.ndarray:
    mask = np.zeros(n, dtype=bool)
    for w in words:
        a, b = int(max(0, w["start"] - pad) * SR), int(min(n / SR, w["end"] + pad) * SR)
        mask[a:b] = True
    return mask


def rms_db(x: np.ndarray) -> Optional[float]:
    if x.size < SR // 4:
        return None
    return 20 * math.log10(float(np.sqrt(np.mean(np.square(x)))) + 1e-9)


def music_bed_db(audio: np.ndarray, words: List[dict]) -> Optional[float]:
    """Level of what plays in the speech gaps (music bed) relative to the voice, in dB."""
    mask = speech_mask(words, audio.size)
    speech, gaps = rms_db(audio[mask]), rms_db(audio[~mask])
    if speech is None or gaps is None:
        return None
    return round(gaps - speech, 1)


def measure(video: Path, words: Optional[List[dict]] = None) -> dict:
    duration = ffprobe_duration(video)
    cuts = scene_cuts(video)
    audio = audio_16k(video)
    words = transcribe(audio) if words is None else words
    speech_seconds = float(speech_mask(words, audio.size, pad=0.0).sum()) / SR
    text = " ".join(w["word"] for w in words)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    gaps = np.diff([0.0, *cuts, duration]) if duration else np.array([])
    return {
        "duration": round(duration, 1),
        "cuts": len(cuts),
        "cut_interval_seconds": round(duration / (len(cuts) + 1), 2) if duration else None,
        "longest_shot_seconds": round(float(gaps.max()), 1) if gaps.size else None,
        "words": len(words),
        "words_per_minute": round(len(words) / (duration / 60), 0) if duration else None,
        "speech_ratio": round(speech_seconds / duration, 2) if duration else None,
        "loudness_lufs": loudness(video),
        "music_bed_db": music_bed_db(audio, words),
        "hook": sentences[0] if sentences else "",
        "transcript": text[:4000],
        "first_words_at": words[0]["start"] if words else None,
    }


def report(sf, reference_id: str, payload: dict) -> Optional[dict]:
    if not reference_id or sf is None:
        return None
    payload = {"id": reference_id, **payload}
    return sf.apex_post("insta/research/reference", payload)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="")
    parser.add_argument("--file", default="")
    parser.add_argument("--reference-id", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    sf = None
    if args.reference_id:
        from pipeline.run_reel import salesforce

        sf = salesforce()
    work = Path(tempfile.mkdtemp(prefix="reel_ref_"))
    try:
        if args.file:
            video, meta = Path(args.file), {}
        else:
            log("downloading (public, no login)")
            video, meta = download(args.url, work)
        log("measuring")
        analysis = measure(video)
        analysis["meta"] = meta
        log(json.dumps({k: v for k, v in analysis.items() if k not in ("transcript",)}, ensure_ascii=False)[:1200])
        if args.out:
            Path(args.out).write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
        from video.style import style_limits

        result = report(sf, args.reference_id, {"status": "Measured", "analysis": analysis, "style_limits": style_limits()})
        if result:
            log(f"Style Analyst: {str(result.get('summary', ''))[:300]}")
        return 0
    except Exception as exc:
        log(f"FAILED {type(exc).__name__}: {exc}")
        try:
            report(sf, args.reference_id, {"status": "Failed", "error": f"{type(exc).__name__}: {exc}"[:250]})
        except Exception as cb:
            log(f"warning: failure callback failed ({cb})")
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)  # the video never outlives the job
        log("downloaded video deleted")


if __name__ == "__main__":
    sys.exit(main())
