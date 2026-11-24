"""Validate all knowledge files and print a summary. Exit code 1 on any problem.

  python -m rag.validate
"""

from __future__ import annotations

import sys
from collections import Counter

from .knowledge import DEFAULT_KNOWLEDGE_DIR, KnowledgeValidationError, load_knowledge, load_metadata


def main(argv=None) -> int:
    knowledge_dir = (argv or sys.argv[1:] or [str(DEFAULT_KNOWLEDGE_DIR)])[0]
    try:
        records = load_knowledge(knowledge_dir)
    except (KnowledgeValidationError, FileNotFoundError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1

    known = {entry["name"] for entry in load_metadata(knowledge_dir)}
    by_anime = Counter(r["anime"] for r in records)
    verified = Counter(r["anime"] for r in records if r["verified"])
    unknown = sorted(set(by_anime) - known)

    print(f"OK: {len(records)} valid record(s)")
    for anime in sorted(by_anime):
        print(f"  {anime}: {by_anime[anime]} record(s), {verified[anime]} verified")
    if unknown:
        print(f"WARNING: anime missing from anime_metadata.json: {unknown}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
