"""Narration with Kokoro (ONNX) and word-level timings for karaoke captions.

Kokoro does not return word timestamps, so each sentence is synthesised separately
and its words are placed inside the measured speech span. Pauses inside a sentence
(commas, colons, dashes) are matched to detected silences when the counts agree,
which keeps captions in sync with natural pauses.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = PROJECT_ROOT / "models"
MODEL_FILE = MODEL_DIR / "kokoro-v1.0.onnx"
VOICES_FILE = MODEL_DIR / "voices-v1.0.bin"
PRONUNCIATIONS_FILE = PROJECT_ROOT / "assets" / "pronunciations.json"

SAMPLE_RATE = 24000
SENTENCE_GAP = 0.12  # s of silence between sentences
SCENE_GAP = 0.22     # s of silence between scenes
LEAD_IN = 0.15       # s of silence at the very start


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class SceneTiming:
    index: int
    role: str
    headline: str
    start: float
    end: float
    words: List[Word] = field(default_factory=list)


@dataclass
class Narration:
    audio: np.ndarray
    sample_rate: int
    scenes: List[SceneTiming]

    @property
    def duration(self) -> float:
        return len(self.audio) / self.sample_rate

    def timeline_dict(self) -> dict:
        return {"duration": round(self.duration, 3), "scenes": [asdict(s) for s in self.scenes]}


def split_sentences(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def split_clauses(sentence: str) -> List[str]:
    parts = re.split(r"(?<=[,;:])\s+|\s+(?:-|–|—)\s+", sentence)
    return [p.strip() for p in parts if p and p.strip()]


def load_pronunciations(path: Path = PRONUNCIATIONS_FILE) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("respell", {})


def apply_pronunciations(text: str, respell: dict) -> str:
    """Replace hard names with phonetic spellings (affects audio only, never captions)."""
    for word, spoken in respell.items():
        text = re.sub(rf"\b{re.escape(word)}\b", spoken, text, flags=re.IGNORECASE)
    return text


def speech_span(audio: np.ndarray, sr: int, threshold_ratio: float = 0.04) -> Tuple[int, int]:
    """First and last sample above an amplitude threshold (trims leading/trailing silence)."""
    if len(audio) == 0:
        return 0, 0
    env = np.abs(audio)
    threshold = max(env.max() * threshold_ratio, 1e-4)
    loud = np.nonzero(env > threshold)[0]
    if len(loud) == 0:
        return 0, len(audio)
    return int(loud[0]), int(loud[-1]) + 1


def find_pauses(audio: np.ndarray, sr: int, start: int, end: int, min_gap: float = 0.09) -> List[Tuple[int, int]]:
    """Silent gaps (>= min_gap s) strictly inside [start, end)."""
    frame = int(sr * 0.01)
    if end - start < frame * 4:
        return []
    seg = np.abs(audio[start:end])
    n = len(seg) // frame
    energy = seg[: n * frame].reshape(n, frame).mean(axis=1)
    quiet = energy < max(energy.max() * 0.03, 1e-4)
    gaps, run_start = [], None
    for i, q in enumerate(quiet):
        if q and run_start is None:
            run_start = i
        elif not q and run_start is not None:
            if (i - run_start) * 0.01 >= min_gap and run_start > 0:
                gaps.append((start + run_start * frame, start + i * frame))
            run_start = None
    return gaps


def distribute_words(words: List[str], t0: float, t1: float) -> List[Word]:
    """Spread words over [t0, t1] proportionally to their length."""
    if not words:
        return []
    weights = [len(re.sub(r"\W", "", w)) + 2 for w in words]
    total = float(sum(weights))
    out, cursor = [], t0
    for word, weight in zip(words, weights):
        span = (t1 - t0) * weight / total
        out.append(Word(word, round(cursor, 3), round(cursor + span, 3)))
        cursor += span
    return out


def time_sentence(sentence: str, audio: np.ndarray, sr: int, offset: float) -> List[Word]:
    s, e = speech_span(audio, sr)
    clauses = split_clauses(sentence)
    if len(clauses) > 1:
        pauses = find_pauses(audio, sr, s, e)
        if len(pauses) == len(clauses) - 1:
            words: List[Word] = []
            bounds = [s] + [p for gap in pauses for p in gap] + [e]
            for i, clause in enumerate(clauses):
                c0, c1 = bounds[2 * i], bounds[2 * i + 1]
                words += distribute_words(clause.split(), offset + c0 / sr, offset + c1 / sr)
            return words
    return distribute_words(sentence.split(), offset + s / sr, offset + e / sr)


# Natural narrator voices. Aura-2 (Deepgram, on Cloudflare Workers AI) sounds like a real
# voice actor; Kokoro runs locally and is the always-available fallback.
AURA_MODEL = "@cf/deepgram/aura-2-en"
AURA_VOICES = ["apollo", "arcas", "orion", "odysseus", "atlas", "zeus",          # male
               "thalia", "luna", "andromeda", "helena", "asteria", "aurora"]   # female
KOKORO_VOICES = ["af_heart", "af_bella", "am_fenrir", "am_michael", "am_puck", "bm_george"]


def pick_voice(pool: List[str], seed: str) -> str:
    """Random-per-Reel but stable for a given Reel id (re-renders keep their voice)."""
    import hashlib

    return pool[int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(pool)]


class AuraVoice:
    """Deepgram Aura-2 via Cloudflare Workers AI REST (free daily allocation)."""

    def __init__(self, account_id: str, token: str, opener=None):
        import urllib.request

        self._url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{AURA_MODEL}"
        self._token = token
        self._opener = opener or urllib.request.urlopen

    @classmethod
    def from_env(cls) -> Optional["AuraVoice"]:
        import os

        a, t = os.environ.get("CF_ACCOUNT_ID"), os.environ.get("CF_API_TOKEN")
        return cls(a, t) if a and t else None

    def synthesize(self, text: str, voice: str) -> np.ndarray:
        import io
        import json as _json
        import urllib.request

        import soundfile as sf

        body = {"text": text, "speaker": voice, "encoding": "linear16", "container": "wav", "sample_rate": SAMPLE_RATE}
        req = urllib.request.Request(self._url, data=_json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json",
                                              "User-Agent": "anime-facts-automation/0.1"})
        with self._opener(req, timeout=90) as resp:
            raw = resp.read()
        audio, sr = sf.read(io.BytesIO(raw), dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if sr != SAMPLE_RATE:  # simple linear resample (Aura honours sample_rate, this is a guard)
            idx = np.linspace(0, len(audio) - 1, int(len(audio) * SAMPLE_RATE / sr))
            audio = np.interp(idx, np.arange(len(audio)), audio).astype(np.float32)
        return audio


class Narrator:
    def __init__(self, model_file: Path = MODEL_FILE, voices_file: Path = VOICES_FILE, aura: Optional[AuraVoice] = None):
        self._model_file, self._voices_file = model_file, voices_file
        self._kokoro = None
        self.aura = aura
        self.respell = load_pronunciations()
        self.backend = "kokoro"
        self.voice_used = None

    def _kokoro_engine(self):
        if self._kokoro is None:
            from kokoro_onnx import Kokoro  # imported lazily: heavy

            if not Path(self._model_file).exists() or not Path(self._voices_file).exists():
                raise FileNotFoundError(f"Kokoro model files missing in {MODEL_DIR}. Run: python -m video.fetch_models")
            self._kokoro = Kokoro(str(self._model_file), str(self._voices_file))
        return self._kokoro

    def voices(self) -> List[str]:
        return list(self._kokoro_engine().get_voices())

    def synthesize(self, text: str, voice: str, speed: float) -> np.ndarray:
        if self.backend == "aura":
            return self.aura.synthesize(text, voice)
        samples, sr = self._kokoro_engine().create(apply_pronunciations(text, self.respell), voice=voice, speed=speed, lang="en-us")
        if sr != SAMPLE_RATE:
            raise RuntimeError(f"unexpected Kokoro sample rate {sr}")
        return np.asarray(samples, dtype=np.float32)

    def narrate_random(self, scenes: List[dict], seed: str, speed: float = 1.1) -> "Narration":
        """Natural Aura voice picked at random for this Reel; whole narration falls back to Kokoro on failure."""
        if self.aura is not None:
            self.backend, self.voice_used = "aura", pick_voice(AURA_VOICES, seed)
            try:
                return self.narrate(scenes, voice=self.voice_used, speed=1.0)
            except Exception as exc:
                print(f"[tts] Aura voice unavailable ({type(exc).__name__}: {str(exc)[:120]}); using Kokoro")
        self.backend, self.voice_used = "kokoro", pick_voice(KOKORO_VOICES, seed)
        return self.narrate(scenes, voice=self.voice_used, speed=speed)

    def narrate(self, scenes: List[dict], voice: str = "am_fenrir", speed: float = 1.1) -> Narration:
        sr = SAMPLE_RATE
        chunks: List[np.ndarray] = [np.zeros(int(LEAD_IN * sr), dtype=np.float32)]
        cursor = LEAD_IN
        timings: List[SceneTiming] = []

        for index, scene in enumerate(scenes):
            scene_start = cursor
            words: List[Word] = []
            for sentence in split_sentences(scene["narration"]):
                audio = self.synthesize(sentence, voice, speed)
                s, e = speech_span(audio, sr)
                audio = audio[max(0, s - int(0.03 * sr)): e + int(0.05 * sr)]  # trim silence, keep a breath
                words += time_sentence(sentence, audio, sr, cursor)
                chunks.append(audio)
                cursor += len(audio) / sr
                chunks.append(np.zeros(int(SENTENCE_GAP * sr), dtype=np.float32))
                cursor += SENTENCE_GAP
            if index < len(scenes) - 1:
                chunks.append(np.zeros(int(SCENE_GAP * sr), dtype=np.float32))
                cursor += SCENE_GAP
            timings.append(SceneTiming(index, scene["role"], scene["headline"], round(scene_start, 3), round(cursor, 3), words))

        tail = np.zeros(int(0.8 * sr), dtype=np.float32)  # let the outro breathe
        chunks.append(tail)
        timings[-1].end = round(cursor + len(tail) / sr, 3)
        audio = np.concatenate(chunks)
        peak = float(np.abs(audio).max()) or 1.0
        return Narration(audio * (0.9 / peak), sr, timings)


def caption_chunks(words: List[Word], max_words: int = 3, max_chars: int = 18) -> List[List[Word]]:
    """Group words into short caption chunks, breaking after punctuation."""
    chunks: List[List[Word]] = []
    current: List[Word] = []
    for word in words:
        too_long = current and len(" ".join(w.text for w in current + [word])) > max_chars
        if current and (len(current) >= max_words or too_long):
            chunks.append(current)
            current = []
        current.append(word)
        if re.search(r"[.!?,;:]$", word.text):
            chunks.append(current)
            current = []
    if current:
        chunks.append(current)
    return chunks


def to_srt(scenes: List[SceneTiming]) -> str:
    def ts(t: float) -> str:
        ms = int(round(t * 1000))
        return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"

    lines, n = [], 1
    for scene in scenes:
        for chunk in caption_chunks(scene.words, max_words=6, max_chars=42):
            lines.append(f"{n}\n{ts(chunk[0].start)} --> {ts(chunk[-1].end)}\n{' '.join(w.text for w in chunk)}\n")
            n += 1
    return "\n".join(lines)
