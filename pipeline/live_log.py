"""Live "agent brain" log: the pipeline's key moments, streamed to Salesforce while a Reel is made.

Every important log line (plan, script, quality-check rounds, cover, music, gate, publish, growth actions, errors) becomes
a JSON event on Video_Job__c.Live_Log__c - one event per line - which the admin site's Live view animates.
Installed as a tee on stdout/stderr, so no module has to change. Never breaks or slows the render: flushes are
throttled and every Salesforce error is swallowed.

  {"t": "2026-11-28T13:05:02Z", "kind": "qa", "title": "Quality check round 2: 8/10", "detail": "...", "score": 8.0}
"""

from __future__ import annotations

import io
import json
import re
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Callable, List, Optional, Tuple

MAX_CHARS = 120_000
FLUSH_EVERY = 6.0

# (pattern, kind, title builder) - first match wins. Groups feed the title / scores.
RULES: List[Tuple[re.Pattern, str, Callable[[re.Match], dict]]] = []


def rule(pattern: str, kind: str):
    def wrap(fn):
        RULES.append((re.compile(pattern), kind, fn))
        return fn
    return wrap


@rule(r"\[reel\] Showrunner directive: show=(.*?) topic=(.*?) format=(.*?) notes=(.*)", "plan")
def _directive(m):
    return {"title": f"Following the Planner: {m[1]} · {m[3]}", "detail": f"Topic: {m[2]}\nBrief: {m[4]}"}


@rule(r"\[reel\] strategist \((.*?)\): (.*)", "plan")
def _strategist(m):
    return {"title": f"Creative Strategist ({m[1]})", "detail": m[2]}


@rule(r"\[reel\] Showrunner edits: (\d+) effect\(s\) placed; not supported yet: (.*)", "plan")
def _edits(m):
    return {"title": f"Editor effects placed: {m[1]}", "detail": "" if m[2].strip() == "-" else f"Not supported yet: {m[2]}"}


@rule(r"\[reel\] Showrunner style overrides applied: (.*)", "plan")
def _style(m):
    return {"title": "Planner's style applied", "detail": m[1]}


@rule(r"\[planner\] (.*)", "plan")
def _planner(m):
    return {"title": "Research", "detail": m[1]}


@rule(r"\[reel\] agents: score (\S+) in (\d+) calls; facts pass=(\w+)", "script")
def _agents(m):
    return {"title": f"Script written and fact-checked (director score {m[1]})", "detail": f"{m[2]} agent calls · facts pass: {m[3]}",
            "score": _num(m[1])}


@rule(r"\[reel\] script source: (.*)", "script")
def _source(m):
    return {"title": f"Script ready ({m[1]})"}


@rule(r"\[render\] visual QA round (\d+): (\d+) wrong shot\(s\), edit score (\S+)/10 - (.*)", "qa")
def _qa(m):
    return {"title": f"Quality check round {m[1]}: {m[3]}/10", "detail": f"{m[4]}\nWrong shots: {m[2]}", "score": _num(m[3])}


@rule(r"\[render\]\s+follows the Showrunner's direction: (\S+)/10 (.*)", "qa")
def _direction(m):
    return {"title": f"Follows the Planner's direction: {m[1]}/10", "detail": m[2], "score2": _num(m[1])}


@rule(r"\[render\]\s+(shot \d+: .*)", "qa")
def _shot(m):
    return {"title": "Shot replaced", "detail": m[1]}


@rule(r"\[cover\] cover: .*? - \"(.*?)\" \((.*)\)", "cover")
def _cover(m):
    return {"title": f"Cover picked: \"{m[1]}\"", "detail": m[2]}


@rule(r"\[reel\] Instagram trending audio planned: (.*)", "music")
def _trending(m):
    return {"title": "Trending Instagram audio picked", "detail": m[1]}


@rule(r"\[reel\] music: (.*)", "music")
def _music(m):
    return {"title": "Music chosen", "detail": m[1]}


@rule(r"\[reel\] (held for review.*|quality gate: .*)", "gate")
def _gate(m):
    return {"title": "Quality gate: held for the Editor agent", "detail": m[1]}


