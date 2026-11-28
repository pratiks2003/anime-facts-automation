"""End-to-end Reel job, run by GitHub Actions (reel.yml / publish.yml).

  python -m pipeline.run_reel generate --content-id R-20261125-AM --job-id ... --format Quiz \
         [--anime "One Piece"] [--topic "..."] [--auto-approve true] [--dry-run true]
  python -m pipeline.run_reel publish --content-id R-20261125-AM --job-id ... [--dry-run false]

Every stage reports to Salesforce (Video_Job__c / Anime_Content__c / Social_Post__c).
A Reel is only published if Meta returns a media id AND all cited facts are verified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
import time
import os
import sys
import traceback
from pathlib import Path
from typing import List, Optional, Set

from integrations.salesforce import NullSalesforce, SalesforceClient, now_iso

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = PROJECT_ROOT / "output"


def log(msg: str) -> None:
    print(f"[reel] {msg}", flush=True)


def truthy(value: Optional[str]) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def run_url() -> Optional[str]:
    server, repo, run_id = (os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{server}/{repo}/actions/runs/{run_id}" if server and repo and run_id else None


def salesforce():
    try:
        client = SalesforceClient.from_env()
    except ValueError:
        client = None
    if client is None:
        log("Salesforce not configured; callbacks are logged only")
        return NullSalesforce()
    return client


def recent_usage(sf) -> tuple:
    """(knowledge ids used in recent Reels, anime names of recent Reels) from Salesforce."""
    if isinstance(sf, NullSalesforce):
        return set(), []
    try:
        rows = sf.query("SELECT Knowledge_Ids__c, Anime__r.Name FROM Anime_Content__c "
                        "WHERE Status__c IN ('Rendered','Approved','Publishing','Published') "
                        "ORDER BY Scheduled_For__c DESC NULLS LAST LIMIT 20")
    except Exception as exc:
        log(f"warning: could not read recent Reels ({exc})")
        return set(), []
    ids: Set[str] = set()
    anime: List[str] = []
    for r in rows:
        ids.update(k.strip() for k in (r.get("Knowledge_Ids__c") or "").split(",") if k.strip())
        if r.get("Anime__r"):
            anime.append(r["Anime__r"]["Name"])
    return ids, anime


def soql_str(value: str) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def learning_inputs(sf, format_name: str) -> tuple:
    """(analyst hook lessons, best backlog idea for this format) from Salesforce."""
    if isinstance(sf, NullSalesforce):
        return [], None
    lessons: List[str] = []
    idea = None
    try:
        rows = sf.query("SELECT Hook_Lessons__c FROM Performance_Review__c ORDER BY CreatedDate DESC LIMIT 1")
        if rows and rows[0].get("Hook_Lessons__c"):
            lessons = [l.strip() for l in rows[0]["Hook_Lessons__c"].splitlines() if l.strip()][:5]
        agent = sf.query("SELECT Lesson__c FROM Agent_Lesson__c WHERE Active__c = true AND Area__c IN "
                         "('planning', 'writing', 'visuals') ORDER BY CreatedDate DESC LIMIT 8") or []
        lessons = [r["Lesson__c"] for r in agent if r.get("Lesson__c")] + lessons
        ideas = sf.query("SELECT Id, Name, Anime__c FROM Topic_Idea__c WHERE Status__c = 'New' AND "
                         f"(Format__c = {soql_str(format_name)} OR Format__c = null) ORDER BY Votes__c DESC NULLS LAST, CreatedDate ASC LIMIT 1")
        idea = ideas[0] if ideas else None
    except Exception as exc:
        log(f"warning: could not read learning inputs ({exc})")
    return lessons, idea


def ensure_anime(sf, name: str, anilist_id: Optional[int]) -> bool:
    """Upsert the Anime__c record (keyed by AniList id) so Reels/facts can reference it."""
    if isinstance(sf, NullSalesforce) or not anilist_id:
        return False
    try:
        sf.upsert("Anime__c", "AniList_Id__c", str(anilist_id), {"Name": name[:80], "Active__c": True})
        return True
    except Exception as exc:
        log(f"warning: could not upsert Anime__c {name} ({exc})")
        return False


def sync_facts(sf, facts: List[dict], anilist_id: Optional[int]) -> None:
    """Mirror the facts a Reel used into Anime_Knowledge__c (small: only what's used)."""
    if isinstance(sf, NullSalesforce):
        return
    for f in facts:
        if f["id"].startswith("NEWS-"):
            continue
        fields = {
            "Name": f["title"][:80], "Content__c": f["content"][:2000], "Characters__c": ", ".join(f["characters"])[:255],
            "Category__c": f["category"], "Canon_Status__c": f["canon_status"], "Spoiler_Level__c": f["spoiler_level"],
            "Source_URL__c": f["source_url"], "Verified__c": f["verified"],
            "Verification_Method__c": "Automatic" if "auto-verified" in (f.get("notes") or "") else "Manual",
            "Last_Verified_Date__c": f.get("last_reviewed"),
        }
        if anilist_id:
            fields["Anime__r"] = {"AniList_Id__c": anilist_id}
        try:
            sf.upsert("Anime_Knowledge__c", "Knowledge_Id__c", f["id"], fields)
        except Exception as exc:
            log(f"warning: fact sync failed for {f['id']} ({exc})")


class TrialNotAvailable(RuntimeError):
    pass


MIN_GAP_MINUTES = 90      # never two posts closer than this (bursts look like "unusual activity" to Meta)
MAX_POSTS_PER_DAY = 6     # rolling 24 h (3 scheduled + room for breaking news / approvals)
MAX_WAIT_MINUTES = 35     # how long a job may wait for the gap before handing over


class SpacingHold(RuntimeError):
    pass


def posting_policy(sf) -> dict:
    """Admin-site Settings: post limit on/off, max per 24 h, minimum gap (defaults if unreachable)."""
    policy = {"enabled": True, "max_per_day": MAX_POSTS_PER_DAY, "min_gap_minutes": MIN_GAP_MINUTES}
    try:
        policy.update((sf.request("GET", "insta/research/brief", base="apexrest") or {}).get("posting") or {})
    except Exception as exc:
        log(f"warning: posting policy unavailable ({exc}); using defaults")
    return policy


def wait_for_posting_window(sf, sleep=time.sleep, policy: Optional[dict] = None) -> None:
    """Enforce post spacing (from Social_Post__c). Waits briefly if needed, else raises SpacingHold."""
    if isinstance(sf, NullSalesforce):
        return
    policy = policy or posting_policy(sf)
    if not policy.get("enabled", True):
        return
    max_per_day = int(policy.get("max_per_day") or MAX_POSTS_PER_DAY)
    min_gap = int(policy.get("min_gap_minutes") if policy.get("min_gap_minutes") is not None else MIN_GAP_MINUTES)
    rows = sf.query("SELECT Published_At__c FROM Social_Post__c WHERE Status__c = 'Published' AND "
                    "Published_At__c = LAST_N_DAYS:1 ORDER BY Published_At__c DESC LIMIT 30") or []
    now = datetime.now(timezone.utc)
    times = []
    for r in rows:
        try:
            times.append(datetime.fromisoformat(str(r["Published_At__c"]).replace("Z", "+00:00").replace("+0000", "+00:00")))
        except (KeyError, ValueError):
            continue
    recent = [t for t in times if (now - t).total_seconds() < 86400]
    if len(recent) >= max_per_day:
        frees = min(recent) + timedelta(hours=24)
        raise SpacingHold(f"{len(recent)} posts in the last 24 h (limit {max_per_day}); next free slot about "
                          f"{frees.astimezone(timezone(timedelta(hours=5, minutes=30))).strftime('%H:%M')} IST")
    if recent and min_gap:
        wait = min_gap * 60 - (now - max(recent)).total_seconds()
        if wait > MAX_WAIT_MINUTES * 60:
            raise SpacingHold(f"last post was {int((now - max(recent)).total_seconds() // 60)} min ago (minimum gap {min_gap} min)")
        if wait > 0:
            log(f"spacing: waiting {int(wait // 60) + 1} min after the previous post")
            sleep(wait + 5)


def publish_to_instagram(video: Path, caption: str, thumb_offset_ms: int, video_url: Optional[str] = None,
                         trial: bool = False, cover_url: Optional[str] = None, audio: Optional[dict] = None,
                         audio_video_url: Optional[str] = None) -> tuple:
    """Prefer handing Meta the public preview URL (reliable); fall back to direct upload,
    whose endpoint intermittently answers 'Request processing failed'.
    audio + audio_video_url: publish the narration-only cut with a licensed Instagram catalog track attached by
    Instagram (Audio API); if Instagram refuses, the normal cut with library music is posted instead.
    Returns (media_id, permalink, audio_used)."""
    from integrations.instagram import ContainerError, InstagramAPIError, client_from_env

    ig = client_from_env()
    extra = {"trial_strategy": "SS_PERFORMANCE"} if trial else {}
    # a designed cover (public JPEG) beats a frame offset; Meta ignores thumb_offset when cover_url is set
    extra.update({"cover_url": cover_url} if cover_url else {"thumb_offset_ms": thumb_offset_ms})
    result = None
    if audio and audio_video_url:
        try:
            result = ig.publish_reel(caption, video_url=audio_video_url, publish=True, audio=audio, **extra)
            if result.published and result.media_id:
                return result.media_id, result.permalink, True
        except (InstagramAPIError, ContainerError) as exc:
            if "publish:" in str(exc):
                raise  # the container was fine; don't risk a duplicate post via the fallback
            log(f"Instagram audio publish failed ({exc}); posting the library-music cut instead")
        result = None
    if video_url:
        try:
            result = ig.publish_reel(caption, video_url=video_url, publish=True, **extra)
        except (InstagramAPIError, ContainerError) as exc:
            if trial and isinstance(exc, InstagramAPIError) and exc.code == 10:
                raise TrialNotAvailable(str(exc)) from None  # account not eligible for Trial Reels yet
            if "publish:" in str(exc):
                raise  # the container was fine; don't risk a duplicate post via the fallback
            log(f"video_url publish failed ({exc}); trying direct upload")
    if result is None:
        result = ig.publish_reel(caption, video_path=str(video), publish=True, **extra)
    if not result.published or not result.media_id:
        raise RuntimeError("Instagram did not confirm publication")
    return result.media_id, result.permalink, False


def post_first_comment(media_id: str, text: str) -> None:
    """A question under the new Reel: early comments are a ranking signal. Never fails the publish."""
    try:
        from integrations.instagram import client_from_env

        client_from_env().comment(media_id, text)
        log(f"first comment: {text}")
    except Exception as exc:
        log(f"first comment skipped ({type(exc).__name__}: {str(exc)[:120]})")


def voice_only_cut(manifest: dict) -> Optional[Path]:
    """The same video with voice + SFX only (no library music), for a licensed Instagram track on top."""
    from video.render import mix_audio, run

    out = Path(manifest["video"]).parent
    voice, sfx = out / "voice.wav", out / "sfx.wav"
    if not voice.exists() or not sfx.exists():
        return None
    duration = float((manifest.get("validation") or {}).get("summary", {}).get("duration") or 0)
    try:
        mix_audio(voice, sfx, None, duration, out / "mix_voice.wav")
        dst = out / "video_voice.mp4"
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(manifest["video"]), "-i", str(out / "mix_voice.wav"),
             "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(dst)])
        return dst
    except Exception as exc:
        log(f"voice-only cut failed ({exc}); this Reel keeps library music")
        return None


UNCHECKED = "quality checker (Gemini) unavailable: held for review instead of posting unchecked"


def qa_review(manifest: dict, script: dict, quality: dict) -> List[str]:
    """Post-render QA Editor (code checks). Any issue makes the quality gate hold the Reel."""
    issues: List[str] = []
    duration = manifest["validation"]["summary"].get("duration") or 0
    if duration > 50:
        issues.append(f"too long ({duration:.0f}s; target 28-45s)")
    if duration < 20:
        issues.append(f"too short ({duration:.0f}s)")
    images = manifest.get("images") or []
    planned = [sc for sc in script["scenes"] if sc.get("image_source") == "official"]
    missing = sum(1 for sc, img in zip(script["scenes"], images) if sc.get("image_source") == "official" and not img)
    if planned and missing > len(planned) / 2:
        issues.append(f"{missing} of {len(planned)} official images missing")
    visual = manifest.get("visual_qa") or {}
    if os.environ.get("GEMINI_API_KEY") and visual.get("status") not in ("checked", "fixed (re-check unavailable)"):
        # owner's rule: every Reel passes the quality checker; never post unchecked
        issues.append(UNCHECKED)
    if visual.get("status") == "checked" and visual.get("issues"):
        issues.append("visual QA: " + "; ".join(visual["issues"])[:400])
    if visual.get("up_to_mark") is False or (visual.get("edit_score") is not None and visual["edit_score"] < 7):
        # the last real verdict counts - "re-check unavailable" never upgrades a below-bar edit
        issues.append(f"edit quality {visual.get('edit_score')}/10 (quality checker): " + "; ".join(visual.get("edit_notes") or [])[:300])
    fact_check = (quality or {}).get("fact_check") or {}
    if fact_check and not fact_check.get("pass", True):
        issues.append("fact checker flagged: " + "; ".join(str(i.get("problem", i)) for i in fact_check.get("issues", []))[:300])
    return issues


def repair_after_qa(sf, cid: str, format_name: str, plan, script: dict, manifest: dict, issues: List[str]):
    """QA found problems: hand them to the team. Returns (fixed script, new quality) or None if nothing to do.

    - official images missing -> those scenes switch to AI art (no LLM needed)
    - length / fact problems  -> Creative Director + Fact Checker revise the script (/insta/studio/revise)
    """
    from pipeline.writer import check_script, normalise

    fixed = json.loads(json.dumps(script))
    changed = False
    images = manifest.get("images") or []
    for scene, img in zip(fixed["scenes"], images):
        if scene.get("image_source") == "official" and not img:
            scene["image_source"] = "ai"
            changed = True
    script_issues = [i for i in issues if "official images" not in i]
    new_quality = None
    if script_issues and not isinstance(sf, NullSalesforce):
        facts = [{k: f.get(k) for k in ("id", "anime", "title", "content", "characters", "spoiler_level")} for f in plan.facts]
        try:
            result = sf.apex_post("insta/studio/revise", {"content_id": cid, "format": format_name, "anime": plan.anime,
                                                          "facts": facts, "script": fixed, "issues": script_issues})
        except Exception as exc:
            log(f"revise unavailable ({exc})")
            result = None
        if isinstance(result, dict) and isinstance(result.get("script"), dict):
            candidate = normalise(result["script"], cid, plan.anime, format_name)
            for key in ("voice", "speed", "mood", "music_keywords", "music_energy"):
                if key in fixed and key not in candidate:
                    candidate[key] = fixed[key]
            if not check_script(candidate, {f["id"] for f in plan.facts}):
                fixed, changed, new_quality = candidate, True, result
                log(f"team revised the script: score {result.get('quality', {}).get('overall')}, facts pass={result.get('fact_check', {}).get('pass')}")
    return (fixed, new_quality) if changed else None


def gate_decision(sf, content_id: str) -> tuple:
    """(hold, trial) read back after the Reel Quality Gate flow ran on our update."""
    if isinstance(sf, NullSalesforce):
        return False, False
    rows = sf.query("SELECT Approval_Status__c, Hold_Reason__c, Publish_As_Trial__c FROM Anime_Content__c "
                    f"WHERE External_Id__c = {soql_str(content_id)} LIMIT 1")
    if not rows:
        return False, False
    if rows[0].get("Hold_Reason__c"):
        log(f"quality gate: {rows[0].get('Hold_Reason__c')}")
    return rows[0].get("Approval_Status__c") != "Auto-Approved", bool(rows[0].get("Publish_As_Trial__c"))


TOPIC_STOP = {"anime", "about", "their", "there", "which", "could", "would", "every", "after", "before", "season",
              "episode", "chapter", "new", "the", "and", "with", "from", "that", "this", "what", "when", "fans"}


def topic_words(text: str, anime: str = "") -> set:
    import re

    skip = {w for w in re.findall(r"[a-z0-9]+", (anime or "").lower())}
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 3 and w not in TOPIC_STOP and w not in skip}


