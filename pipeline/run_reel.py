"""End-to-end Reel job, run by GitHub Actions (reel.yml / publish.yml).

  python -m pipeline.run_reel generate --content-id R-20261125-AM --job-id ... --format Quiz \
         [--anime "One Piece"] [--topic "..."] [--auto-approve true] [--dry-run true]
  python -m pipeline.run_reel publish --content-id R-20261125-AM --job-id ... [--dry-run false]

Every stage reports to Salesforce (Video_Job__c / Anime_Content__c / Social_Post__c).
A Reel is only published if Meta returns a media id AND all cited facts are verified.
"""

from __future__ import annotations

import argparse
import json
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


def publish_to_instagram(video: Path, caption: str, thumb_offset_ms: int, video_url: Optional[str] = None,
                         trial: bool = False) -> tuple:
    """Prefer handing Meta the public preview URL (reliable); fall back to direct upload,
    whose endpoint intermittently answers 'Request processing failed'."""
    from integrations.instagram import ContainerError, InstagramAPIError, client_from_env

    ig = client_from_env()
    extra = {"trial_strategy": "SS_PERFORMANCE"} if trial else {}
    result = None
    if video_url:
        try:
            result = ig.publish_reel(caption, video_url=video_url, publish=True, thumb_offset_ms=thumb_offset_ms, **extra)
        except (InstagramAPIError, ContainerError) as exc:
            if trial and isinstance(exc, InstagramAPIError) and exc.code == 10:
                raise TrialNotAvailable(str(exc)) from None  # account not eligible for Trial Reels yet
            if "publish:" in str(exc):
                raise  # the container was fine; don't risk a duplicate post via the fallback
            log(f"video_url publish failed ({exc}); trying direct upload")
    if result is None:
        result = ig.publish_reel(caption, video_path=str(video), publish=True, thumb_offset_ms=thumb_offset_ms, **extra)
    if not result.published or not result.media_id:
        raise RuntimeError("Instagram did not confirm publication")
    return result.media_id, result.permalink


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
    if visual.get("status") == "checked" and visual.get("issues"):
        issues.append("visual QA: " + "; ".join(visual["issues"])[:400])
    if visual.get("status") == "checked" and visual.get("up_to_mark") is False:
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
    stage = lambda name: sf.job_update(job, Stage__c=name)  # noqa: E731
    sf.job_update(job, Status__c="Running", Started_At__c=now_iso(), Run_URL__c=run_url(),
                  GitHub_Run_Id__c=os.environ.get("GITHUB_RUN_ID"), Stage__c="Planning")
    sf.reel_update(cid, Status__c="Generating")

    from pipeline.llm import AgentforceLLM, ChainLLM

    try:
        cloudflare = LLM.from_env()
    except ValueError:
        cloudflare = None
    llm = ChainLLM([None if isinstance(sf, NullSalesforce) else AgentforceLLM(sf), cloudflare])
    recent_ids, recent_anime = recent_usage(sf)
    anime_hint, topic = args.anime or "", args.topic or ""

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
    angle = str(decision.get("angle") or "")
    lessons, idea = learning_inputs(sf, args.format)
    if not anime_hint and not topic and decision.get("anime"):
        anime_hint, topic = str(decision["anime"]), str(decision.get("topic") or "")
    if idea and not anime_hint and not topic and args.format not in ("This Week's Drops", "Why It's Trending", "Hype Check", "Season Preview", "Just Announced"):
        anime_hint, topic = idea.get("Anime__c") or "", idea["Name"]
        log(f"using backlog idea: {idea['Name']} ({anime_hint})")
    else:
        idea = None
    plan = plan_reel(llm, args.format, anime_hint=anime_hint, topic=topic,
                     recent_ids=recent_ids, recent_anime=recent_anime, slot_seed=cid, strict_anime=bool(args.anime))
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
    trending_sound = bool(decision.get("needs_trending_sound"))
    OUTPUT.mkdir(exist_ok=True)
    script_path = OUTPUT / f"{cid}.script.json"
    script_path.write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")

    stage("Rendering")
    picker = music_picker_for(sf, script)
    manifest, qa_issues = None, []
    for attempt in range(2):  # render -> QA -> (team repairs -> re-render) at most once
        # a trending sound is added by hand in the Instagram app, so the export carries voice + SFX only
        manifest = render(str(script_path), str(OUTPUT), extra_records=plan.extra_records, music_picker=picker,
                          music=not trending_sound, recent_music=recent_music(sf))
        errors = manifest["validation"]["errors"]
        if errors:
            raise RuntimeError("video failed validation: " + "; ".join(errors))
        qa_issues = qa_review(manifest, script, quality)
        if not qa_issues:
            break
        log("QA issues: " + "; ".join(qa_issues))
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

    stage("Uploading preview")
    urls = {}
    store = ReleaseStore.from_env()
    if store:
        try:
            urls = store.upload({f"{cid}.mp4": Path(manifest["video"]), f"{cid}.jpg": Path(manifest["cover"])})
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

    hook = script["scenes"][0]["narration"].split(". ")[0][:250]
    fields = {
        "Name": script["title"][:80], "Topic__c": script["title"][:255], "Hook__c": hook,
        "Script_JSON__c": json.dumps(script, ensure_ascii=False)[:131000],
        "Caption__c": manifest["caption"][:5000], "Hashtags__c": " ".join(script["hashtags"])[:255],
        "Knowledge_Ids__c": ",".join(manifest["knowledge_ids"])[:255], "Sources__c": (manifest.get("sources") or "")[:2000],
        "Cover_URL__c": urls.get(f"{cid}.jpg"), "Video_URL__c": urls.get(f"{cid}.mp4"),
        "Duration_Seconds__c": manifest["validation"]["summary"].get("duration"), "Status__c": "Rendered",
        "Error_Message__c": None, "Script_Source__c": source, "Music_Choice__c": music_choice, "QA_Issues__c": "\n".join(qa_issues)[:2000] or None,
    }
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
        fields["Error_Message__c"] = "Dry run: rendered and approved, not posted to Instagram."
        sf.reel_update(cid, **fields)
        sf.job_update(job, Status__c="Succeeded", Stage__c="Dry run complete", Completed_At__c=now_iso())
        log("dry run: not publishing")
        return 0

    sf.reel_update(cid, **fields)
    hold, trial = gate_decision(sf, cid)
    if hold:
        sf.job_update(job, Status__c="Succeeded", Stage__c="Held for review by the quality gate", Completed_At__c=now_iso())
        log("held for review by the Salesforce quality gate")
        return 0
    stage("Publishing to Instagram" + (" as a Trial Reel" if trial else ""))
    sf.reel_update(cid, Status__c="Publishing")
    try:
        media_id, permalink = publish_to_instagram(Path(manifest["video"]), manifest["caption"], manifest["thumb_offset_ms"],
                                                   urls.get(f"{cid}.mp4"), trial=trial)
    except TrialNotAvailable as exc:
        reason = "Trial Reels are not available for this account yet (Instagram eligibility); held for your review."
        sf.reel_update(cid, Status__c="Rendered", Approval_Status__c="Pending", Publish_As_Trial__c=False, Hold_Reason__c=reason)
        sf.job_update(job, Status__c="Succeeded", Stage__c="Held: trial not available", Completed_At__c=now_iso())
        log(f"{reason} ({exc})")
        return 0
    sf.record_post(cid, media_id, permalink, trial=trial)
    sf.reel_update(cid, Status__c="Published")
    sf.job_update(job, Status__c="Succeeded", Stage__c="Published", Completed_At__c=now_iso())
    log(f"published: {permalink}")
    return 0


def publish(args, sf) -> int:
    """Manual-approval path: download the rendered preview and publish it."""
    from pipeline.github_release import ReleaseStore

    cid, job = args.content_id, args.job_id
    sf.job_update(job, Status__c="Running", Started_At__c=now_iso(), Run_URL__c=run_url(),
                  GitHub_Run_Id__c=os.environ.get("GITHUB_RUN_ID"), Stage__c="Downloading")
    rows = [] if isinstance(sf, NullSalesforce) else sf.query(
        f"SELECT Caption__c, Video_URL__c FROM Anime_Content__c WHERE External_Id__c = '{cid}' LIMIT 1")
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
    sf.job_update(job, Stage__c="Publishing to Instagram")
    media_id, permalink = publish_to_instagram(video, rows[0]["Caption__c"] or "", 900, rows[0].get("Video_URL__c"))
    sf.record_post(cid, media_id, permalink)
    sf.reel_update(cid, Status__c="Published", Error_Message__c=None)
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
