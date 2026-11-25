"""Visual QA agent: watches the Reel before it is published, like a human editor would.

For every shot it looks at a frame together with the sentence being narrated at that moment
(Gemini vision, free tier) and judges: who/what is on screen, does it match the sentence, is it
the right show, any foreign watermark/caption, low quality or unsafe content. Shots that fail are
banned and re-picked by the shot resolver; the Reel is checked again (bounded rounds). Whatever it
still cannot fix is reported to the QA gate (pipeline.run_reel.qa_review).

Needs GEMINI_API_KEY (Google AI Studio, free). Without it the agent is skipped.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from io import BytesIO
from typing import List, Optional

MODELS = ["gemini-3.8-flash", "gemini-flash-latest", "gemini-3.5-flash-lite", "gemini-flash-lite-latest"]
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
SECRET_RE = re.compile(r"(EAA[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")

PROMPT = """You are the visual QA editor of "Senpai Secrets", an anime Instagram Reel channel.
The Reel is about: {anime}. Below are its shots in order. For each shot you get the sentence the narrator is
saying while it is on screen, then the frame.

Judge every shot like a strict human editor:
- on_screen: who/what is shown. Name anime characters when you recognise them.
- matches: does the picture fit the sentence? If the sentence names or is clearly about a character, place or object
  (e.g. "the Elbaf mural"), that exact thing must be visible. Set matches=false (severity "minor") for shots that are
  merely generic when the sentence describes something specific, and say in "better" exactly what to show
  (a fan-wiki-style search phrase, e.g. "Elbaf mural horned figure").
- Also fail a shot for: a different anime than {anime}; another creator's watermark or burned-in captions/subtitles;
  text-only / logo / title cards; rough sketches, storyboards or unfinished line art; merchandise, video-game or ad
  content; real-world photos; very low quality; nudity or gore.

Then judge the WHOLE Reel as the channel's final quality checker: would a top anime edit page post this?
edit_score 1-10 (relevance of every shot to the words, variety, polish, no repeated/boring shots, looks like a real
anime edit). up_to_mark = edit_score >= 7.
Big on-screen words in the middle are OUR captions - ignore them.