def recent_reels(sf, exclude: str = "", hours: int = 24) -> List[dict]:
    """Reels posted / rendered recently (newest first): anime, format, text for topic matching, age in hours."""
    try:
        rows = sf.query("SELECT External_Id__c, Anime__r.Name, Format__c, Topic__c, Hook__c, Strategy__c, CreatedDate "
                        "FROM Anime_Content__c WHERE Status__c IN ('Published', 'Rendered', 'Approved', 'Publishing') "
                        "AND CreatedDate = LAST_N_DAYS:2 ORDER BY CreatedDate DESC LIMIT 15") or []
    except Exception:
        return []
    now = datetime.now(timezone.utc)
    out = []
    for r in rows:
        if r.get("External_Id__c") == exclude:
            continue
        try:
            age = (now - datetime.fromisoformat(str(r["CreatedDate"]).replace("+0000", "+00:00"))).total_seconds() / 3600
        except (KeyError, ValueError):
            age = 0.0
        out.append({"anime": (r.get("Anime__r") or {}).get("Name") or "", "format": r.get("Format__c") or "", "age_h": age,
                    "text": " ".join(str(r.get(k) or "") for k in ("Topic__c", "Hook__c", "Strategy__c"))})
    return [r for r in out if r["age_h"] <= hours or r is out[0]]


