"""Keep the Agentforce Data Library "Senpai_Knowledge" (Data Cloud) in sync with knowledge/*.jsonl.

One .txt document per show (the library only accepts .txt / PDF / HTML). A show's document is replaced only
when its facts changed, so this is cheap to run after every render and on a daily safety-net schedule.

  python -m tools.sync_knowledge            # sync (needs SF_DOMAIN / SF_CLIENT_ID / SF_CLIENT_SECRET)
  python -m tools.sync_knowledge --dry-run  # only show what would change
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Callable, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from integrations.salesforce import SalesforceClient  # noqa: E402
from rag.knowledge import load_knowledge  # noqa: E402

LIBRARY_ID = os.environ.get("SF_KNOWLEDGE_LIBRARY_ID", "1JDgK000009eCJpWAM")
API = "v67.0"  # data-library file endpoints need v67+


def log(msg: str) -> None:
    print(f"[knowledge-sync] {msg}", file=sys.stderr, flush=True)


def file_name(anime: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in anime).strip("_")[:60] + ".txt"


def build_documents(records: List[dict]) -> Dict[str, bytes]:
    """file name -> document text (same layout the library was created with)."""
    by = defaultdict(list)
    for r in records:
        by[r["anime"]].append(r)
    docs = {}
    for anime, recs in sorted(by.items()):
        lines = [f"# {anime} - verified facts (Senpai Secrets knowledge base)", "",
                 "Every fact below was verified against a quoted source. Canon status tells you whether it is canon, "
                 "a fan theory or non-canon.", ""]
        for r in sorted(recs, key=lambda r: r["id"]):
            lines += [f"## {r['id']}: {r.get('title', '')}", r["content"], "",
                      f"- characters: {', '.join(r.get('characters') or []) or '-'}",
                      f"- category: {r.get('category')} | canon status: {r.get('canon_status')} | spoiler level: {r.get('spoiler_level')}",
                      f"- source: {r.get('source_url')}", ""]
        docs[file_name(anime)] = "\n".join(lines).encode("utf-8")
    return docs


class Library:
    def __init__(self, sf: SalesforceClient, library_id: str = LIBRARY_ID, opener: Callable = urllib.request.urlopen):
        self.sf, self.base, self.opener = sf, f"einstein/data-libraries/{library_id}", opener

    def files(self) -> Dict[str, dict]:
        data = self.sf.request("GET", f"{self.base}/files?pageSize=200", version=API) or {}
        return {f["fileName"]: f for f in data.get("files") or []}

    def delete(self, file_id: str) -> None:
        self.sf.request("DELETE", f"{self.base}/files/{file_id}", version=API)

    def add(self, docs: Dict[str, bytes]) -> None:
        names = list(docs)
        resp = self.sf.request("POST", f"{self.base}/file-upload-urls", {"files": [{"fileName": n} for n in names]},
                               version=API) or {}
        entries = resp.get("uploadUrls") or []
        if len(entries) != len(names):
            raise RuntimeError(f"expected {len(names)} upload URLs, got {len(entries)}")
        uploaded = []
        for name, entry in zip(names, entries):
            req = urllib.request.Request(entry["uploadUrl"], data=docs[name], method="PUT")
            for k, v in (entry.get("headers") or {}).items():
                req.add_header(k, v)
            with self.opener(req, timeout=120) as r:
                if r.status >= 300:
                    raise RuntimeError(f"upload of {name} failed: HTTP {r.status}")
            uploaded.append({"filePath": entry["filePath"], "fileSize": len(docs[name])})
        self.sf.request("POST", f"{self.base}/files", {"uploadedFiles": uploaded}, version=API)


def plan(docs: Dict[str, bytes], existing: Dict[str, dict]) -> Dict[str, List[str]]:
    """Changed = new show, or size differs (any added / edited fact changes the size in practice)."""
    new = [n for n in docs if n not in existing]
    changed = [n for n in docs if n in existing and int(existing[n].get("fileSize") or -1) != len(docs[n])]
    return {"new": new, "changed": changed}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    docs = build_documents(load_knowledge())
    sf = SalesforceClient.from_env()
    if sf is None:
        log("Salesforce credentials missing; nothing synced")
        return 0
    lib = Library(sf)
    try:
        existing = lib.files()
    except Exception as exc:
        log(f"cannot read the library ({exc}); the GitHub Salesforce user may need Data Library access")
        return 0
    todo = plan(docs, existing)
    log(f"{len(docs)} shows locally, {len(existing)} in the library; new: {todo['new'] or '-'}; changed: {todo['changed'] or '-'}")
    if args.dry_run or not (todo["new"] or todo["changed"]):
        return 0
    try:
        lib.add({n: docs[n] for n in todo["new"]}) if todo["new"] else None
        for n in todo["changed"]:  # replace one show at a time so a failure never leaves several shows missing
            lib.delete(existing[n]["fileId"])
            lib.add({n: docs[n]})
    except Exception as exc:  # never fail the render for this; tell Ops / the owner instead
        log(f"sync failed: {exc}")
        try:
            sf.request("POST", "sobjects/Agent_Activity__c", {
                "Agent__c": "Ops", "Action__c": "Knowledge sync", "Needs_Owner__c": False, "Severity__c": "warning",
                "Details__c": f"Could not refresh the Senpai_Knowledge library in Data Cloud: {exc}"[:5000]})
        except Exception:
            pass
        return 0
    log("uploaded; Data Cloud re-indexes in the background (a few minutes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
