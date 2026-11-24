"""Knowledge record schema and validation.

Every line in knowledge/*.jsonl is one JSON object ("record") describing a
single, source-backed anime fact. Records are validated strictly so that bad
data fails fast in CI instead of reaching a published video.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List

CANON_STATUSES = (
    "canon",           # confirmed by the original work / official sources
    "anime_original",  # anime-only material (filler, anime-original arcs)
    "theory",          # fan theory: must never be presented as confirmed
)

SPOILER_LEVELS = ("none", "low", "medium", "high")

SOURCE_TYPES = ("official", "encyclopedia", "wiki", "database", "news", "fan")

CATEGORIES = (
    "character_goal",
    "character_profile",
    "ability",
    "backstory",
    "relationship",
    "plot_event",
    "world_building",
    "production",
    "trivia",
    "theory",
)

ID_PATTERN = re.compile(r"^[A-Z]{2,5}-\d{3,}$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# field name -> (expected python type(s), required?)
FIELDS: Dict[str, tuple] = {
    "id": (str, True),
    "anime": (str, True),
    "title": (str, True),
    "content": (str, True),
    "characters": (list, True),
    "category": (str, True),
    "canon_status": (str, True),
    "spoiler_level": (str, True),
    "source_url": (str, True),
    "source_type": (str, True),
    "verified": (bool, True),
    "tags": (list, True),
    "last_reviewed": ((str, type(None)), False),  # YYYY-MM-DD, required when verified
    "evidence_quote": ((str, type(None)), False),  # exact supporting text from the source
    "license": ((str, type(None)), False),         # e.g. "CC BY-SA 4.0" for Wikipedia
    "notes": ((str, type(None)), False),
}


def validate_record(record: Any) -> List[str]:
    """Return a list of human-readable problems. Empty list means valid."""
    if not isinstance(record, dict):
        return ["record must be a JSON object"]

    errors: List[str] = []

    for name, (expected, required) in FIELDS.items():
        if name not in record:
            if required:
                errors.append(f"missing required field '{name}'")
            continue
        if not isinstance(record[name], expected):
            errors.append(f"field '{name}' has wrong type {type(record[name]).__name__}")

    unknown = sorted(set(record) - set(FIELDS))
    if unknown:
        errors.append(f"unknown field(s): {', '.join(unknown)}")

    if errors:
        return errors  # type problems make further checks unreliable

    for name in ("id", "anime", "title", "content", "source_url"):
        if not record[name].strip():
            errors.append(f"field '{name}' must not be empty")

    if not ID_PATTERN.match(record["id"]):
        errors.append(f"id '{record['id']}' must look like 'NAR-001'")

    for list_field in ("characters", "tags"):
        if not all(isinstance(v, str) and v.strip() for v in record[list_field]):
            errors.append(f"field '{list_field}' must contain non-empty strings only")

    for name, allowed in (
        ("category", CATEGORIES),
        ("canon_status", CANON_STATUSES),
        ("spoiler_level", SPOILER_LEVELS),
        ("source_type", SOURCE_TYPES),
    ):
        if record[name] not in allowed:
            errors.append(f"field '{name}'='{record[name]}' not in {list(allowed)}")

    if not record["source_url"].startswith("https://"):
        errors.append("source_url must be an https:// URL")

    reviewed = record.get("last_reviewed")
    if reviewed is not None:
        if not DATE_PATTERN.match(reviewed):
            errors.append("last_reviewed must be YYYY-MM-DD")
        else:
            try:
                date.fromisoformat(reviewed)
            except ValueError:
                errors.append("last_reviewed is not a real date")

    if record["verified"] and not reviewed:
        errors.append("verified records must have last_reviewed set")

    if record["canon_status"] == "theory" and record["category"] != "theory":
        errors.append("theory records must use category 'theory'")

    return errors
