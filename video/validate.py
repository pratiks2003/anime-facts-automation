"""Validate a rendered MP4 against Instagram Reels specs (Meta docs, 2026-11).

  python -m video.validate output/<id>/video.mp4
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

MAX_BYTES = 300 * 1024 * 1024


def top_level_atoms(path: Path, limit: int = 20) -> List[str]:
    atoms = []
    with path.open("rb") as fh:
        while len(atoms) < limit:
            header = fh.read(8)
            if len(header) < 8:
                break
            size = int.from_bytes(header[:4], "big")
            kind = header[4:8].decode("latin-1")
            atoms.append(kind)
            if size == 1:
                size = int.from_bytes(fh.read(8), "big")
                fh.seek(size - 16, 1)
            elif size == 0:
                break
            else:
                fh.seek(size - 8, 1)
    return atoms


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(out.stdout)


def validate_video(path: str, min_seconds: float = 3.0, max_seconds: float = 90.0,
                   target: Tuple[float, float] = (28.0, 50.0)) -> Tuple[List[str], List[str], dict]:
    """Return (errors, warnings, summary)."""
    p = Path(path)
    errors: List[str] = []
    warnings: List[str] = []
    if not p.is_file():
        return [f"file not found: {p}"], [], {}
    info = probe(p)
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    duration = float(info["format"].get("duration", 0))
    size = p.stat().st_size
    summary = {"duration": round(duration, 2), "size_mb": round(size / 1048576, 2)}

    if video is None:
        errors.append("no video stream")
    else:
        num, den = (int(x) for x in video["r_frame_rate"].split("/"))
        fps = num / den if den else 0
        summary.update(video=video["codec_name"], width=video["width"], height=video["height"],
                       fps=round(fps, 2), pix_fmt=video.get("pix_fmt"))
        if video["codec_name"] not in ("h264", "hevc"):
            errors.append(f"video codec {video['codec_name']} (need h264/hevc)")
        if (video["width"], video["height"]) != (1080, 1920):
            errors.append(f"resolution {video['width']}x{video['height']} (want 1080x1920)")
        if not 23 <= fps <= 60:
            errors.append(f"frame rate {fps:.2f} outside 23-60")
        if video.get("pix_fmt") != "yuv420p":
            errors.append(f"pixel format {video.get('pix_fmt')} (need yuv420p)")
    if audio is None:
        errors.append("no audio stream")
    else:
        summary.update(audio=audio["codec_name"], sample_rate=int(audio["sample_rate"]), channels=audio["channels"])
        if audio["codec_name"] != "aac":
            errors.append(f"audio codec {audio['codec_name']} (need aac)")
        if int(audio["sample_rate"]) > 48000:
            errors.append("audio sample rate above 48 kHz")
        if audio["channels"] not in (1, 2):
            errors.append("audio must be mono or stereo")

    if not min_seconds <= duration <= max_seconds:
        errors.append(f"duration {duration:.1f}s outside {min_seconds}-{max_seconds}s")
    elif not target[0] <= duration <= target[1]:
        warnings.append(f"duration {duration:.1f}s outside target {target[0]}-{target[1]}s")
    if size > MAX_BYTES:
        errors.append("file larger than 300 MB")

    atoms = top_level_atoms(p)
    if "moov" in atoms and "mdat" in atoms and atoms.index("moov") > atoms.index("mdat"):
        errors.append("moov atom after mdat (encode with -movflags +faststart)")
    return errors, warnings, summary


def main(argv=None) -> int:
    paths = argv or sys.argv[1:]
    if not paths:
        print("usage: python -m video.validate VIDEO.mp4", file=sys.stderr)
        return 2
    errors, warnings, summary = validate_video(paths[0])
    print(json.dumps({"ok": not errors, "errors": errors, "warnings": warnings, "summary": summary}, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