def recent_topics(sf, hours: int = 24) -> List[str]:
    return [r["text"] for r in recent_reels(sf, hours=hours)]


def same_show(a: str, b: str) -> bool:
    wa, wb = topic_words(a), topic_words(b)
    return bool(a and b) and (a.casefold() == b.casefold() or (bool(wa) and wa == wb) or
                              (bool(wa & wb) and min(len(wa), len(wb)) == len(wa & wb)))


def choose_hot_topic(sf, hot: Optional[dict], recent: List[dict], breaking: bool = False) -> Optional[dict]:
    """Fallback only (the Showrunner's Planner normally decides each slot): views first, light variety.
    - never the exact topic covered in the last 24 h (a repeat Reel earns nothing);
    - a booming show (heat >= 8) may be covered again from a new angle, even back to back (max 2 per 24 h);
    - otherwise prefer a different show from the previous Reel / last 24 h.
    Falls through the brief's hot topics, then the cross-checked show ranking."""
    last_show = recent[0]["anime"] if recent else ""
    shows_24h = [r["anime"] for r in recent if r["age_h"] <= 24]
    covered = [r["text"] for r in recent if r["age_h"] <= 24]

    def topic_covered(t: dict) -> bool:
        words = topic_words(f"{t.get('topic', '')} {t.get('headline') or ''}", t.get("anime", ""))
        return bool(words) and any(len(words & topic_words(text)) >= min(2, len(words)) for text in covered)

    def ok(t: dict, strict: bool = True) -> bool:
        anime = str(t.get("anime") or "")
        if not anime or topic_covered(t):
            return False
        if (t.get("heat") or 0) >= 8 and sum(same_show(anime, s) for s in shows_24h) < 2:
            return True  # booming right now: views beat variety (but never a third Reel on one show in 24 h)
        if same_show(anime, last_show) or any(same_show(anime, s) for s in shows_24h):
            return breaking and (t.get("heat") or 0) >= 9 and not strict
        return True

    if hot and ok(hot, strict=False):
        return hot
    candidates: List[dict] = []
    try:
        rows = sf.query("SELECT Brief_JSON__c FROM Trend_Brief__c ORDER BY CreatedDate DESC LIMIT 1") or []
        brief = json.loads(rows[0]["Brief_JSON__c"]) if rows else {}
    except Exception:
        brief = {}
    candidates += sorted([t for t in brief.get("hot_topics") or [] if isinstance(t, dict)], key=lambda t: -(t.get("heat") or 0))
    for r in brief.get("show_ranking") or []:  # cross-checked shows (2+ sources) as topics
        if isinstance(r, dict) and r.get("hot"):
            head = r.get("headline") or {}
            candidates.append({"topic": head.get("title") or f"why fans are talking about {r['anime']}",
                               "anime": r["anime"], "heat": min(10, round(r.get("score", 0))), "headline": head.get("title"),
                               "source_url": head.get("url"), "evidence": "; ".join(r.get("evidence") or [])[:300]})
    for t in candidates:
        if ok(t):
            if hot:
                log(f"hot topic '{hot.get('topic')}' ({hot.get('anime')}) skipped for variety; "
                    f"next: '{t.get('topic')}' ({t.get('anime')}, heat {t.get('heat')})")
            return t
    log("no hot topic passes the variety rules; planning a regular Reel")
    return None


