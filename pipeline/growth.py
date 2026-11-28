"""Growth levers that use Instagram's own features (official APIs only).

- Trending audio: once a day the Instagram Audio API's trending list (+ a few mood searches) is stored in Salesforce
  (Agent_Playbook__c "trending_audio"). Each Reel then gets a licensed catalog track attached by Instagram at publish
  time, for the playbook's trending_audio_share of Reels (the rest keep library music - the scoreboard compares).
- Audience hours: followers online by hour -> Agent_Playbook__c "audience_online_hours" for the Strategist.
- First comment: a question under every new Reel (early comments are a strong ranking signal).
- Search captions: the first caption line carries the show name (Instagram search matches caption keywords).

  python -m pipeline.growth daily     # trending audio + audience hours (run by metrics.yml, once a day)
"""

from __future__ import annotations

import json
from pathlib import Path
import random
import re
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

MOOD_QUERIES = {"battle": "phonk", "hype": "hype", "epic": "epic", "emotional": "emotional", "mystery": "dark",
                "playful": "funny", "news": "trending", "trailer": "cinematic trailer"}
FIRST_COMMENT = {
    "Who Would Win": "Who wins this one? 👇 Defend your pick",
    "Ranking": "What's YOUR #1? 👇",
    "Fan Theory": "Do you buy this theory? 👇 Yes or no",
    "What If": "What would YOU change? 👇",
    "Quiz": "How many did you get right? 👇 Be honest",
    "Why It's Trending": "Are you watching this yet? 👇",
    "Just Announced": "Hyped or worried? 👇",
    "Hype Check": "Hyped or worried? 👇",
}


def log(msg: str) -> None:
    print(f"[growth] {msg}", file=sys.stderr, flush=True)


def _put(sf, key: str, value) -> None:
    sf.upsert("Agent_Playbook__c", "Key__c", key, {"Name": key, "Value__c": json.dumps(value, ensure_ascii=False)[:32000],
                                                     "Explanation__c": "Data written by GitHub (not a tunable setting)."})


def _get(sf, key: str):
    try:
        rows = sf.query(f"SELECT Value__c FROM Agent_Playbook__c WHERE Key__c = '{key}' LIMIT 1") or []
        return json.loads(rows[0]["Value__c"]) if rows and rows[0].get("Value__c") else None
    except Exception:
        return None


# ------------------------------------------------------------------ daily sync

def sync_trending_audio(ig, sf) -> int:
    """Trending music (no query) + one search per mood. ~6 light read calls."""
    tracks: List[dict] = []
    seen = set()

    def take(rows, mood):
        for r in rows:
            aid = str(r.get("audio_id") or "")
            if aid and aid not in seen and (r.get("duration_in_ms") or 0) >= 20000:
                seen.add(aid)
                tracks.append({"audio_id": aid, "title": r.get("title"), "artist": r.get("display_artist"),
                               "duration_ms": r.get("duration_in_ms"), "mood": mood})

    take(ig.search_audio(), "trending")
    for mood, query in MOOD_QUERIES.items():
        try:
            take(ig.search_audio(query)[:8], mood)
        except Exception as exc:
            log(f"audio search '{query}' failed ({exc})")
    _put(sf, "trending_audio", {"fetched_at": datetime.now(timezone.utc).isoformat(), "tracks": tracks})
    log(f"trending audio: {len(tracks)} licensed tracks stored")
    return len(tracks)


def sync_audience_hours(ig, sf) -> Dict[str, int]:
    try:
        hours = ig.online_followers()
    except Exception as exc:  # under 100 followers Instagram returns no data
        log(f"audience hours unavailable ({exc})")
        return {}
    if hours:
        _put(sf, "audience_online_hours", {"fetched_at": datetime.now(timezone.utc).isoformat(),
                                           "timezone": "account time zone (Instagram)", "followers_online_by_hour": hours})
        log(f"audience hours stored (peak hour {max(hours, key=hours.get)})")
    return hours


# ------------------------------------------------------------------ per Reel

def trending_share(sf, default: float = 0.7) -> float:
    v = _get(sf, "trending_audio_share")
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return default


