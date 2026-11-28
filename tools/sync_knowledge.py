"""Keep the agents' knowledge in sync with knowledge/*.jsonl.

1. Anime_Knowledge__c: every fact, bulk upserted. This is what the agents' find_facts action searches, and it is
   always current: GitHub's API-only Salesforce user can write it after every render.
2. The Agentforce Data Library "Senpai_Knowledge" (Data Cloud, search by meaning): one .txt per show, replaced only
   when that show's facts changed. Managing a library needs an Agentforce-licensed user, which the integration
   user is not, so from GitHub this part is skipped quietly. Refresh it from an admin session with --cli.

  python -m tools.sync_knowledge            # GitHub: mirror facts (+ library if the user may manage it)
  python -m tools.sync_knowledge --cli      # laptop: library refresh through the logged-in sf CLI (admin)
  python -m tools.sync_knowledge --dry-run  # only show what would change
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
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


def fact_rows(records: List[dict], anime_ids: Dict[str, str]) -> List[dict]:
    rows = []
    for f in records:
        if str(f["id"]).startswith("NEWS-"):
            continue
        row = {
            "attributes": {"type": "Anime_Knowledge__c"}, "Knowledge_Id__c": f["id"], "Name": f["title"][:80],
            "Content__c": f["content"][:2000], "Characters__c": ", ".join(f.get("characters") or [])[:255],
            "Category__c": f.get("category"), "Canon_Status__c": f.get("canon_status"),
            "Spoiler_Level__c": f.get("spoiler_level"), "Source_URL__c": f.get("source_url"),
            "Verified__c": bool(f.get("verified")),
            "Verification_Method__c": "Automatic" if "auto-verified" in (f.get("notes") or "") else "Manual",
            "Last_Verified_Date__c": f.get("last_reviewed"),
        }
        if str(f.get("anime", "")).lower() in anime_ids:
            row["Anime__c"] = anime_ids[f["anime"].lower()]
        rows.append(row)
    return rows


def mirror_facts(sf, records: List[dict]) -> int:
    """Every fact -> Anime_Knowledge__c, 200 per call (sObject Collections upsert)."""
    anime_ids = {r["Name"].lower(): r["Id"] for r in sf.query("SELECT Id, Name FROM Anime__c")}
    rows = fact_rows(records, anime_ids)
    done = 0
    for i in range(0, len(rows), 200):
        chunk = rows[i:i + 200]
        result = sf.request("PATCH", "composite/sobjects/Anime_Knowledge__c/Knowledge_Id__c",
                            {"allOrNone": False, "records": chunk}) or []
        for r, row in zip(result, chunk):
            if r.get("success"):
                done += 1
            else:
                log(f"fact {row['Knowledge_Id__c']} not mirrored: {r.get('errors')}")
    return done


class Library:
    def __init__(self, sf, library_id: str = LIBRARY_ID, opener: Callable = urllib.request.urlopen):
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


class CliSalesforce:
    """Salesforce REST through the logged-in sf CLI (admin session on a laptop; the token is never read)."""

    def __init__(self, org: str = "InstaAgent"):
        self.org = org

    def request(self, method, path, payload=None, attempts=1, base="data", version="v64.0"):
        cmd = ["sf", "api", "request", "rest", f"/services/data/{version}/{path}", "--method", method, "-o", self.org]
        body = None
        if payload is not None:
            body = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
            json.dump(payload, body)
            body.close()
            cmd += ["--body", "@" + body.name]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, check=False).stdout.strip()
        finally:
            if body:
                os.unlink(body.name)
        data = json.loads(out) if out else None
        if isinstance(data, list) and data and isinstance(data[0], dict) and "errorCode" in data[0]:
            raise RuntimeError(f"{method} {path}: {data[0]['errorCode']} {data[0].get('message')}")
        return data

    def query(self, soql: str) -> List[dict]:
        import urllib.parse

        return (self.request("GET", f"query?q={urllib.parse.quote(soql)}") or {}).get("records", [])


def plan(docs: Dict[str, bytes], existing: Dict[str, dict]) -> Dict[str, List[str]]:
    """Changed = new show, or size differs (any added / edited fact changes the size in practice)."""
    new = [n for n in docs if n not in existing]
    changed = [n for n in docs if n in existing and int(existing[n].get("fileSize") or -1) != len(docs[n])]
    return {"new": new, "changed": changed}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cli", action="store_true", help="use the logged-in sf CLI (admin) instead of env credentials")
    args = ap.parse_args(argv)
    records = load_knowledge()
    docs = build_documents(records)
    sf = CliSalesforce() if args.cli else SalesforceClient.from_env()
    if sf is None:
        log("Salesforce credentials missing; nothing synced")
        return 0
    if not args.dry_run:
        try:
            log(f"{mirror_facts(sf, records)} of {len(records)} facts mirrored to Anime_Knowledge__c (find_facts)")
        except Exception as exc:
            log(f"fact mirror failed: {exc}")
    lib = Library(sf)
    try:
        existing = lib.files()
    except Exception as exc:
        log(f"Data Cloud library not reachable for this user ({str(exc)[:120]}); skipped - find_facts is current")
        return 0
    todo = plan(docs, existing)
    log(f"{len(docs)} shows locally, {len(existing)} in the library; new: {todo['new'] or '-'}; changed: {todo['changed'] or '-'}")
    if args.dry_run or not (todo["new"] or todo["changed"]):
        return 0
    try:
        if todo["new"]:
            lib.add({n: docs[n] for n in todo["new"]})
        for n in todo["changed"]:  # one show at a time so a failure never leaves several shows missing
            lib.delete(existing[n]["fileId"])
            lib.add({n: docs[n]})
    except Exception as exc:
        log(f"library refresh failed: {exc}")
        return 0
    log("library updated; Data Cloud re-indexes in the background (a few minutes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