def uncovered_hot_topic(sf, hot: dict, covered: List[str]) -> Optional[dict]:
    """Back-compat wrapper: topic-only check (used by tests)."""
    recent = [{"anime": "", "format": "", "age_h": 1.0, "text": c} for c in covered]
    return choose_hot_topic(sf, hot, recent)


ALT_FORMATS = {  # when the planned format repeats the previous Reel's
    "Why It's Trending": ["Just Announced", "Hype Check"], "Just Announced": ["Why It's Trending", "Hype Check"],
    "Hype Check": ["Why It's Trending", "Just Announced"], "This Week's Drops": ["Why It's Trending", "Season Preview"],
    "Season Preview": ["Just Announced", "This Week's Drops"],
}
EVERGREEN_ORDER = ["Hidden Details", "Backstory", "Fact", "Fan Theory", "Who Would Win", "Ranking", "What If", "Quiz",
                   "Behind the Scenes"]


def vary_format(fmt: str, last_format: str, seed: str = "") -> str:
    """Never the same format twice in a row."""
    if not last_format or fmt != last_format:
        return fmt
    options = ALT_FORMATS.get(fmt) or [f for f in EVERGREEN_ORDER if f != fmt]
    return options[int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(options)] if seed else options[0]


def showrunner_directive(sf, cid: str) -> dict:
    """Instructions from the Showrunner agent when it ordered a remake of this Reel."""
    try:
        rows = sf.query(f"SELECT Showrunner_Directive__c FROM Anime_Content__c WHERE External_Id__c = {soql_str(cid)} LIMIT 1") or []
        return json.loads(rows[0]["Showrunner_Directive__c"]) if rows and rows[0].get("Showrunner_Directive__c") else {}
    except Exception:
        return {}


def report_feature_requests(sf, cid: str, requests: List[str]) -> None:
    """Editor requests the renderer cannot do yet: logged for the agents and the developers (never blocks the Reel)."""
    log(f"editor feature requests: {requests}")
    try:
        sf.request("POST", "sobjects/Agent_Activity__c", {
            "Agent__c": "Editor", "Action__c": "Feature request", "Reel__c": cid, "Needs_Owner__c": False,
            "Severity__c": "info", "Details__c": ("The agents asked the video editor for things it cannot do yet:\n- "
                                                  + "\n- ".join(requests))[:5000]})
    except Exception as exc:
        log(f"could not log feature requests ({type(exc).__name__})")


def render_report(manifest: dict, quality: dict, qa_issues: List[str]) -> dict:
    """What happened in this render, for the Showrunner agents (stored on the Reel)."""
    visual = dict(manifest.get("visual_qa") or {})
    visual.pop("renderer", None)
    quality = quality or {}
    return {
        "visual_qa": {k: visual.get(k) for k in ("status", "edit_score", "up_to_mark", "issues", "fixed_issues", "edit_notes",
                                                  "direction_score", "direction_notes",
                                                  "summary", "rounds", "checker")},
        "fact_check": quality.get("fact_check"),
        "director": {"score": (quality.get("quality") or {}).get("overall"), "notes": (quality.get("quality") or {}).get("notes")},
        "qa_issues": qa_issues,
        "duration_seconds": (manifest.get("validation") or {}).get("summary", {}).get("duration"),
        "shots_per_scene": manifest.get("shots"),
        "pictures_rejected": len(manifest.get("image_rejections") or []),
        "rejection_examples": (manifest.get("image_rejections") or [])[:5],
        "cover": manifest.get("designed_cover"),
        "effects_used": manifest.get("effects_used"),
        "music": (manifest.get("music") or {}).get("title") if isinstance(manifest.get("music"), dict) else manifest.get("music"),
    }


