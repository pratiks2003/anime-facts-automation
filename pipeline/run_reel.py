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


def publish_to_instagram(video: Path, caption: str, thumb_offset_ms: int, video_url: Optional[str] = None) -> tuple:
    """Prefer handing Meta the public preview URL (reliable); fall back to direct upload,
    whose endpoint intermittently answers 'Request processing failed'."""
    from integrations.instagram import ContainerError, InstagramAPIError, client_from_env

    ig = client_from_env()
    result = None
    if video_url:
        try:
            result = ig.publish_reel(caption, video_url=video_url, publish=True, thumb_offset_ms=thumb_offset_ms)
        except (InstagramAPIError, ContainerError) as exc:
            if "publish:" in str(exc):
                raise  # the container was fine; don't risk a duplicate post via the fallback
            log(f"video_url publish failed ({exc}); trying direct upload")
    if result is None:
        result = ig.publish_reel(caption, video_path=str(video), publish=True, thumb_offset_ms=thumb_offset_ms)
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
    fact_check = (quality or {}).get("fact_check") or {}
    if fact_check and not fact_check.get("pass", True):
        issues.append("fact checker flagged: " + "; ".join(str(i.get("problem", i)) for i in fact_check.get("issues", []))[:300])
    return issues


def gate_holds(sf, content_id: str) -> bool:
    """Read back the Reel after the Reel Quality Gate flow ran on our update."""
    if isinstance(sf, NullSalesforce):
        return False
    rows = sf.query(f"SELECT Approval_Status__c, Hold_Reason__c FROM Anime_Content__c WHERE External_Id__c = '{content_id}' LIMIT 1")
    if rows and rows[0].get("Approval_Status__c") != "Auto-Approved":
        log(f"quality gate: {rows[0].get('Hold_Reason__c')}")
        return True
    return False


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
    lessons, idea = learning_inputs(sf, args.format)
    anime_hint, topic = args.anime or "", args.topic or ""
    if idea and not anime_hint and not topic and args.format not in ("This Week's Drops", "Why It's Trending", "Hype Check", "Season Preview", "Just Announced"):
        anime_hint, topic = idea.get("Anime__c") or "", idea["Name"]
        log(f"using backlog idea: {idea['Name']} ({anime_hint})")
    else:
        idea = None
    plan = plan_reel(llm, args.format, anime_hint=anime_hint, topic=topic,
                     recent_ids=recent_ids, recent_anime=recent_anime, slot_seed=cid)
    log(f"plan: {plan.format} about {plan.anime} with {len(plan.facts)} facts (ingested {plan.ingested})")

    stage("Writing script (agents)")
    script, source, quality = None, None, {}
    if not isinstance(sf, NullSalesforce):
        try:
            from pipeline.studio import agent_script

            result = agent_script(sf, cid, args.format, plan.anime, plan.facts, plan.context, topic, lessons)
            script, source, quality = result["script"], "Agentforce", result
            log(f"agents: score {result['quality'].get('overall')} in {result.get('calls')} calls; facts pass={result['fact_check'].get('pass')}")
        except Exception as exc:
            log(f"Agentforce studio unavailable ({type(exc).__name__}: {str(exc)[:200]})")
    if script is None:
        try:
            script = write_script(llm, cid, args.format, plan.anime, plan.facts, topic_hint=args.topic or "", context=plan.context)
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
    OUTPUT.mkdir(exist_ok=True)
    script_path = OUTPUT / f"{cid}.script.json"
    script_path.write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")

    stage("Rendering")
    manifest = render(str(script_path), str(OUTPUT), extra_records=plan.extra_records)
    errors = manifest["validation"]["errors"]
    if errors:
        raise RuntimeError("video failed validation: " + "; ".join(errors))
    qa_issues = qa_review(manifest, script, quality)
    if qa_issues:
        log("QA issues: " + "; ".join(qa_issues))
    out_dir = Path(manifest["video"]).parent

    stage("Uploading preview")
    urls = {}
    store = ReleaseStore.from_env()
    if store:
        urls = store.upload({f"{cid}.mp4": Path(manifest["video"]), f"{cid}.jpg": Path(manifest["cover"])})
        store.prune()
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
        "Error_Message__c": None, "Script_Source__c": source, "QA_Issues__c": "\n".join(qa_issues)[:2000] or None,
    }
    if linked:
        fields["Anime__r"] = {"AniList_Id__c": plan.anilist_id}
    if not manifest["publishable"]:
        raise RuntimeError(f"not publishable: unverified={manifest['unverified_knowledge_ids']} missing={manifest['missing_knowledge_ids']}")

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
    if gate_holds(sf, cid):
        sf.job_update(job, Status__c="Succeeded", Stage__c="Held for review by the quality gate", Completed_At__c=now_iso())
        log("held for review by the Salesforce quality gate")
        return 0
    stage("Publishing to Instagram")
    sf.reel_update(cid, Status__c="Publishing")
    media_id, permalink = publish_to_instagram(Path(manifest["video"]), manifest["caption"], manifest["thumb_offset_ms"],
                                               urls.get(f"{cid}.mp4"))
    sf.record_post(cid, media_id, permalink)
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
