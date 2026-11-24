"""Daily Instagram metrics sync -> Salesforce Social_Post__c (run by metrics.yml).

  python -m pipeline.metrics [--days 30]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from integrations.instagram import InstagramAPIError, client_from_env
from integrations.salesforce import SalesforceClient, now_iso


def review_comments(sf, ig, media_id: str, row: dict, stats: dict) -> None:
    """New comments -> Community Manager agent (reply drafts + topic ideas)."""
    try:
        comments = ig.recent_comments(media_id)
        if not comments:
            return
        content = row.get("Content__r") or {}
        reel = {"format": content.get("Format__c"), "anime": (content.get("Anime__r") or {}).get("Name"), "hook": content.get("Hook__c")}
        result = sf.apex_post("insta/studio/comments", {"media_id": media_id, "reel": reel, "comments": comments})
        stats["comment_reviews"] = stats.get("comment_reviews", 0) + 1
        stats["ideas_from_comments"] = stats.get("ideas_from_comments", 0) + int((result or {}).get("ideas_added") or 0)
    except Exception as exc:
        print(f"[metrics] comment review skipped for {media_id}: {exc}")


def sync(days: int = 30) -> dict:
    sf = SalesforceClient.from_env()
    if sf is None:
        raise SystemExit("Salesforce is not configured (SF_DOMAIN / SF_CLIENT_ID / SF_CLIENT_SECRET)")
    ig = client_from_env()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    posts = sf.query("SELECT Instagram_Media_Id__c, Comments__c, Comments_Reviewed_At__c, Content__r.Format__c, "
                     "Content__r.Anime__r.Name, Content__r.Hook__c FROM Social_Post__c WHERE Status__c = 'Published' "
                     f"AND Published_At__c >= {since} ORDER BY Published_At__c DESC LIMIT 60")
    stats = {"posts": len(posts), "updated": 0, "insights": False, "errors": 0}
    for row in posts:
        media_id = row["Instagram_Media_Id__c"]
        try:
            m = ig.media_metrics(media_id)
        except InstagramAPIError as exc:
            stats["errors"] += 1
            print(f"[metrics] {media_id}: {exc}")
            continue
        fields = {"Likes__c": m.get("likes"), "Comments__c": m.get("comments"), "Metrics_Updated_At__c": now_iso()}
        if m.get("insights_available"):
            stats["insights"] = True
            fields.update({"Views__c": m.get("views"), "Reach__c": m.get("reach"), "Shares__c": m.get("shares"),
                           "Saves__c": m.get("saves"), "Avg_Watch_Time_Ms__c": m.get("avg_watch_ms")})
        sf.upsert("Social_Post__c", "Instagram_Media_Id__c", media_id, {k: v for k, v in fields.items() if v is not None})
        stats["updated"] += 1
        if (m.get("comments") or 0) > (row.get("Comments__c") or 0) or ((m.get("comments") or 0) and not row.get("Comments_Reviewed_At__c")):
            review_comments(sf, ig, media_id, row, stats)
    return stats


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args(argv)
    stats = sync(args.days)
    print(f"[metrics] {stats}")
    if not stats["insights"] and stats["posts"]:
        print("[metrics] Insights unavailable: add the instagram_manage_insights permission to the Meta login configuration")
    return 1 if stats["errors"] and not stats["updated"] else 0


if __name__ == "__main__":
    sys.exit(main())
