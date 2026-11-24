"""Auto-reply to Instagram comments (run every 2 hours by comments.yml).

For each Reel from the last N days: fetch comments -> Salesforce Community Manager agent writes a
reply for every NEW comment (spam/unsafe are skipped and logged) -> post replies with pacing ->
mark each Comment_Reply__c Posted / Failed.

  python -m pipeline.comments [--days 14] [--max-replies 30]
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone

from integrations.instagram import InstagramAPIError, client_from_env
from integrations.salesforce import SalesforceClient, now_iso


def run(days: int = 14, max_replies: int = 30, pause: float = 4.0, sleep=time.sleep) -> dict:
    sf = SalesforceClient.from_env()
    if sf is None:
        raise SystemExit("Salesforce is not configured")
    ig = client_from_env()
    own = ig.account().get("username")
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    posts = sf.query("SELECT Instagram_Media_Id__c, Content__r.Format__c, Content__r.Anime__r.Name, Content__r.Hook__c "
                     f"FROM Social_Post__c WHERE Status__c = 'Published' AND Published_At__c >= {since} "
                     "ORDER BY Published_At__c DESC LIMIT 30")
    stats = {"posts": len(posts), "comments_seen": 0, "replied": 0, "failed": 0, "ideas": 0, "auto_reply": None}
    for post in posts:
        if stats["replied"] >= max_replies:
            break
        media_id = post["Instagram_Media_Id__c"]
        try:
            comments = ig.recent_comments(media_id, own_username=own)
        except InstagramAPIError as exc:
            print(f"[comments] {media_id}: cannot read comments ({exc})")
            continue
        stats["comments_seen"] += len(comments)
        if not comments:
            continue
        content = post.get("Content__r") or {}
        reel = {"format": content.get("Format__c"), "anime": (content.get("Anime__r") or {}).get("Name"), "hook": content.get("Hook__c")}
        result = sf.apex_post("insta/studio/comments", {"media_id": media_id, "reel": reel,
                                                        "comments": [{k: c[k] for k in ("id", "text", "likes")} for c in comments]}) or {}
        if "error" in result:
            print(f"[comments] agent error for {media_id}: {result['error']}")
            continue
        stats["auto_reply"] = result.get("auto_reply")
        stats["ideas"] += int(result.get("ideas_added") or 0)
        for item in result.get("replies") or []:
            if stats["replied"] >= max_replies:
                break
            try:
                reply_id = ig.reply_to_comment(item["comment_id"], item["reply"])
                sf.upsert("Comment_Reply__c", "Comment_Id__c", item["comment_id"],
                          {"Status__c": "Posted", "Instagram_Reply_Id__c": reply_id, "Posted_At__c": now_iso()})
                stats["replied"] += 1
            except (InstagramAPIError, ValueError) as exc:
                sf.upsert("Comment_Reply__c", "Comment_Id__c", item["comment_id"],
                          {"Status__c": "Failed", "Error_Message__c": str(exc)[:255]})
                stats["failed"] += 1
            sleep(pause)  # human-like pacing; avoids looking like a spam bot
    return stats


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--max-replies", type=int, default=30)
    args = parser.parse_args(argv)
    stats = run(args.days, args.max_replies)
    print(f"[comments] {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
