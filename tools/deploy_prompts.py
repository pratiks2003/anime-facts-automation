"""Deploy the Prompt Builder agent templates and activate their latest versions.

Prompt templates deployed through metadata are created without an active version, so:
deploy -> retrieve (to learn the generated version ids) -> set activeVersionIdentifier -> deploy again.

  python tools/deploy_prompts.py [--org InstaAgent]
"""

from __future__ import annotations

import argparse
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--org", default="InstaAgent")
    args = parser.parse_args()
    names = sorted(p.name.split(".")[0] for p in SRC.glob("*.genAiPromptTemplate-meta.xml"))
    print(f"deploying {len(names)} templates")
    sf("project", "deploy", "start", "--target-org", args.org, "--source-dir", str(SRC), "--wait", "20")
    shutil.rmtree(TMP, ignore_errors=True)
    sf("project", "retrieve", "start", "--target-org", args.org, "--output-dir", str(TMP.relative_to(ROOT)), "--wait", "10",
       *sum((["--metadata", f"GenAiPromptTemplate:{n}"] for n in names), []))
    for name in names:
        retrieved = next(TMP.rglob(f"{name}.genAiPromptTemplate-meta.xml"))
        xml = retrieved.read_text()
        versions = re.findall(r"<versionIdentifier>([^<]+)</versionIdentifier>", xml)
        if not versions:
            raise SystemExit(f"{name}: no version identifier found")
        latest = versions[-1]
        xml = re.sub(r"\s*<activeVersionIdentifier>[^<]*</activeVersionIdentifier>", "", xml)
        xml = xml.replace("    <description>", f"    <activeVersionIdentifier>{latest}</activeVersionIdentifier>\n    <description>", 1)
        (SRC / f"{name}.genAiPromptTemplate-meta.xml").write_text(xml)
        print(f"  {name}: active version {latest[-12:]} ({len(versions)} version(s))")
    sf("project", "deploy", "start", "--target-org", args.org, "--source-dir", str(SRC), "--wait", "20")
    shutil.rmtree(TMP, ignore_errors=True)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
