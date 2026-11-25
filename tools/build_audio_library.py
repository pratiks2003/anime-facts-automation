"""Build the licensed MUSIC and SFX libraries once (re-run any time to refresh).

Music: per-mood instrumental tracks (CC0 / CC BY) from Openverse (Jamendo, ccMixter...).
SFX:   per-role short CC0 sound effects from Freesound (via Openverse) - CC0 so no credits needed.
Files are uploaded to GitHub release assets ("music-library", "sfx-library"); the index JSON
(title, creator, license, source, mood/role, duration) is committed to assets/audio_library.json.

  python tools/build_audio_library.py [--per-mood 8] [--per-role 6] [--no-upload]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from video.media import BANNED_MUSIC, Openverse, license_label  # noqa: E402

INDEX = ROOT / "assets" / "audio_library.json"

MUSIC_QUERIES = {
    "battle": ["epic orchestral battle", "epic drums action", "taiko drums", "cinematic action rock", "epic trailer battle"],
    "hype": ["energetic rock instrumental", "upbeat electronic", "epic trailer", "sport energetic"],
    "mystery": ["mystery suspense", "dark ambient tension", "investigation suspense", "thriller underscore"],
    "emotional": ["emotional piano", "sad strings", "melancholic cinematic", "piano sad"],
    "playful": ["playful quirky", "funny cartoon", "light adventure", "whimsical"],
    "news": ["upbeat corporate electronic", "energetic pop instrumental", "positive electronic", "news intro"],
    "epic": ["epic cinematic", "inspiring orchestral", "heroic orchestral", "rising cinematic"],
}

# Broader searches for moods the first pass couldn't fill.
EXTRA_QUERIES = {
    "battle": ["action", "fight", "battle", "war drums", "intense", "heavy rock instrumental", "metal instrumental", "drums"],
    "mystery": ["mystery", "suspense", "dark", "tension", "thriller", "horror ambient", "noir"],
    "news": ["corporate", "upbeat", "happy electronic", "funk", "pop instrumental", "motivational"],
    # the sound of anime edits: dark trap / drift phonk and big trailer builds
    "phonk": ["phonk", "drift phonk", "trap beat", "hard trap beat", "dark trap", "808"],
    "trailer": ["epic trailer", "trailer music", "cinematic trailer hybrid", "dramatic trailer", "action trailer"],
}

# Moods whose tracks mostly live on Freesound/ccMixter without the "music" category tag.
UNCATEGORIZED = {"phonk"}

SFX_QUERIES = {
    "whoosh": ["whoosh", "swoosh transition", "swipe whoosh"],
    "impact": ["cinematic impact", "hit impact", "punch impact"],
    "boom": ["boom", "trailer boom", "sub boom"],
    "riser": ["riser", "tension riser", "uplifter"],
    "drop": ["bass drop", "sub drop"],
    "sparkle": ["sparkle", "magic shimmer", "twinkle"],
    "glitch": ["glitch", "digital glitch"],
    "ding": ["ding", "correct answer", "bell notification"],
}


def text_of(r: dict) -> str:
    return f"{r.get('title', '')} {' '.join(t.get('name', '') for t in (r.get('tags') or []))}".lower()


def banned(r: dict) -> bool:
    t = text_of(r)
    return any(b in t for b in BANNED_MUSIC)


def normalize_mp3(raw: bytes, dest: Path, max_seconds: float, loudness: str) -> bool:
    """Trim + loudness-normalise with FFmpeg; returns False if the file isn't decodable audio."""
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
        tmp.write(raw)
    try:
        result = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", tmp.name, "-t", str(max_seconds),
                                 "-af", f"loudnorm={loudness}", "-ac", "2", "-ar", "48000", "-b:a", "160k", str(dest)],
                                capture_output=True)
        return result.returncode == 0 and dest.exists() and dest.stat().st_size > 2000
    finally:
        os.unlink(tmp.name)


