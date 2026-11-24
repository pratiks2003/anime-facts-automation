"""Command-line search. Prints JSON (used later by GitHub Actions).

Examples:
  python -m rag.run_search "Itachi and Sasuke" --anime Naruto --max-spoiler high
  python -m rag.run_search "rubber powers" --anime "one piece" --include-unverified --context
"""

from __future__ import annotations

import argparse
import json
import sys

from .knowledge import DEFAULT_KNOWLEDGE_DIR, KnowledgeValidationError
from .retriever import CONTENT_MODES, AnimeRAG
from .schema import CATEGORIES, SPOILER_LEVELS


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Search the anime knowledge base.")
    parser.add_argument("query")
    parser.add_argument("--anime")
    parser.add_argument("--character", action="append", default=[], help="repeatable")
    parser.add_argument("--category", choices=CATEGORIES)
    parser.add_argument("--mode", choices=list(CONTENT_MODES), default="fact")
    parser.add_argument("--max-spoiler", choices=SPOILER_LEVELS, default="medium")
    parser.add_argument("--include-unverified", action="store_true",
                        help="for drafting/review only; never for publishing")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--context", action="store_true", help="print LLM context block instead of JSON")
    parser.add_argument("--knowledge-dir", default=str(DEFAULT_KNOWLEDGE_DIR))
    args = parser.parse_args(argv)

    try:
        rag = AnimeRAG(args.knowledge_dir)
        response = rag.retrieve(
            args.query,
            anime=args.anime,
            characters=args.character,
            category=args.category,
            mode=args.mode,
            max_spoiler=args.max_spoiler,
            verified_only=not args.include_unverified,
            top_k=args.top_k,
        )
    except (KnowledgeValidationError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.context:
        print(response.build_context())
    else:
        print(json.dumps(response.to_dict(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
