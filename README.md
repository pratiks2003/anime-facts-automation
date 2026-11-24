# InstaAgent: Anime Facts Reels Automation

Daily, automated Instagram Reels about anime, built on Salesforce (Agentforce, Apex, Flows) and a Python pipeline that runs on GitHub Actions. The target is zero paid APIs.

The project has two parts:

| Part | Where | What it does |
|---|---|---|
| Python pipeline | `knowledge/`, `rag/`, `trends/`, `tests/` | Finds trending topics, retrieves source-backed facts, and later renders videos and publishes them |
| Salesforce | `force-app/` (Salesforce DX) | Stores content, approvals, job status and analytics, and runs the agent and orchestration (Phase 3 onwards) |

## Status

Phase 1 is done: the knowledge schema, BM25 retrieval, trending-topic discovery and tests. Everything after that is still to be built.

## Python setup

You need Python 3.9 or later locally. CI uses 3.12.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Commands

```bash
# Run all tests
python -m unittest discover -s tests -t . -v

# Validate the knowledge files (CI runs this too)
python -m rag.validate

# Search. By default this returns only verified facts with spoiler level medium or lower
python -m rag.run_search "Itachi and Sasuke relationship" --anime Naruto
python -m rag.run_search "Luffy powers" --anime "one piece" --max-spoiler high --context

# Drafting only: include unverified facts. Never use this for publishing
python -m rag.run_search "Luffy powers" --include-unverified

# Today's trending anime from AniList (no key needed) and which ones lack knowledge
python -m trends.run_trends --limit 20
```

Search options: `--mode fact|theory|what_if|ranking`, `--character NAME` (can be repeated), `--category`, `--max-spoiler none|low|medium|high`, `--top-k`.

## Knowledge records

Each file in `knowledge/*.jsonl` holds one fact per line:

```json
{"id": "NAR-001", "anime": "Naruto", "title": "Naruto's dream",
 "content": "Naruto Uzumaki wants to become Hokage ...",
 "characters": ["Naruto Uzumaki"], "category": "character_goal",
 "canon_status": "canon", "spoiler_level": "none",
 "source_url": "https://en.wikipedia.org/wiki/Naruto_Uzumaki",
 "source_type": "encyclopedia", "license": "CC BY-SA 4.0",
 "verified": false, "last_reviewed": null, "evidence_quote": null,
 "tags": ["naruto", "hokage"]}
```

`rag/schema.py` enforces these rules:
- `canon_status` is one of `canon`, `anime_original` or `theory`. Theory records must use `category: "theory"`.
- `spoiler_level` is one of `none`, `low`, `medium` or `high`.
- `source_url` must be a real `https://` URL. Never invent one.
- `verified: true` requires `last_reviewed` in `YYYY-MM-DD` format.
- IDs must be unique across all files, and every anime must be listed in `anime_metadata.json`.

**To verify a fact:**
1. Open `source_url` and confirm the content matches the source.
2. Paste the supporting sentence into `evidence_quote`.
3. Set `verified: true` and `last_reviewed` to today's date.

Unverified records never reach a published script.

### How each content mode uses records

| Mode | Records it can use |
|---|---|
| `fact`, `ranking`, `what_if` | `canon` and `anime_original` only |
| `theory` | Also `theory` records, which are labelled `FAN THEORY - NOT CONFIRMED` in the context passed to the LLM |

## Salesforce

This is a standard Salesforce DX project. Metadata lives in `force-app/main/default/` and the default org alias is `InstaAgent`. Custom objects and Apex arrive in Phase 3.

```bash
sf project deploy start --target-org InstaAgent
```