def recent_pictures(sf, limit: int = 10) -> List[str]:
    """Picture fingerprints used by the last Reels (stored in their Script_JSON__c)."""
    try:
        rows = sf.query("SELECT Script_JSON__c FROM Anime_Content__c WHERE Script_JSON__c != null AND Status__c IN "
                        f"('Published', 'Rendered', 'Approved') ORDER BY CreatedDate DESC LIMIT {int(limit)}") or []
    except Exception:
        return []
    out: List[str] = []
    for r in rows:
        try:
            out += json.loads(r["Script_JSON__c"]).get("picture_hashes") or []
        except (ValueError, TypeError, KeyError):
            continue
    return out


def recent_music(sf, limit: int = 20) -> List[str]:
    """Music_Choice__c of the last Reels ("title by creator - reason"): those tracks are not reused."""
    try:
        rows = sf.query("SELECT Music_Choice__c FROM Anime_Content__c WHERE Music_Choice__c != null "
                        f"ORDER BY CreatedDate DESC LIMIT {int(limit)}") or []
        return [r["Music_Choice__c"] for r in rows]
    except Exception as exc:
        log(f"warning: recent music unavailable ({exc})")
        return []


def music_picker_for(sf, script: dict, duration_hint: float = 40.0):
    """Music Supervisor agent (Salesforce) choosing among licensed candidates."""
    if isinstance(sf, NullSalesforce):
        return None
    state = {}

    def pick(candidates):
        from video.media import FORMAT_MUSIC

        brief = {"music_direction": FORMAT_MUSIC.get(script.get("format", ""), ("cinematic instrumental", []))[0],
                 "music_mood": script.get("mood"), "music_keywords": script.get("music_keywords", ""),
                 "music_energy": script.get("music_energy", ""), "format": script.get("format"), "video_seconds": duration_hint}
        result = sf.apex_post("insta/studio/music", {"brief": brief, "candidates": candidates})
        if not isinstance(result, dict) or "error" in result:
            raise RuntimeError(f"music supervisor unavailable: {result}")
        state["reason"] = result.get("reason")
        return result.get("pick")  # None = nothing fits

    pick.state = state
    return pick