@rule(r"\[reel\] (dry run: passed the gate.*)", "gate")
def _dry(m):
    return {"title": "Passed the quality gate (dry run)", "detail": m[1]}


@rule(r"\[reel\] published: (.*)", "publish")
def _published(m):
    return {"title": "Live on Instagram", "detail": m[1]}


@rule(r"\[reel\] (Posting limit: .*)", "publish")
def _limit(m):
    return {"title": "Waiting for the posting window", "detail": m[1]}


@rule(r"\[reel\] first comment: (.*)", "growth")
def _comment(m):
    return {"title": "First comment posted", "detail": m[1]}


@rule(r"\[growth\] (story teaser posted.*)", "growth")
def _story(m):
    return {"title": "Story teaser posted", "detail": m[1]}


@rule(r"\[youtube\] (uploaded YouTube Short.*)", "growth")
def _yt(m):
    return {"title": "Uploaded to YouTube Shorts", "detail": m[1]}


@rule(r"\[facebook\] (cross-posted.*)", "growth")
def _fb(m):
    return {"title": "Cross-posted to Facebook Reels", "detail": m[1]}


@rule(r"\[reel\] (FAILED .*)", "error")
def _failed(m):
    return {"title": "Failed", "detail": m[1]}


def _num(v: str) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse(line: str) -> Optional[dict]:
    line = line.strip()
    for pattern, kind, fn in RULES:
        m = pattern.search(line)
        if m:
            event = {"kind": kind, **{k: v for k, v in fn(m).items() if v not in (None, "")}}
            if "detail" in event:
                event["detail"] = str(event["detail"])[:1200]
            return event
    return None


class LiveLog:
    def __init__(self, sf, job_id: str, clock: Callable[[], float] = time.time):
        self.sf, self.job, self.clock = sf, job_id, clock
        self.events: List[dict] = []
        self.last_flush = float("-inf")  # the first event shows up immediately
        self.lock = threading.Lock()

    def add(self, kind: str, title: str, detail: str = "", **extra) -> None:
        event = {"t": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "kind": kind, "title": title[:200]}
        if detail:
            event["detail"] = detail[:1200]
        event.update({k: v for k, v in extra.items() if v is not None})
        with self.lock:
            self.events.append(event)
        self.flush(force=kind in ("gate", "publish", "error"))

    def line(self, text: str) -> None:
        event = parse(text)
        if event:
            kind = event.pop("kind")
            self.add(kind, event.pop("title", kind), event.pop("detail", ""), **event)

    def flush(self, force: bool = False) -> None:
        if not force and self.clock() - self.last_flush < FLUSH_EVERY:
            return
        self.last_flush = self.clock()
        with self.lock:
            body = "\n".join(json.dumps(e, ensure_ascii=False) for e in self.events)
        while len(body) > MAX_CHARS and "\n" in body:
            body = body.split("\n", 1)[1]  # keep the newest events
        try:
            self.sf.job_update(self.job, Live_Log__c=body)
        except Exception:
            pass


class _Tee(io.TextIOBase):
    def __init__(self, stream, live: LiveLog):
        self.stream, self.live, self.buf = stream, live, ""

    def write(self, s: str) -> int:
        self.stream.write(s)
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            try:
                self.live.line(line)
            except Exception:
                pass
        return len(s)

    def flush(self) -> None:
        self.stream.flush()

    def isatty(self) -> bool:
        return False

    def fileno(self):
        return self.stream.fileno()

    @property
    def encoding(self):
        return getattr(self.stream, "encoding", "utf-8")

    def __getattr__(self, name):  # buffer, errors, ... - anything else goes to the real stream
        return getattr(self.stream, name)


def install(sf, job_id: str) -> Optional[LiveLog]:
    """Tee stdout + stderr into a LiveLog for this job (no-op without a real Salesforce)."""
    if sf is None or type(sf).__name__ == "NullSalesforce" or not job_id:
        return None
    import atexit

    live = LiveLog(sf, job_id)
    sys.stdout = _Tee(sys.stdout, live)
    sys.stderr = _Tee(sys.stderr, live)
    atexit.register(live.flush, True)  # the last events (first comment, Story, YouTube) always arrive
    return live
