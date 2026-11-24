"""Auto-verify unverified knowledge records against their own source pages.

For each source page, the LLM is asked to find an exact supporting quote for each
claim (or say none). Code then checks the quote really exists in the page text.
Only matches become verified; everything else is left untouched (and unusable).

  python -m pipeline.verify_existing
"""

from __future__ import annotations

import json
import sys
import urllib.parse
from collections import defaultdict
from datetime import date
from pathlib import Path

from rag.knowledge import DEFAULT_KNOWLEDGE_DIR

from .ingest import normalize, trim_source, wiki_page
from .llm import LLM

SYSTEM = """You check claims against a source text. For each claim, copy an EXACT contiguous excerpt
(20-250 characters) from the source text that directly supports it, or null if the text does not
clearly support the claim. Never paraphrase quotes. Return JSON: {"results": [{"id": str, "quote": str|null}]}"""


def verify_directory(llm: LLM, directory: Path = DEFAULT_KNOWLEDGE_DIR) -> dict:
    files = sorted(directory.glob("*.jsonl"))
    lines = {f: [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()] for f in files}
    by_source = defaultdict(list)
    for f, records in lines.items():
        for r in records:
            if not r["verified"] and r["source_url"].startswith("https://en.wikipedia.org/wiki/"):
                by_source[r["source_url"]].append(r)
    stats = {"checked": 0, "verified": 0}
    for url, records in by_source.items():
        title = urllib.parse.unquote(url.rsplit("/", 1)[1]).replace("_", " ")
        page = wiki_page(title)
        if not page:
            continue
        source = page[2]
        haystack = normalize(source)
        claims = "\n".join(f"[{r['id']}] {r['content']}" for r in records)
        reply = llm.json(SYSTEM, f"CLAIMS:\n{claims}\n\nSOURCE TEXT:\n{trim_source(source, 24000)}", max_tokens=2500, temperature=0.1)
        quotes = {str(x.get("id")): x.get("quote") for x in reply.get("results") or []}
        for r in records:
            stats["checked"] += 1
            quote = quotes.get(r["id"])
            if quote and len(quote) >= 20 and normalize(quote) in haystack:
                r.update(verified=True, last_reviewed=date.today().isoformat(), evidence_quote=quote[:400],
                         notes="auto-verified: evidence quote matched source text")
                stats["verified"] += 1
    for f, records in lines.items():
        f.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    return stats


def main() -> int:
    stats = verify_directory(LLM.from_env())
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