def generate(args, sf) -> int:
    from pipeline.github_release import ReleaseStore
    from pipeline.llm import LLM
    from pipeline.planner import plan_reel
    from pipeline.writer import write_script
    from video.render import render

    cid, job = args.content_id, args.job_id
    from pipeline.live_log import install

    live = install(sf, job)  # streams the pipeline's key moments to the admin site's Live view

    def stage(name):
        sf.job_update(job, Stage__c=name)
        if live:
            live.add("stage", name)

    sf.job_update(job, Status__c="Running", Started_At__c=now_iso(), Run_URL__c=run_url(),
                  GitHub_Run_Id__c=os.environ.get("GITHUB_RUN_ID"), Stage__c="Planning")
    if live:
        live.add("stage", "Planning", "Reading the Showrunner's plan, trends and verified facts")
    sf.reel_update(cid, Status__c="Generating")

    from pipeline.llm import AgentforceLLM, ChainLLM

    try:
        cloudflare = LLM.from_env()
    except ValueError:
        cloudflare = None
    llm = ChainLLM([None if isinstance(sf, NullSalesforce) else AgentforceLLM(sf), cloudflare])
    recent_ids, recent_anime = recent_usage(sf)
    anime_hint, topic = args.anime or "", args.topic or ""

    directive = showrunner_directive(sf, cid)
    if directive:  # a remake ordered by the Showrunner agent: its instructions win over the usual planning
        args.anime = directive.get("show") or args.anime
        args.topic = directive.get("topic") or args.topic
        args.format = directive.get("format") or args.format
        anime_hint, topic = args.anime or "", args.topic or ""
        log(f"Showrunner directive: show={args.anime or '-'} topic={args.topic or '-'} format={args.format} "
            f"notes={str(directive.get('notes'))[:200]}")
    # Creative Strategist: what to make and how it should look (format, angle, length, style)
    decision = {}
    if not isinstance(sf, NullSalesforce):
        try:
            from pipeline.studio import strategy

            decision = strategy(sf, cid, args.format, args.slot, anime_hint, topic)
            log(f"strategist ({decision.get('mode')}): {decision['format']} / style '{decision['style']['name']}' / "
                f"{decision['length_seconds']}s / angle: {decision.get('angle')}")
            args.format = decision["format"]
        except Exception as exc:
            log(f"strategist unavailable ({type(exc).__name__}: {str(exc)[:200]}); using the default style")
    from video.style import resolve_style

    style = decision.get("style") or resolve_style(None)
    editor_requests: List[str] = []  # what the agents asked the editor for but it cannot do yet (feature requests)
    if directive.get("style"):
        from video.editing import style_overrides

        style, unknown = style_overrides(style, directive["style"])
        editor_requests += unknown
        log(f"Showrunner style overrides applied: {directive['style']}")
    angle = str(decision.get("angle") or "")
    lessons, idea = learning_inputs(sf, args.format)
    if directive:
        lessons = [f"SHOWRUNNER (must follow): {directive.get('notes')}"] + lessons
    hot_topic = decision.get("hot_topic") if isinstance(decision.get("hot_topic"), dict) else None
    history = recent_reels(sf, exclude=cid)
    if hot_topic or args.slot == "Morning":  # enforced in code: topic, show and per-day variety
        chosen = choose_hot_topic(sf, hot_topic, history, breaking=bool(decision.get("breaking")))
        if chosen is not hot_topic:
            angle = ""  # the strategist's angle was about a different topic
        hot_topic = chosen
    if directive:  # the Showrunner's remake instructions are final
        hot_topic, angle = None, ""
        if directive.get("show"):  # news formats must cover the show the Showrunner chose, not today's trending list
            hot_topic = {"anime": directive["show"], "topic": directive.get("topic") or directive["show"],
                         "headline": directive.get("topic"), "heat": 0}
        args.format = directive.get("format") or args.format
        anime_hint, topic = directive.get("show") or anime_hint, directive.get("topic") or topic
    last = history[0] if history else {}
    if decision.get("anime") and not hot_topic and same_show(str(decision["anime"]), last.get("anime", "")):
        log(f"strategist picked {decision['anime']} again right after the last Reel; letting the planner choose another show")
        decision["anime"], angle = None, ""
    new_format = args.format if directive.get("format") else vary_format(args.format, last.get("format", ""), cid)
    if new_format != args.format:
        log(f"format {args.format} repeats the previous Reel; using {new_format}")
        args.format = new_format
    if hot_topic and not args.anime and not args.topic and hot_topic.get("anime"):
        anime_hint, topic = str(hot_topic["anime"]), str(hot_topic.get("topic") or "")
        log(f"hot topic (heat {hot_topic.get('heat')}{', breaking' if decision.get('breaking') else ''}): "
            f"{topic} - {anime_hint}")
    elif not anime_hint and not topic and decision.get("anime"):
        anime_hint, topic = str(decision["anime"]), str(decision.get("topic") or "")
    if idea and not hot_topic and not anime_hint and not topic and args.format not in ("This Week's Drops", "Why It's Trending", "Hype Check", "Season Preview", "Just Announced"):
        anime_hint, topic = idea.get("Anime__c") or "", idea["Name"]
        log(f"using backlog idea: {idea['Name']} ({anime_hint})")
    else:
        idea = None
    plan = plan_reel(llm, args.format, anime_hint=anime_hint, topic=topic,
                     recent_ids=recent_ids, recent_anime=recent_anime, slot_seed=cid, strict_anime=bool(args.anime),
                     hot_topic=hot_topic)
    if plan.format != args.format:
        log(f"format changed by the planner: {args.format} -> {plan.format}")
        args.format = plan.format
    strategist_anime = str(decision.get("anime") or "")
    if angle and strategist_anime and strategist_anime.casefold() not in plan.anime.casefold() \
            and plan.anime.casefold() not in strategist_anime.casefold():
        log(f"strategist angle was about {strategist_anime}, but this Reel is about {plan.anime}; the writer picks its own angle")
        angle = ""
    if anime_hint and plan.anime.casefold() != anime_hint.casefold() and not args.anime:
        log(f"{anime_hint} lacked verified facts; the team switched to {plan.anime}")
        idea, topic, angle = None, "", ""  # the idea stays in the backlog for when we know more about it
    log(f"plan: {plan.format} about {plan.anime} with {len(plan.facts)} facts (ingested {plan.ingested})")

    stage("Writing script (agents)")
    script, source, quality = None, None, {}
    if not isinstance(sf, NullSalesforce):
        try:
            from pipeline.studio import agent_script

            result = agent_script(sf, cid, args.format, plan.anime, plan.facts, plan.context, topic, lessons,
                                  angle=angle, length_seconds=decision.get("length_seconds"), style=style)
            script, source, quality = result["script"], "Agentforce", result
            log(f"agents: score {result['quality'].get('overall')} in {result.get('calls')} calls; facts pass={result['fact_check'].get('pass')}")
        except Exception as exc:
            log(f"Agentforce studio unavailable ({type(exc).__name__}: {str(exc)[:200]})")
    if script is None:
        try:
            script = write_script(llm, cid, args.format, plan.anime, plan.facts, topic_hint=args.topic or angle, context=plan.context)
            source = "Cloudflare LLM"  # single-pass writer via the LLM chain (Agentforce task or Cloudflare)
        except Exception as exc:
            from pipeline.templates import template_script
            from pipeline.writer import check_script

            log(f"LLM writer unavailable ({type(exc).__name__}: {str(exc)[:160]}); using template writer")
            script, source = template_script(cid, args.format, plan.anime, plan.facts, plan.media, plan.context), "Template"
            problems = check_script(script, {f["id"] for f in plan.facts})
            if problems:
                raise RuntimeError("template script invalid: " + "; ".join(problems))
    log(f"script source: {source}")
    script["style"] = style
    if directive.get("edits"):
        from video.editing import apply_edits, normalize_edits

        edits, unsupported = normalize_edits(directive["edits"])
        editor_requests += unsupported
        log(f"Showrunner edits: {apply_edits(script, edits)} effect(s) placed; not supported yet: {unsupported or '-'}")
    if directive:  # the quality checker (Gemini) judges the Reel against this direction too
        script["direction"] = {"brief": str(directive.get("notes") or "")[:900], "topic": directive.get("topic"),
                               "effects": {str(i + 1) + ":" + str(sc.get("role")): sc.get("fx")
                                           for i, sc in enumerate(script.get("scenes") or []) if sc.get("fx")},
                               "style": directive.get("style")}
    if editor_requests:
        report_feature_requests(sf, cid, editor_requests)
    trending_sound = False  # the owner wants zero manual steps: always render with library music
    OUTPUT.mkdir(exist_ok=True)
    script_path = OUTPUT / f"{cid}.script.json"
    script_path.write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")

    stage("Rendering")
    picker = music_picker_for(sf, script)
    manifest, qa_issues = None, []
    for attempt in range(2):  # render -> QA -> (team repairs -> re-render) at most once
        # a trending sound is added by hand in the Instagram app, so the export carries voice + SFX only
        manifest = render(str(script_path), str(OUTPUT), extra_records=plan.extra_records, music_picker=picker,
                          music=not trending_sound, recent_music=recent_music(sf),
                          avoid_pictures=recent_pictures(sf))
        errors = manifest["validation"]["errors"]
        if errors:
            raise RuntimeError("video failed validation: " + "; ".join(errors))
        qa_issues = qa_review(manifest, script, quality)
        if not qa_issues:
            break
        log("QA issues: " + "; ".join(qa_issues))
        if qa_issues == [UNCHECKED]:
            break  # nothing to repair in the script: hold until a check is possible
        if attempt == 1:
            break
        stage("Team fixing QA issues")
        repaired = repair_after_qa(sf, cid, args.format, plan, script, manifest, qa_issues)
        if repaired is None:
            break
        script, extra_quality = repaired
        script["style"] = style  # the look stays the Strategist's decision through repairs
        quality = extra_quality or quality
        script_path.write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")
        log("re-rendering with the team's fixes")
    music = manifest.get("music")
    music_choice = None
    if isinstance(music, dict):
        reason = (picker.state.get("reason") if picker else None) or "stable pick"
        music_choice = f"{music.get('title')} by {music.get('creator')} - {reason}"[:255]
        log(f"music: {music_choice}")
    out_dir = Path(manifest["video"]).parent

    trending = None  # a licensed Instagram catalog track (Audio API), attached by Instagram at publish time
    try:
        from pipeline.growth import choose_trending, search_caption
        from video.audio_library import FORMAT_MOODS

        manifest["caption"] = search_caption(manifest["caption"], plan.anime)
        trending = choose_trending(sf, FORMAT_MOODS.get(args.format) or ["epic"], recent_music(sf), cid)
    except Exception as exc:
        log(f"trending audio unavailable ({type(exc).__name__}: {str(exc)[:120]})")
    voice_cut = voice_only_cut(manifest) if trending else None
    if trending and voice_cut is None:
        trending = None

    stage("Uploading preview")
    urls = {}
    store = ReleaseStore.from_env()
    if store:
        try:
            assets = {f"{cid}.mp4": Path(manifest["video"]), f"{cid}.jpg": Path(manifest["cover"])}
            if voice_cut:
                assets[f"{cid}-voice.mp4"] = voice_cut
            from pipeline.growth import story_teaser_cut

            teaser = Path(manifest["video"]).parent / "story.mp4"
            if story_teaser_cut(manifest["video"], str(teaser)):
                assets[f"{cid}-story.mp4"] = teaser
            urls = store.upload(assets)
            store.prune()
        except Exception as exc:  # no preview link: Instagram still gets the file by direct upload
            log(f"warning: preview upload failed ({type(exc).__name__}: {str(exc)[:160]}); continuing without it")
    if idea:
        try:
            sf.request("PATCH", f"sobjects/Topic_Idea__c/{idea['Id']}", {"Status__c": "Used", "Used_By__r": {"External_Id__c": cid}})
        except Exception as exc:
            log(f"warning: could not mark idea used ({exc})")
    used = [f for f in plan.facts if f["id"] in set(manifest["knowledge_ids"])]
    linked = ensure_anime(sf, plan.anime, plan.anilist_id)
    sync_facts(sf, used, plan.anilist_id if linked else None)

    script["picture_hashes"] = manifest.get("picture_hashes") or []  # the next Reels won't reuse these pictures
    hook = script["scenes"][0]["narration"].split(". ")[0][:250]
    fields = {
        "Name": script["title"][:80], "Topic__c": script["title"][:255], "Hook__c": hook, "Format__c": args.format,
        "Script_JSON__c": json.dumps(script, ensure_ascii=False)[:131000],
        "Caption__c": manifest["caption"][:5000], "Hashtags__c": " ".join(script["hashtags"])[:255],
        "Knowledge_Ids__c": ",".join(manifest["knowledge_ids"])[:255], "Sources__c": (manifest.get("sources") or "")[:2000],
        "Cover_URL__c": urls.get(f"{cid}.jpg"), "Video_URL__c": urls.get(f"{cid}.mp4"),
        "Duration_Seconds__c": manifest["validation"]["summary"].get("duration"), "Status__c": "Rendered",
        "Error_Message__c": None, "Script_Source__c": source, "Music_Choice__c": music_choice, "QA_Issues__c": "\n".join(qa_issues)[:2000] or None,
        "Render_Report__c": json.dumps(dict(render_report(manifest, quality, qa_issues),
                                            trending_audio=dict(trending, video_url=urls.get(f"{cid}-voice.mp4")) if trending else None),
                                       ensure_ascii=False)[:131000],
    }
    if trending:
        log(f"Instagram trending audio planned: {trending.get('title')} - {trending.get('artist')} ({trending.get('mood')})")
    if linked:
        fields["Anime__r"] = {"AniList_Id__c": plan.anilist_id}
    if not manifest["publishable"]:
        raise RuntimeError(f"not publishable: unverified={manifest['unverified_knowledge_ids']} missing={manifest['missing_knowledge_ids']}")

    if trending_sound:
        fields.update(Approval_Status__c="Pending", Needs_Trending_Sound__c=True,
                      Hold_Reason__c="Post manually with a trending sound: download the video from the admin site, "
                                     "add the sound in the Instagram app (rendered without music).")
        sf.reel_update(cid, **fields)
        sf.job_update(job, Status__c="Succeeded", Stage__c="Waiting: post with a trending sound", Completed_At__c=now_iso())
        log("rendered without music; waiting for a manual post with a trending sound")
        return 0

    if not truthy(args.auto_approve):
        fields["Approval_Status__c"] = "Pending"
        sf.reel_update(cid, **fields)
        sf.job_update(job, Status__c="Succeeded", Stage__c="Waiting for approval", Completed_At__c=now_iso())
        log("rendered; waiting for manual approval in Salesforce")
        return 0

    fields["Approval_Status__c"] = "Auto-Approved"
    if truthy(args.dry_run):
        fields["Error_Message__c"] = "Dry run: rendered, not posted to Instagram."
    sf.reel_update(cid, **fields)
    hold, trial = gate_decision(sf, cid)  # the gate runs in dry runs too: a hold wakes the Showrunner
    if hold:
        sf.job_update(job, Status__c="Succeeded", Stage__c="Held for review by the quality gate", Completed_At__c=now_iso())
        log("held for review by the Salesforce quality gate (the Showrunner agent takes it from here)")
        return 0
    if truthy(args.dry_run):
        sf.job_update(job, Status__c="Succeeded", Stage__c="Dry run complete", Completed_At__c=now_iso())
        log("dry run: passed the gate, not publishing")
        return 0
    try:
        wait_for_posting_window(sf)
    except SpacingHold as exc:
        reason = f"Posting limit: {exc}. Posts automatically when the window opens."
        sf.reel_update(cid, Status__c="Rendered", Approval_Status__c="Pending", Hold_Reason__c=reason)
        sf.job_update(job, Status__c="Succeeded", Stage__c="Waiting: post spacing", Completed_At__c=now_iso())
        log(reason)
        return 0
    stage("Publishing to Instagram" + (" as a Trial Reel" if trial else ""))
    sf.reel_update(cid, Status__c="Publishing")
    try:
        media_id, permalink, audio_used = publish_to_instagram(
            Path(manifest["video"]), manifest["caption"], manifest["thumb_offset_ms"], urls.get(f"{cid}.mp4"), trial=trial,
            cover_url=urls.get(f"{cid}.jpg") if manifest.get("designed_cover") else None,
            audio=trending, audio_video_url=urls.get(f"{cid}-voice.mp4"))
    except TrialNotAvailable as exc:  # no hold: post it as a normal Reel
        log(f"Trial Reels not available ({exc}); posting as a normal Reel")
        trial = False
        sf.reel_update(cid, Publish_As_Trial__c=False)
        media_id, permalink, audio_used = publish_to_instagram(
            Path(manifest["video"]), manifest["caption"], manifest["thumb_offset_ms"], urls.get(f"{cid}.mp4"), trial=False,
            cover_url=urls.get(f"{cid}.jpg") if manifest.get("designed_cover") else None,
            audio=trending, audio_video_url=urls.get(f"{cid}-voice.mp4"))
    sf.record_post(cid, media_id, permalink, trial=trial)
    sf.reel_update(cid, Status__c="Published", **({"Music_Choice__c": (f"Instagram trending audio: {trending.get('title')} - "
                                                                       f"{trending.get('artist')} [{trending['audio_id']}]")[:255]}
                                                    if audio_used else {}))
    from pipeline.growth import first_comment, post_story_teaser
    post_first_comment(media_id, first_comment(script, args.format))
    post_story_teaser(urls.get(f"{cid}-story.mp4"))
    from integrations.facebook import cross_post_reel

    cross_post_reel(urls.get(f"{cid}.mp4"), manifest["caption"])  # off unless FB_PAGE_ID is configured
    from integrations.youtube import upload_short

    upload_short(Path(manifest["video"]), manifest["caption"], plan.anime)  # off unless YT_REFRESH_TOKEN is configured
    sf.job_update(job, Status__c="Succeeded", Stage__c="Published", Completed_At__c=now_iso())
    log(f"published: {permalink}")
    return 0


