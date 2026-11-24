"""Loading knowledge records and anime metadata from disk."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Union

from .schema import validate_record

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_KNOWLEDGE_DIR = PROJECT_ROOT / "knowledge"
METADATA_FILENAME = "anime_metadata.json"

PathLike = Union[str, Path]


class KnowledgeValidationError(ValueError):
    """Raised when one or more knowledge records are invalid."""

    def __init__(self, problems: List[str]):
        self.problems = problems
        preview = "\n  ".join(problems[:20])
        more = f"\n  ... and {len(problems) - 20} more" if len(problems) > 20 else ""
        super().__init__(f"{len(problems)} knowledge problem(s):\n  {preview}{more}")


def load_knowledge(knowledge_dir: PathLike = DEFAULT_KNOWLEDGE_DIR) -> List[dict]:
    """Load and validate every *.jsonl file in knowledge_dir.

    Raises KnowledgeValidationError listing *all* problems (bad JSON, schema
    violations, duplicate IDs) so they can be fixed in one pass.
    """
    directory = Path(knowledge_dir)
    if not directory.is_dir():
        raise FileNotFoundError(f"knowledge directory not found: {directory}")

    records: List[dict] = []
    problems: List[str] = []
    seen_ids: Dict[str, str] = {}

    for path in sorted(directory.glob("*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                where = f"{path.name}:{line_no}"
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    problems.append(f"{where}: invalid JSON ({exc.msg})")
                    continue

                errors = validate_record(record)
                if errors:
                    problems.extend(f"{where}: {err}" for err in errors)
                    continue

                if record["id"] in seen_ids:
                    problems.append(
                        f"{where}: duplicate id '{record['id']}' (first seen at {seen_ids[record['id']]})"
                    )
                    continue

                seen_ids[record["id"]] = where
                records.append(record)

    if problems:
        raise KnowledgeValidationError(problems)
    return records


def load_metadata(knowledge_dir: PathLike = DEFAULT_KNOWLEDGE_DIR) -> List[dict]:
    """Load anime_metadata.json (list of anime with aliases and AniList IDs)."""
    path = Path(knowledge_dir) / METADATA_FILENAME
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("anime", []))


def normalize_name(name: str) -> str:
    return " ".join(name.casefold().replace("-", " ").replace(":", " ").split())


def build_alias_index(metadata: List[dict]) -> Dict[str, str]:
    """Map every normalized name/alias to the canonical anime name."""
    index: Dict[str, str] = {}
    for entry in metadata:
        canonical = entry["name"]
        for alias in [canonical, *entry.get("aliases", [])]:
            index[normalize_name(alias)] = canonical
    return index


def resolve_anime(name: Optional[str], alias_index: Dict[str, str]) -> Optional[str]:
    """Return the canonical anime name for a user-supplied name or alias."""
    if name is None:
        return None
    key = normalize_name(name)
    return alias_index.get(key, name.strip())
