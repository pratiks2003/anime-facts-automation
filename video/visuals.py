"""Frame rendering with Pillow: kinetic typography, karaoke captions, themed backgrounds.

All artwork is generated: no anime screenshots or character art (copyright-safe).
Layout respects Instagram Reels safe zones (top ~220 px and bottom ~420 px are
covered by UI; right ~140 px has the action buttons).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .tts import Narration, SceneTiming, caption_chunks

W, H, FPS = 1080, 1920, 30
FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"

RGB = Tuple[int, int, int]


@dataclass(frozen=True)
class Theme:
    bg_top: RGB
    bg_bottom: RGB
    glow: RGB
    accent: RGB
    accent2: RGB


THEMES: Dict[str, Theme] = {
    "One Piece": Theme((8, 22, 48), (3, 8, 20), (20, 90, 160), (255, 205, 40), (230, 57, 70)),
    "Naruto": Theme((40, 16, 4), (10, 5, 2), (200, 90, 10), (255, 140, 26), (60, 160, 255)),
    "_default": Theme((24, 10, 48), (6, 3, 14), (120, 50, 200), (255, 92, 168), (80, 220, 255)),
}

WHITE: RGB = (255, 255, 255)
DARK: RGB = (12, 10, 20)
THEORY_COLOR: RGB = (255, 120, 40)

ROLE_TAGS = {
    "hook": "DID YOU KNOW?",
    "twist": "THE TWIST",
    "theory": "FAN THEORY · NOT CONFIRMED",
    "what_if": "WHAT IF? · NOT CANON",
    "outro": "YOUR TURN",
}


@lru_cache(maxsize=64)
def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / name), size)


def ease_out_back(x: float) -> float:
    c1, c3 = 1.70158, 2.70158
    x = min(max(x, 0.0), 1.0)
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def mix(a: RGB, b: RGB, t: float) -> RGB:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))  # type: ignore[return-value]


def theme_for(anime: str) -> Theme:
    return THEMES.get(anime, THEMES["_default"])


# ---------------------------------------------------------------- backgrounds

def base_background(theme: Theme) -> Image.Image:
    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)
    for y in range(H):
        draw.line([(0, y), (W, y)], fill=mix(theme.bg_top, theme.bg_bottom, y / H))
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    ImageDraw.Draw(glow).ellipse([W * 0.5 - 520, 380, W * 0.5 + 520, 1400], fill=theme.glow)
    glow = glow.filter(ImageFilter.GaussianBlur(220))
    img = Image.blend(img, Image.composite(glow, img, Image.new("L", (W, H), 255)), 0.35)
    # diagonal manga-style speed lines, very faint
    lines = Image.new("L", (W, H), 0)
    ld = ImageDraw.Draw(lines)
    rng = random.Random(7)
    for _ in range(60):
        x = rng.randint(-600, W)
        ld.line([(x, 0), (x + 700, H)], fill=rng.randint(10, 26), width=rng.randint(2, 6))
    img.paste(mix(theme.glow, WHITE, 0.3), mask=lines)
    return img


def scene_background(base: Image.Image, theme: Theme, scene: SceneTiming, number: Optional[int]) -> Image.Image:
    img = base.copy()
    if number is not None:
        label = f"{number:02d}"
        layer = Image.new("L", (W, H), 0)
        d = ImageDraw.Draw(layer)
        f = font("Anton-Regular.ttf", 760)
        box = d.textbbox((0, 0), label, font=f)
        x = (W - (box[2] - box[0])) // 2 - box[0]
        d.text((x, 360 - box[1]), label, font=f, fill=0, stroke_width=6, stroke_fill=60)
        img.paste(mix(theme.glow, WHITE, 0.25), mask=layer)
    return img


class Embers:
    """Rising particles, deterministic per video."""

    def __init__(self, theme: Theme, count: int = 42, seed: int = 3):
        rng = random.Random(seed)
        self.items = [
            (rng.uniform(0, W), rng.uniform(0, H), rng.uniform(40, 140), rng.uniform(3, 9), rng.uniform(0, 6.28),
             mix(theme.bg_bottom, theme.accent if rng.random() < 0.6 else theme.accent2, rng.uniform(0.35, 0.75)))
            for _ in range(count)
        ]

    def draw(self, draw: ImageDraw.ImageDraw, t: float) -> None:
        for x0, y0, speed, r, phase, color in self.items:
            y = (y0 - speed * t) % (H + 40) - 20
            x = x0 + 18 * math.sin(t * 0.9 + phase)
            draw.ellipse([x - r, y - r, x + r, y + r], fill=color)


# ---------------------------------------------------------------- text blocks

def wrap(words: List[str], f: ImageFont.FreeTypeFont, max_width: int) -> List[List[str]]:
    lines: List[List[str]] = [[]]
    for word in words:
        trial = " ".join(lines[-1] + [word])
        if lines[-1] and f.getlength(trial) > max_width:
            lines.append([word])
        else:
            lines[-1].append(word)
    return lines


def headline_image(text: str, emphasis: List[str], theme: Theme, max_width: int = 900) -> Image.Image:
    words = text.upper().split()
    keys = {e.upper().strip(".,!?") for e in emphasis}
    for size in (170, 150, 132, 116, 100, 88):
        f = font("Anton-Regular.ttf", size)
        lines = wrap(words, f, max_width)
        if len(lines) <= 3:
            break
    line_h = int(size * 1.08)
    img = Image.new("RGBA", (max_width + 60, line_h * len(lines) + 60), (0, 0, 0, 0))
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d, sd = ImageDraw.Draw(img), ImageDraw.Draw(shadow)
    space = f.getlength(" ")
    for i, line in enumerate(lines):
        width = f.getlength(" ".join(line))
        x = (img.width - width) / 2
        y = 30 + i * line_h
        for word in line:
            color = theme.accent if word.strip(".,!?'\"") in keys else WHITE
            sd.text((x + 8, y + 10), word, font=f, fill=(0, 0, 0, 170))
            d.text((x, y), word, font=f, fill=color)
            x += f.getlength(word) + space
    shadow = shadow.filter(ImageFilter.GaussianBlur(6))
    return Image.alpha_composite(shadow, img)


def pill(draw: ImageDraw.ImageDraw, center_x: int, y: int, text: str, fill: RGB, color: RGB, size: int = 40) -> None:
    f = font("Poppins-Bold.ttf", size)
    tw = f.getlength(text)
    pad_x, h = 30, int(size * 1.7)
    x0 = center_x - tw / 2 - pad_x
    draw.rounded_rectangle([x0, y, x0 + tw + 2 * pad_x, y + h], radius=h // 2, fill=fill)
    draw.text((center_x, y + h / 2), text, font=f, fill=color, anchor="mm")


# ---------------------------------------------------------------- renderer

class FrameRenderer:
    def __init__(self, script: dict, narration: Narration, handle: str = "", sources_note: str = ""):
        self.script = script
        self.narration = narration
        self.theme = theme_for(script["anime"])
        self.handle = handle
        self.sources_note = sources_note
        self.duration = narration.duration
        self.scenes = narration.scenes
        self.embers = Embers(self.theme)

        base = base_background(self.theme)
        self.backgrounds, self.headlines, self.tags = [], [], []
        fact_no = 0
        for scene, spec in zip(self.scenes, script["scenes"]):
            number = None
            if scene.role not in ("hook", "outro"):
                fact_no += 1
                number = fact_no
            self.backgrounds.append(scene_background(base, self.theme, scene, number))
            self.headlines.append(headline_image(spec["headline"], spec.get("emphasis", []), self.theme))
            tag = ROLE_TAGS.get(scene.role) or (
                f"#{number}" if script["mode"] == "ranking" else f"FACT {number:02d}"
            )
            if script["mode"] == "what_if" and scene.role not in ("hook", "outro"):
                tag = ROLE_TAGS["what_if"]
            self.tags.append(tag)
        self.chunks = [caption_chunks(s.words) for s in self.scenes]
        self.brand = f"{script['anime'].upper()}  •  ANIME SECRETS"

    def scene_at(self, t: float) -> int:
        for i, s in enumerate(self.scenes):
            if t < s.end:
                return i
        return len(self.scenes) - 1

    def frame(self, t: float, captions: bool = True) -> Image.Image:
        i = self.scene_at(t)
        scene = self.scenes[i]
        u = t - scene.start
        img = self.backgrounds[i].copy()
        d = ImageDraw.Draw(img)
        self.embers.draw(d, t)

        # top: brand pill + progress bar
        pill(d, W // 2, 232, self.brand, mix(self.theme.bg_top, WHITE, 0.12), WHITE, size=30)
        bar_y = 312
        d.rounded_rectangle([140, bar_y, W - 140, bar_y + 8], radius=4, fill=mix(self.theme.bg_top, WHITE, 0.18))
        d.rounded_rectangle([140, bar_y, 140 + (W - 280) * min(t / self.duration, 1), bar_y + 8], radius=4, fill=self.theme.accent)

        # scene tag
        tag = self.tags[i]
        warn = scene.role in ("theory", "what_if") or tag.startswith(("FAN THEORY", "WHAT IF"))
        tag_alpha_in = min(max(u / 0.2, 0), 1)
        if tag_alpha_in > 0:
            pill(d, W // 2, int(470 - 30 * (1 - tag_alpha_in)), tag,
                 THEORY_COLOR if warn else self.theme.accent, DARK, size=38)

        # headline: pop-in with overshoot, shake on hook impact
        head = self.headlines[i]
        k = ease_out_back(u / 0.38)
        scale = 0.55 + 0.45 * k
        if scale != 1.0:
            hw, hh = max(1, int(head.width * scale)), max(1, int(head.height * scale))
            head = head.resize((hw, hh), Image.BILINEAR)
        alpha = min(max(u / 0.12, 0), 1)
        if alpha < 1:
            head = head.copy()
            head.putalpha(head.getchannel("A").point(lambda a: int(a * alpha)))
        dx = dy = 0
        if scene.role == "hook" and u < 0.5:
            amp = 14 * (1 - u / 0.5)
            dx, dy = int(amp * math.sin(u * 90)), int(amp * math.cos(u * 70))
        cy = 800
        img.paste(head, (W // 2 - head.width // 2 + dx, cy - head.height // 2 + dy), head)

        # karaoke captions
        if captions:
            self.draw_caption(d, i, t)

        # outro: follow call-to-action + sources
        if scene.role == "outro":
            if self.handle and u > 0.4:
                pill(d, W // 2, 1395, f"FOLLOW {self.handle}", self.theme.accent2, WHITE, size=34)
            if self.sources_note:
                d.text((W // 2, 1490), self.sources_note, font=font("Poppins-SemiBold.ttf", 24),
                       fill=mix(WHITE, self.theme.bg_top, 0.35), anchor="mm")

        # flash on scene change
        if u < 0.1 and i > 0:
            flash = Image.new("RGB", (W, H), WHITE)
            img = Image.blend(img, flash, 0.55 * (1 - u / 0.1))
        return img

    def draw_caption(self, d: ImageDraw.ImageDraw, i: int, t: float) -> None:
        chunks = self.chunks[i]
        current = None
        for chunk in chunks:
            if chunk[0].start - 0.05 <= t <= chunk[-1].end + 0.25:
                current = chunk
        if current is None:
            return
        f = font("Poppins-ExtraBold.ttf", 68)
        texts = [w.text.upper() for w in current]
        space = f.getlength(" ") + 18  # extra room for the 9px outline on both words
        total = sum(f.getlength(x) for x in texts) + space * (len(texts) - 1)
        x = (W - total) / 2
        y = 1200
        spoken = [w for w in current if w.start <= t]
        active_word = spoken[-1] if spoken else None  # exactly one highlighted word
        for word, text in zip(current, texts):
            active = word is active_word
            color = self.theme.accent if active else WHITE
            lift = -6 if active else 0
            d.text((x, y + lift), text, font=f, fill=color, stroke_width=9, stroke_fill=DARK)
            x += f.getlength(text) + space