def publish(args, sf) -> int:
    """Manual-approval path: download the rendered preview and publish it."""
    from pipeline.github_release import ReleaseStore

    cid, job = args.content_id, args.job_id
    from pipeline.live_log import install

    live = install(sf, job)
    sf.job_update(job, Status__c="Running", Started_At__c=now_iso(), Run_URL__c=run_url(),
                  GitHub_Run_Id__c=os.environ.get("GITHUB_RUN_ID"), Stage__c="Downloading")
    if live:
        live.add("stage", "Publishing", "Approved by the Editor agent - posting to Instagram")
    rows = [] if isinstance(sf, NullSalesforce) else sf.query(
        f"SELECT Caption__c, Video_URL__c, Cover_URL__c, Render_Report__c, Script_JSON__c, Format__c FROM Anime_Content__c "
        f"WHERE External_Id__c = '{cid}' LIMIT 1")
    if not rows:
        raise RuntimeError(f"Reel {cid} not found in Salesforce")
    store = ReleaseStore.from_env()
    if not store:
        raise RuntimeError("GITHUB_TOKEN/GITHUB_REPOSITORY needed to fetch the preview")
    video = store.download(f"{cid}.mp4", OUTPUT / cid / "video.mp4")
    if truthy(args.dry_run):
        sf.reel_update(cid, Status__c="Rendered", Error_Message__c="Dry run: approved but not posted.")
        sf.job_update(job, Status__c="Succeeded", Stage__c="Dry run complete", Completed_At__c=now_iso())
        return 0
    try:  # owner-approved posts still respect the posting limit - held (not failed) when over it
        wait_for_posting_window(sf)
    except SpacingHold as exc:
        reason = f"Posting limit: {exc}. Posts automatically when the window opens."
        sf.reel_update(cid, Status__c="Rendered", Approval_Status__c="Pending", Hold_Reason__c=reason)
        sf.job_update(job, Status__c="Succeeded", Stage__c="Held: posting limit", Completed_At__c=now_iso())
        log(reason)
        return 0
    sf.job_update(job, Stage__c="Publishing to Instagram")
    try:
        trending = (json.loads(rows[0].get("Render_Report__c") or "{}") or {}).get("trending_audio")
    except ValueError:
        trending = None
    media_id, permalink, audio_used = publish_to_instagram(
        video, rows[0]["Caption__c"] or "", 900, rows[0].get("Video_URL__c"), cover_url=rows[0].get("Cover_URL__c"),
        audio=trending, audio_video_url=(trending or {}).get("video_url"))
    sf.record_post(cid, media_id, permalink)
    sf.reel_update(cid, Status__c="Published", Error_Message__c=None,
                   **({"Music_Choice__c": f"Instagram trending audio: {trending.get('title')} - {trending.get('artist')} "
                                          f"[{trending['audio_id']}]"[:255]} if audio_used else {}))
    try:
        from pipeline.growth import first_comment

        post_first_comment(media_id, first_comment(json.loads(rows[0].get("Script_JSON__c") or "{}"), rows[0].get("Format__c") or ""))
    except ValueError:
        pass
    from pipeline.growth import post_story_teaser

    main_url = rows[0].get("Video_URL__c") or ""
    post_story_teaser(main_url.replace(f"{cid}.mp4", f"{cid}-story.mp4") if main_url.endswith(f"{cid}.mp4") else None)
    from integrations.facebook import cross_post_reel

    cross_post_reel(main_url, rows[0]["Caption__c"] or "")  # off unless FB_PAGE_ID is configured
    from integrations.youtube import upload_short

    upload_short(video, rows[0]["Caption__c"] or "")  # off unless YT_REFRESH_TOKEN is configured
    sf.job_update(job, Status__c="Succeeded", Stage__c="Published", Completed_At__c=now_iso())
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run a Reel job")
    parser.add_argument("command", choices=["generate", "publish"])
    parser.add_argument("--content-id", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--format", default="Fact")
    parser.add_argument("--anime", default="")
    parser.add_argument("--topic", default="")
    parser.add_argument("--slot", default="")
    parser.add_argument("--auto-approve", default="true")
    parser.add_argument("--dry-run", default="true")
    args = parser.parse_args(argv)

    sf = salesforce()
    try:
        return generate(args, sf) if args.command == "generate" else publish(args, sf)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"[:4000]
        log("FAILED " + message)
        traceback.print_exc()
        try:
            sf.job_update(args.job_id, Status__c="Failed", Error_Message__c=message, Completed_At__c=now_iso())
            sf.reel_update(args.content_id, Status__c="Failed", Error_Message__c=message)
        except Exception as cb_exc:  # never hide the original failure
            log(f"warning: failure callback also failed ({cb_exc})")
        return 1


if __name__ == "__main__":
    sys.exit(main())