def collect(ov: Openverse, queries: dict, kind: str, per_key: int, out_dir: Path) -> list:
    entries, seen = [], set()
    for key, qs in queries.items():
        picked = 0
        for q in qs:
            if picked >= per_key:
                break
            params = ({} if key in UNCATEGORIZED else {"category": "music"}) if kind == "music" else {"source": "freesound"}
            try:
                results = ov.search("audio", q, page_size=20, **params)
            except Exception as exc:
                print(f"  {key}: search '{q}' failed ({exc})")
                continue
            for r in results:
                if picked >= per_key or r["id"] in seen:
                    continue
                dur = (r.get("duration") or 0) / 1000
                if kind == "music" and (dur < 50 or banned(r)):
                    continue
                if kind == "sfx" and (not 0.2 <= dur <= 6 or r.get("license") != "cc0"):
                    continue
                if kind == "music" and r.get("license") not in ("cc0", "by", "pdm"):
                    continue
                try:
                    raw = ov.download(r["url"])
                except Exception:
                    continue
                name = f"{kind}_{key}_{hashlib.sha1(r['id'].encode()).hexdigest()[:10]}.mp3"
                dest = out_dir / name
                ok = normalize_mp3(raw, dest, 180 if kind == "music" else 6,
                                   "I=-18:TP=-2:LRA=11" if kind == "music" else "I=-16:TP=-1.5:LRA=7")
                if not ok:
                    continue
                seen.add(r["id"])
                picked += 1
                entries.append({"file": name, "kind": kind, "key": key, "id": r["id"], "title": r.get("title") or "Untitled",
                                "creator": r.get("creator") or "", "license": license_label(r.get("license"), r.get("license_version")),
                                "source_url": r.get("foreign_landing_url") or r.get("url"), "provider": r.get("provider") or "",
                                "duration": round(dur, 1), "tags": [t.get("name") for t in (r.get("tags") or [])][:8]})
                print(f"  {kind}/{key}: {r.get('title')!r} ({entries[-1]['license']}, {dur:.0f}s)")
        print(f"{kind}/{key}: {picked} item(s)")
    return entries


def upload(tag: str, files: list) -> None:
    exists = subprocess.run(["gh", "release", "view", tag], capture_output=True, cwd=ROOT).returncode == 0
    if not exists:
        subprocess.run(["gh", "release", "create", tag, "--prerelease", "--title", f"{tag} (licensed audio)",
                        "--notes", "Licensed background music / CC0 sound effects used by the Reel renderer. See assets/audio_library.json."],
                       check=True, cwd=ROOT)
    for i in range(0, len(files), 20):
        subprocess.run(["gh", "release", "upload", tag, "--clobber", *map(str, files[i:i + 20])], check=True, cwd=ROOT)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-mood", type=int, default=8)
    parser.add_argument("--per-role", type=int, default=6)
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--only", default="", help="comma-separated music moods to (re)collect and MERGE into the index")
    args = parser.parse_args()
    ov = Openverse()
    work = ROOT / "output" / "audio_library"
    work.mkdir(parents=True, exist_ok=True)
    if args.only:
        wanted = {m.strip() for m in args.only.split(",")}
        old = json.loads(INDEX.read_text()) if INDEX.exists() else {"music": [], "sfx": []}
        new = collect(ov, {k: v for k, v in EXTRA_QUERIES.items() if k in wanted}, "music", args.per_mood, work)
        seen = {e["id"] for e in old["music"]}
        music = old["music"] + [e for e in new if e["id"] not in seen]
        sfx = old["sfx"]
        INDEX.write_text(json.dumps({**old, "music": music}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"merged: {len(new)} new tracks -> {len(music)} total")
        if not args.no_upload:
            upload("music-library", [work / e["file"] for e in new])
        return 0
    music = collect(ov, MUSIC_QUERIES, "music", args.per_mood, work)
    sfx = collect(ov, SFX_QUERIES, "sfx", args.per_role, work)
    INDEX.write_text(json.dumps({"music_release": "music-library", "sfx_release": "sfx-library", "music": music, "sfx": sfx},
                                indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"index: {len(music)} tracks, {len(sfx)} effects -> {INDEX.relative_to(ROOT)}")
    if not args.no_upload:
        upload("music-library", [work / e["file"] for e in music])
        upload("sfx-library", [work / e["file"] for e in sfx])
        print("uploaded to GitHub releases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
