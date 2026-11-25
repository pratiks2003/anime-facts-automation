"""Image Inspector: every picture is checked BEFORE it goes into a Reel. Free and local (no API quota).

1. Technical: big enough for its use (no blurry over-zoom), not blank/flat, sane aspect ratio.
2. Duplicates: perceptual hash, so the same picture under two URLs counts as one.
3. Content (CLIP ViT-B/32, ONNX, CPU): anime art vs real photo / text page / logo / merch; NSFW screen;
   for a named character, similarity to that character's official AniList portrait (same character
   -> higher similarity); for AI / stock pictures, match against the scene description.

  python -m video.image_check fetch          # download the pinned CLIP model (checksum verified)
  python -m video.image_check <img> [subject] # inspect one image
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
CLIP_DIR = ROOT / "models" / "clip"
CLIP_COMMIT = "6b7c523703ae26e39be4b03774966f8571c7f523"  # Xenova/clip-vit-base-patch32 (MIT), pinned
CLIP_FILES = {
    "onnx/vision_model_quantized.onnx": "583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299",
    "onnx/text_model_quantized.onnx": "73baab855d406190da9faa498cfedf65f15cf309f4cc7385b7b032e6d08e5c3a",
    "tokenizer.json": "f7f3b7af117d467b58374797691a6438d3e6b9e9cef800dfd5dced7f697a90cd",
}
MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)

# Zero-shot labels: what kind of picture is this?
GOOD_LABELS = ["an anime screenshot", "anime character artwork", "a manga panel", "anime key visual poster",
               "a cartoon illustration"]
BAD_LABELS = {  # kind -> prompts (probabilities are summed per kind)
    "photo": ["a real photograph", "a photo of a person", "a photo of nature, animals or the sea", "a photo of a city or building"],
    "text": ["a page of text or a screenshot of a website", "a title card with large Japanese text",
             "an anime frame covered with Japanese captions, credits or a price sign", "a logo with stars and katakana"],
    "logo": ["a logo or title text on a plain background"],
    "merch": ["a toy figure or merchandise product photo"],
    "game": ["a video game user interface screenshot", "a 3D video game character render", "a Fortnite skin"],
    "sketch": ["a rough pencil sketch", "an unfinished storyboard or animation key-frame drawing", "messy line art scribbles"],
    "nsfw": ["explicit sexual or nude content"],
}
UPSCALE_FULL = 1.6  # a picture may be enlarged at most this much when shown full-screen


def log(msg: str) -> None:
    print(f"[inspect] {msg}", file=sys.stderr, flush=True)


# ------------------------------------------------------------------ technical

def dhash(img: Image.Image, size: int = 8) -> int:
    g = img.convert("L").resize((size + 1, size), Image.BILINEAR)
    a = np.asarray(g, dtype=np.int16)
    bits = (a[:, 1:] > a[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def full_bleed_ok(width: int, height: int, out_w: int = 1080, out_h: int = 1920) -> bool:
    """Can it fill a 9:16 frame (cover) without being enlarged more than UPSCALE_FULL?"""
    scale = max(out_w / width, out_h / height)
    return scale <= UPSCALE_FULL


def technical(img: Image.Image) -> Optional[str]:
    """Reason to reject, or None."""
    w, h = img.size
    if min(w, h) < 200:
        return f"too small ({w}x{h})"
    if not 0.3 <= w / h <= 3.5:
        return f"odd aspect ratio ({w}x{h})"
    small = np.asarray(img.convert("L").resize((64, 64)), dtype=np.float32)
    if small.std() < 12:
        return "flat / nearly blank"
    return None


# ------------------------------------------------------------------ CLIP

def fetch_clip(opener=urllib.request.urlopen) -> bool:
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    for rel, expected in CLIP_FILES.items():
        path = CLIP_DIR / Path(rel).name
        if path.exists() and _sha256(path) == expected:
            continue
        url = f"https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/{CLIP_COMMIT}/{rel}"
        tmp = path.with_suffix(path.suffix + ".part")
        with opener(urllib.request.Request(url, headers={"User-Agent": "anime-facts-automation"}), timeout=120) as r, \
                open(tmp, "wb") as fh:
            while True:
                block = r.read(1 << 20)
                if not block:
                    break
                fh.write(block)
        if _sha256(tmp) != expected:
            tmp.unlink()
            log(f"checksum mismatch for {rel}")
            return False
        tmp.replace(path)
    return True


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class Clip:
    def __init__(self, model_dir: Path = CLIP_DIR):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        self.vision = ort.InferenceSession(str(model_dir / "vision_model_quantized.onnx"), opts, providers=["CPUExecutionProvider"])
        self.text = ort.InferenceSession(str(model_dir / "text_model_quantized.onnx"), opts, providers=["CPUExecutionProvider"])
        self.tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self._text_cache: Dict[str, np.ndarray] = {}

    @classmethod
    def load(cls) -> Optional["Clip"]:
        try:
            if not all((CLIP_DIR / Path(r).name).exists() for r in CLIP_FILES) and not fetch_clip():
                return None
            return cls()
        except Exception as exc:
            log(f"CLIP unavailable ({type(exc).__name__}: {exc}); technical checks only")
            return None

    def image(self, img: Image.Image) -> np.ndarray:
        img = img.convert("RGB")
        w, h = img.size
        s = 224 / min(w, h)
        img = img.resize((max(224, round(w * s)), max(224, round(h * s))), Image.BICUBIC)
        left, top = (img.width - 224) // 2, (img.height - 224) // 2
        x = (np.asarray(img.crop((left, top, left + 224, top + 224)), dtype=np.float32) / 255 - MEAN) / STD
        out = self.vision.run(None, {"pixel_values": x.transpose(2, 0, 1)[None]})[0][0]
        return out / np.linalg.norm(out)

    def texts(self, labels: Sequence[str]) -> np.ndarray:
        missing = [t for t in labels if t not in self._text_cache]
        if missing:
            encs = [self.tok.encode(t).ids[:77] for t in missing]
            for t, ids in zip(missing, encs):
                out = self.text.run(None, {"input_ids": np.array([ids], dtype=np.int64)})[0][0]
                self._text_cache[t] = out / np.linalg.norm(out)
        return np.stack([self._text_cache[t] for t in labels])


def softmax(x: np.ndarray, scale: float = 100.0) -> np.ndarray:
    z = np.exp(scale * (x - x.max()))
    return z / z.sum()


# ------------------------------------------------------------------ inspector

@dataclass
class Verdict:
    ok: bool
    reason: str
    kind: str = ""                 # anime | photo | text | ...
    full_bleed: bool = True        # big enough to fill the frame
    likeness: Optional[float] = None
    score: float = 0.0             # ranking among accepted pictures
    hash: int = 0


@dataclass
class Inspector:
    clip: Optional[Clip] = None
    seen: List[int] = field(default_factory=list)        # hashes already used in this Reel
    refs: Dict[str, np.ndarray] = field(default_factory=dict)  # character -> portrait embedding
    rejected: int = 0
    accepted: int = 0

    @classmethod
    def create(cls) -> "Inspector":
        return cls(Clip.load())

    def add_reference(self, subject: str, portrait: Path) -> None:
        """Official portrait of a character: wiki pictures of that character must resemble it."""
        if self.clip and subject and portrait and subject not in self.refs:
            try:
                self.refs[subject] = self.clip.image(Image.open(portrait))
            except Exception:
                pass

    def inspect(self, path: Path, subject: str = "", expect: str = "anime", description: str = "",
                register: bool = True) -> Verdict:
        """expect: "anime" (wiki/official art, manga panels), "clip" (video clip frame: also rejects rough sketches /
        animation-process footage) or "scene" (AI / stock picture matching `description`)."""
        try:
            img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        except Exception as exc:
            return self._reject(f"unreadable ({type(exc).__name__})")
        problem = technical(img)
        if problem:
            return self._reject(problem)
        h = dhash(img)
        if any(hamming(h, s) <= 6 for s in self.seen):
            return self._reject("duplicate of a picture already used", h=h)
        full = full_bleed_ok(*img.size)
        score = min(img.width * img.height, 2_000_000) / 1_000_000 + (0.5 if full else 0)
        kind, likeness = "unchecked", None
        if self.clip:
            emb = self.clip.image(img)
            labels = GOOD_LABELS + [t for ts in BAD_LABELS.values() for t in ts]
            probs = softmax(self.clip.texts(labels) @ emb)
            good = float(probs[: len(GOOD_LABELS)].sum())
            bad, i = {}, len(GOOD_LABELS)
            for k, ts in BAD_LABELS.items():
                bad[k] = float(probs[i:i + len(ts)].sum())
                i += len(ts)
            if expect != "clip":
                bad.pop("sketch", None)  # B&W manga panels look "sketchy" to CLIP; the sketch rule is for video clips only
            worst = max(bad, key=bad.get)
            kind = "anime" if good >= max(bad.values()) else worst
            if bad["nsfw"] > 0.25:
                return self._reject("NSFW risk", h=h, kind="nsfw")
            if expect in ("anime", "clip") and bad[worst] > good:
                return self._reject(f"not anime art (looks like {worst}: {bad[worst]:.0%})", h=h, kind=worst)
            score += good
            if expect == "scene" and description:
                match = float(self.clip.texts([description[:300], "an unrelated random picture"]) @ emb @ np.array([1, -1]))
                if match < 0:
                    return self._reject("does not match the scene description", h=h, kind=kind)
                score += 5 * match
            ref = self.refs.get(subject)
            if ref is not None:  # soft ranking only: CLIP sees "anime style", not identity (Zoro vs Luffy = 0.84)
                likeness = float(ref @ emb)
                score += 2 * max(0.0, likeness - 0.6)
        if register:
            self.seen.append(h)
        self.accepted += 1
        return Verdict(True, "ok", kind, full, likeness, round(score, 3), h)

    def _reject(self, reason: str, h: int = 0, kind: str = "") -> Verdict:
        self.rejected += 1
        return Verdict(False, reason, kind, False, None, 0.0, h)


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if argv and argv[0] == "fetch":
        return 0 if fetch_clip() else 1
    ins = Inspector.create()
    for p in argv[:1]:
        print(ins.inspect(Path(p), argv[1] if len(argv) > 1 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
