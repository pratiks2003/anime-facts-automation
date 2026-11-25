"""Edit motion for still pictures: what makes manga/anime edits feel alive without footage.

- 2.5D parallax: a free depth model (Depth Anything V2 Small, Apache-2.0, ONNX, pinned + checksum)
  splits the character (near) from the background (far); the layers drift at different speeds.
- Beat punches: a quick zoom-punch on the music's beat.
- Shake: a short camera shake when a highlighted word lands.
- Speed lines: anime action lines on action words ("punch", "destroy", "power"...).

All strengths come from the Style Spec (beat_punch, shake, speed_lines, parallax).
"""

from __future__ import annotations

import hashlib
import math
import random
import sys
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
DEPTH_DIR = ROOT / "models" / "depth"
DEPTH_URL = ("https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/"
             "4472b7362082ad9968fee890ca0f1e5aca36b93d/onnx/model_quantized.onnx")
DEPTH_SHA256 = "fcf51f1b230362b28690bb9d1809bf0431f29cad20534e3f589bd7285547f20d"

ACTION_WORDS = {
    "punch", "punched", "hit", "hits", "strike", "strikes", "slash", "slashed", "cut", "explode", "exploded", "explosion",
    "destroy", "destroyed", "destroys", "kill", "killed", "kills", "attack", "attacks", "attacked", "fight", "fights",
    "fought", "battle", "war", "power", "powerful", "rage", "smash", "smashed", "crush", "crushed", "beat", "beats",
    "defeat", "defeated", "blast", "unleash", "unleashed", "awaken", "awakened", "awakens", "strongest", "fastest",
    "storm", "shatter", "shattered", "clash", "boom", "dead", "died", "die", "brutal", "insane", "monster", "god",
}


def log(msg: str) -> None:
    print(f"[motion] {msg}", file=sys.stderr, flush=True)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch_depth(opener=urllib.request.urlopen) -> bool:
    path = DEPTH_DIR / "model_quantized.onnx"
    if path.exists() and _sha256(path) == DEPTH_SHA256:
        return True
    DEPTH_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    with opener(urllib.request.Request(DEPTH_URL, headers={"User-Agent": "anime-facts-automation"}), timeout=120) as r, \
            open(tmp, "wb") as fh:
        while True:
            block = r.read(1 << 20)
            if not block:
                break
            fh.write(block)
    if _sha256(tmp) != DEPTH_SHA256:
        tmp.unlink()
        log("depth model checksum mismatch")
        return False
    tmp.replace(path)
    return True


class Depth:
    _instance: Optional["Depth"] = None
    _failed = False

    def __init__(self):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        self.session = ort.InferenceSession(str(DEPTH_DIR / "model_quantized.onnx"), opts, providers=["CPUExecutionProvider"])

    @classmethod
    def get(cls) -> Optional["Depth"]:
        if cls._instance is None and not cls._failed:
            try:
                if not fetch_depth():
                    raise RuntimeError("download failed")
                cls._instance = cls()
            except Exception as exc:
                cls._failed = True
                log(f"depth model unavailable ({type(exc).__name__}: {exc}); parallax off")
        return cls._instance

    def __call__(self, img: Image.Image) -> np.ndarray:
        """Relative depth in [0, 1] (1 = nearest), same size as `img`."""
        x = img.convert("RGB").resize((518, 518), Image.BICUBIC)
        a = (np.asarray(x, np.float32) / 255 - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
        d = self.session.run(None, {"pixel_values": a.transpose(2, 0, 1)[None].astype(np.float32)})[0][0]
        d = (d - d.min()) / max(1e-6, float(d.max() - d.min()))
        return np.asarray(Image.fromarray((d * 255).astype(np.uint8)).resize(img.size, Image.BILINEAR), np.float32) / 255


def split_layers(img: Image.Image, depth: np.ndarray) -> Optional[Tuple[Image.Image, Image.Image]]:
    """(background with the subject painted out, subject as RGBA). None when there is no clear subject
    or it fills so much of the frame that moving it would reveal artefacts."""
    values = (depth * 255).astype(np.uint8)
    hist = np.bincount(values.ravel(), minlength=256).astype(np.float64)
    # Otsu threshold: the natural split between near (subject) and far (background)
    total, sum_all = hist.sum(), np.dot(np.arange(256), hist)
    best, thresh, w0, s0 = 0.0, 128, 0.0, 0.0
    for k in range(256):
        w0 += hist[k]
        s0 += k * hist[k]
        if w0 == 0 or w0 == total:
            continue
        m0, m1 = s0 / w0, (sum_all - s0) / (total - w0)
        between = w0 * (total - w0) * (m0 - m1) ** 2
        if between > best:
            best, thresh = between, k
    mask = np.clip((values.astype(np.float32) - thresh) / 10.0, 0, 1)
    share = float((mask > 0.5).mean())
    if not 0.06 <= share <= 0.42:
        return None
    m = Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.5))
    # background: the subject's area (binary, grown) is refilled only from real background pixels
    hole = np.asarray(m.filter(ImageFilter.MaxFilter(15)), np.uint8) > 10
    keep = (~hole).astype(np.float32)
    rgb = np.asarray(img.convert("RGB"), np.float32)
    small = (max(1, img.width // 8), max(1, img.height // 8))

    def blur(a: np.ndarray) -> np.ndarray:
        im = Image.fromarray(np.uint8(np.clip(a, 0, 255)))
        return np.asarray(im.resize(small, Image.BILINEAR).filter(ImageFilter.GaussianBlur(8))
                          .resize(img.size, Image.BILINEAR), np.float32)

    num = blur(rgb * keep[..., None])
    den = blur(keep * 255)[..., None] / 255  # how much real background each blurred pixel came from
    filled = num / np.maximum(den, 0.03)
    bg = np.where(hole[..., None], filled, rgb)
    background = Image.fromarray(np.uint8(np.clip(bg, 0, 255)))
    subject = img.convert("RGB").copy()
    subject.putalpha(m)
    return background, subject


def speed_lines(size: Tuple[int, int], seed: int = 0, count: int = 90) -> Image.Image:
    """Radial anime action lines (white, transparent centre)."""
    w, h = size
    rng = random.Random(seed)
    layer = Image.new("L", size, 0)
    d = ImageDraw.Draw(layer)
    cx, cy = w / 2, h * 0.45
    radius = math.hypot(w, h)
    for _ in range(count):
        ang = rng.uniform(0, 2 * math.pi)
        inner = rng.uniform(0.32, 0.5) * min(w, h)
        width = rng.uniform(0.004, 0.018)
        p1 = (cx + math.cos(ang - width) * radius, cy + math.sin(ang - width) * radius)
        p2 = (cx + math.cos(ang + width) * radius, cy + math.sin(ang + width) * radius)
        p3 = (cx + math.cos(ang) * inner, cy + math.sin(ang) * inner)
        d.polygon([p1, p2, p3], fill=rng.randint(150, 235))
    lines = Image.new("RGBA", size, (255, 255, 255, 0))
    lines.putalpha(layer.filter(ImageFilter.GaussianBlur(1)))
    return lines


def ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 2