def choose_trending(sf, mood, recent: List[str], seed: str) -> Optional[dict]:
    """A licensed trending track for this Reel's mood (or None -> library music). Not reused within recent Reels."""
    data = _get(sf, "trending_audio") or {}
    tracks = [t for t in data.get("tracks") or [] if t.get("audio_id")]
    if not tracks:
        return None
    rng = random.Random(seed)
    if rng.random() >= trending_share(sf):
        return None  # this Reel keeps library music (the comparison group)
    used = " ".join(recent)
    fresh = [t for t in tracks if t["audio_id"] not in used]
    moods = [mood] if isinstance(mood, str) else list(mood or [])
    pool = []
    for m in moods + ["epic", "hype"]:  # the format's moods first; generic regional "trending" only as a last resort
        pool = [t for t in fresh if t.get("mood") == m]
        if pool:
            break
    pool = pool or [t for t in fresh if t.get("mood") == "trending"]
    return dict(pool[0]) if pool else None


def first_comment(script: dict, fmt: str) -> str:
    outro = " ".join(sc.get("narration", "") for sc in script.get("scenes", []) if sc.get("role") == "outro")
    questions = re.findall(r"[^.!?]*\?", outro)
    if questions and 12 <= len(questions[-1].strip()) <= 120:
        return questions[-1].strip() + " 👇"
    return FIRST_COMMENT.get(fmt, f"What's your favourite {script.get('anime', 'anime')} moment? 👇")


def search_caption(caption: str, anime: str) -> str:
    """Make sure the first line (what search and the feed show) names the show."""
    first, _, rest = caption.partition("\n")
    if anime and anime.lower() not in first.lower():
        first = f"{first.rstrip()} | {anime}" if len(first) + len(anime) < 120 else f"{anime}: {first}"
    return first + ("\n" + rest if rest else "")


def story_teaser_cut(video: str, out: str, seconds: float = 8.0) -> bool:
    """First seconds of the Reel (the hook) as a Story teaser, with a 'NEW REEL' banner."""
    import subprocess

    from PIL import Image, ImageDraw, ImageFont

    font = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "Anton-Regular.ttf"
    banner_png = str(Path(out).with_suffix(".banner.png"))
    try:  # the banner is drawn with PIL (ffmpeg builds without drawtext are common) and overlaid
        img = Image.new("RGBA", (1080, 170), (0, 0, 0, 165))
        d = ImageDraw.Draw(img)
        f = ImageFont.truetype(str(font), 76) if font.exists() else ImageFont.load_default()
        d.text((540, 85), "NEW REEL  •  TAP OUR PROFILE", font=f, fill=(255, 255, 255), anchor="mm")
        img.save(banner_png)
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", video, "-i", banner_png, "-t", f"{seconds:.1f}",
                        "-filter_complex", "[0:v]scale=1080:1920[m];[m][1:v]overlay=0:1500[v]",
                        "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                        "-c:a", "aac", "-b:a", "128k", out], check=True, timeout=180)
        return True
    except Exception as exc:
        log(f"story teaser cut failed ({exc})")
        return False


def post_story_teaser(video_url: Optional[str]) -> Optional[str]:
    """Never fails the Reel."""
    if not video_url:
        return None
    try:
        from integrations.instagram import client_from_env

        story_id = client_from_env().publish_story(video_url)
        log(f"story teaser posted ({story_id})")
        return story_id
    except Exception as exc:
        log(f"story teaser skipped ({type(exc).__name__}: {str(exc)[:160]})")
        return None


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if argv[:1] != ["daily"]:
        print(__doc__)
        return 0
    from integrations.instagram import client_from_env
    from integrations.salesforce import SalesforceClient

    sf = SalesforceClient.from_env()
    if sf is None:
        log("Salesforce not configured")
        return 0
    current = _get(sf, "trending_audio") or {}
    try:
        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(current["fetched_at"])).total_seconds() / 3600
    except (KeyError, TypeError, ValueError):
        age_h = None
    if age_h is not None and age_h < 20 and "--force" not in argv:
        log(f"trending audio is {age_h:.0f} h old; no refresh needed")
        return 0
    ig = client_from_env()
    try:
        sync_trending_audio(ig, sf)
    except Exception as exc:
        log(f"trending audio sync failed ({exc}); Reels keep library music")
    sync_audience_hours(ig, sf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