Return ONLY JSON: {{"shots": [{{"shot": n, "on_screen": str, "matches": bool, "severity": "major"|"minor"|null,
"problem": str|null, "better": "what should be shown instead"|null}}], "summary": "one sentence",
"edit_score": 1-10, "up_to_mark": bool, "edit_notes": ["what would make this a better edit"]}}
major = wrong character / wrong show / unsafe / watermark; minor = weak but acceptable."""


def log(msg: str) -> None:
    print(f"[visual-qa] {msg}", file=sys.stderr, flush=True)


class Gemini:
    def __init__(self, key: str, opener=urllib.request.urlopen):
        self.key, self.opener = key, opener
        self.calls = 0
        self.exhausted: set = set()  # models that answered 429 (free quota used up): skip for this run

    @classmethod
    def from_env(cls) -> Optional["Gemini"]:
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        return cls(key) if key else None

    def json(self, parts: list, temperature: float = 0.2, rounds: int = 4) -> Optional[dict]:
        """Ask with model fallback + backoff (free tier is often busy). None if every model is unavailable."""
        safe = [{"text": SECRET_RE.sub("[redacted]", p["text"])} if "text" in p else p for p in parts]
        body = json.dumps({"contents": [{"parts": safe}],
                           "generationConfig": {"response_mime_type": "application/json", "temperature": temperature}}).encode()
        for attempt in range(rounds):
            for model in [m for m in MODELS if m not in self.exhausted]:
                req = urllib.request.Request(API.format(model=model), data=body,
                                             headers={"Content-Type": "application/json", "x-goog-api-key": self.key})
                try:
                    self.calls += 1
                    with self.opener(req, timeout=120) as resp:
                        out = json.loads(resp.read().decode())
                    text = out["candidates"][0]["content"]["parts"][-1]["text"]
                    return json.loads(text[text.find("{"): text.rfind("}") + 1])
                except urllib.error.HTTPError as exc:
                    if exc.code in (429, 404):
                        self.exhausted.add(model)  # quota / retired: don't hammer it again this run
                        continue
                    if exc.code not in (500, 503):
                        log(f"{model}: HTTP {exc.code}")
                        return None
                except (KeyError, ValueError, urllib.error.URLError, TimeoutError):
                    continue
            if len(self.exhausted) == len(MODELS):
                break
            time.sleep(15 * (attempt + 1))
        log("all Gemini models busy or unavailable; skipping visual QA")
        return None


def jpeg_part(img, width: int = 384) -> dict:
    im = img.convert("RGB")
    im = im.resize((width, int(im.height * width / im.width)))
    buf = BytesIO()
    im.save(buf, "JPEG", quality=80)
    return {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}}


def sentence_at(narration, start: float, end: float) -> str:
    """Words spoken while the shot is on screen (plus a little context)."""
    words = [w.text for sc in narration.scenes for w in sc.words if w.end >= start - 0.6 and w.start <= end + 0.3]
    return " ".join(words)[:300]


def review(renderer, narration, script: dict, gemini: Gemini, shot_sources: List[Optional[str]]) -> Optional[dict]:
    """{"bad": [shot indices], "issues": [str], "summary": str} or None when the agent is unavailable."""
    shots = renderer.shots
    parts = [{"text": PROMPT.format(anime=script["anime"])}]
    for i, shot in enumerate(shots):
        mid = (shot.start + shot.end) / 2
        parts.append({"text": f"Shot {i + 1} ({'video clip' if shot.clip else 'still'}) - narration: "
                              f"\"{sentence_at(narration, shot.start, shot.end)}\""})
        parts.append(jpeg_part(renderer.frame(mid, captions=False)))
    result = gemini.json(parts)
    if not result:
        return None
    bad, issues, weak = [], [], []
    for row in result.get("shots") or []:
        try:
            idx = int(row.get("shot")) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(shots) and row.get("matches") is False and row.get("better"):
            weak.append((idx, str(row["better"])[:120]))  # what the editor wants instead (any severity)
        if 0 <= idx < len(shots) and row.get("matches") is False and row.get("severity") == "major":
            bad.append(idx)
            issues.append(f"shot {idx + 1}: {row.get('problem') or 'mismatch'} (on screen: {row.get('on_screen')}; "
                          f"should show: {row.get('better') or '-'})")
    try:
        score = float(result.get("edit_score"))
    except (TypeError, ValueError):
        score = None
    return {"bad": bad, "issues": issues, "summary": str(result.get("summary") or "")[:300],
            "edit_score": score, "up_to_mark": bool(result.get("up_to_mark", score is None or score >= 7)),
            "edit_notes": [str(n)[:200] for n in (result.get("edit_notes") or [])][:5],
            "weak": weak,
            "bad_sources": [shot_sources[i] for i in bad if i < len(shot_sources) and shot_sources[i]]}


CLIP_PROMPT = """You are the clip editor of an anime Instagram Reel about {anime}. Below are candidate video clips (one frame
each) and the Reel's sentences. Approve a clip only if it is FINISHED {anime} anime footage (final colour animation or a
clean official illustration) - reject rough sketches, storyboards, animation-process footage, logos/title cards, ads,
game collabs, merchandise, live action and anything from another show.
For approved clips pick the sentence it fits best (the character/moment described), or null if it is only a generic
hype shot of the show.

Sentences:
{sentences}

Return ONLY JSON: {{"clips": [{{"clip": n, "approved": bool, "scene": sentence_number|null, "why": str}}]}}"""


def approve_clips(gemini: Gemini, clip_dirs: List[str], scene_texts: List[str], anime: str) -> Optional[dict]:
    """{clip_dir: scene_index|None} for approved clips (rejected ones are left out); None when unavailable."""
    from PIL import Image

    if not clip_dirs:
        return {}
    sentences = "\n".join(f"{i + 1}. {t[:200]}" for i, t in enumerate(scene_texts))
    parts = [{"text": CLIP_PROMPT.format(anime=anime, sentences=sentences)}]
    for i, d in enumerate(clip_dirs):
        frames = sorted(__import__("pathlib").Path(d).glob("*.jpg"))
        if not frames:
            continue
        parts.append({"text": f"Clip {i + 1}"})
        parts.append(jpeg_part(Image.open(frames[len(frames) // 2]), width=288))
    result = gemini.json(parts)
    if not result:
        return None
    approved = {}
    for row in result.get("clips") or []:
        try:
            idx = int(row.get("clip")) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(clip_dirs) and row.get("approved"):
            scene = row.get("scene")
            try:
                scene = int(scene) - 1 if scene is not None else None
            except (TypeError, ValueError):
                scene = None
            approved[clip_dirs[idx]] = scene if scene is None or 0 <= scene < len(scene_texts) else None
    log(f"clip editor approved {len(approved)}/{len(clip_dirs)} clips")
    return approved
