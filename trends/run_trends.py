"""Print today's trending anime with knowledge-base coverage as JSON.

  python -m trends.run_trends --limit 20
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from rag.knowledge import DEFAULT_KNOWLEDGE_DIR, load_knowledge, load_metadata

from .anilist import TrendFetchError, fetch_trending
from .coverage import annotate_coverage


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Show trending anime and knowledge coverage.")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--knowledge-dir", default=str(DEFAULT_KNOWLEDGE_DIR))
    args = parser.parse_args(argv)

    try:
        trending = fetch_trending(limit=args.limit)
    except (TrendFetchError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    rows = annotate_coverage(trending, load_metadata(args.knowledge_dir), load_knowledge(args.knowledge_dir))
    report = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "AniList TRENDING_DESC",
        "covered": [r["title"] for r in rows if r["has_knowledge"]],
        "knowledge_gaps": [r["title"] for r in rows if not r["has_knowledge"]],
        "trending": rows,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
