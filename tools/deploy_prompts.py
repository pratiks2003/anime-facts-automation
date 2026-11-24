"""Deploy the Prompt Builder agent templates and activate their latest versions.

Published template versions are immutable: redeploying an existing versionIdentifier keeps
the OLD text. So for every template whose local <content> differs from the org's active
version we drop the versionIdentifier (Salesforce then creates a new version), deploy,
retrieve to learn the new ids, set activeVersionIdentifier to the newest, and deploy again.

  python tools/deploy_prompts.py [--org InstaAgent]
"""

from __future__ import annotations

import argparse
import html
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "force-app" / "main" / "default" / "genAiPromptTemplates"
TMP = ROOT / "output" / "prompt_retrieve"


def sf(*args: str) -> str:
    env = dict(os.environ, SF_SKIP_NEW_VERSION_CHECK="true")
    result = subprocess.run(["sf", *args], capture_output=True, text=True, env=env, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit(f"sf {' '.join(args[:3])} failed:\n{result.stdout[-1500:]}\n{result.stderr[-1500:]}")
    return result.stdout


def retrieve(org: str, names) -> dict:
    """{name: xml} for templates that exist in the org."""
    shutil.rmtree(TMP, ignore_errors=True)
    env = dict(os.environ, SF_SKIP_NEW_VERSION_CHECK="true")
    subprocess.run(["sf", "project", "retrieve", "start", "--target-org", org, "--output-dir", str(TMP.relative_to(ROOT)),
                    "--wait", "10", *sum((["--metadata", f"GenAiPromptTemplate:{n}"] for n in names), [])],
                   capture_output=True, text=True, env=env, cwd=ROOT)
    found = {}
    for name in names:
        hit = next(TMP.rglob(f"{name}.genAiPromptTemplate-meta.xml"), None)
        if hit:
            found[name] = hit.read_text()
    return found


def versions(xml: str):
    """[(versionIdentifier, content)] in file order."""
    out = []
    for block in re.findall(r"<templateVersions>(.*?)</templateVersions>", xml, re.S):
        vid = re.search(r"<versionIdentifier>([^<]+)</versionIdentifier>", block)
        content = re.search(r"<content>(.*?)</content>", block, re.S)
        out.append((vid.group(1) if vid else None, html.unescape(content.group(1)) if content else ""))
    return out


def active_content(xml: str) -> str:
    active = re.search(r"<activeVersionIdentifier>([^<]+)</activeVersionIdentifier>", xml)
    vs = versions(xml)
    for vid, content in vs:
        if active and vid == active.group(1):
            return content
    return vs[-1][1] if vs else ""


def local_content(xml: str) -> str:
    vs = versions(xml)
    return vs[-1][1] if vs else ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--org", default="InstaAgent")
    args = parser.parse_args()
    files = {p.name.split(".")[0]: p for p in SRC.glob("*.genAiPromptTemplate-meta.xml")}
    org = retrieve(args.org, list(files))

    changed = []
    for name, path in files.items():
        xml = path.read_text()
        if name in org and local_content(xml).strip() == active_content(org[name]).strip():
            continue  # identical to what's live
        # keep only the newest local version block, without an identifier -> Salesforce creates a new version
        blocks = re.findall(r"\s*<templateVersions>.*?</templateVersions>", xml, re.S)
        newest = re.sub(r"\s*<versionIdentifier>[^<]*</versionIdentifier>", "", blocks[-1])
        xml = re.sub(r"\s*<templateVersions>.*?</templateVersions>", "", xml, flags=re.S)
        xml = re.sub(r"\s*<activeVersionIdentifier>[^<]*</activeVersionIdentifier>", "", xml)
        xml = xml.replace("    <type>", newest.lstrip("\n") + "\n    <type>", 1)
        path.write_text(xml)
        changed.append(name)
    if not changed:
        print("all templates already live")
        return 0
    print("new versions for:", ", ".join(changed))
    sf("project", "deploy", "start", "--target-org", args.org, "--source-dir", str(SRC), "--wait", "20")
    org = retrieve(args.org, changed)
    for name in changed:
        xml = org[name]
        latest = versions(xml)[-1][0]
        xml = re.sub(r"\s*<activeVersionIdentifier>[^<]*</activeVersionIdentifier>", "", xml)
        xml = xml.replace("    <description>", f"    <activeVersionIdentifier>{latest}</activeVersionIdentifier>\n    <description>", 1)
        files[name].write_text(xml)
        print(f"  {name}: activating {latest[-12:]} ({len(versions(xml))} versions)")
    sf("project", "deploy", "start", "--target-org", args.org, "--source-dir", str(SRC), "--wait", "20")
    shutil.rmtree(TMP, ignore_errors=True)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
