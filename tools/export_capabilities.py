"""Write the editor capability manifest (video/editing.py) into the Salesforce static resource the agents read.

  python -m tools.export_capabilities   # then deploy force-app/main/default/staticresources/EditorCapabilities.*
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TARGET = ROOT / "force-app" / "main" / "default" / "staticresources" / "EditorCapabilities.json"


def render() -> str:
    from video.editing import manifest

    return json.dumps(manifest(), indent=1, sort_keys=True)


if __name__ == "__main__":
    TARGET.write_text(render())
    print(f"wrote {TARGET.relative_to(ROOT)}")
