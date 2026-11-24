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


_GLYPH_FIXES = str.maketrans({
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2015": "-", "\u2212": "-",
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u00a0": " ", "\u202f": " ",
})


def clean_text(text: str) -> str:
    """Replace characters the bundled fonts don't have (fancy dashes/quotes/spaces)."""
    return str(text).translate(_GLYPH_FIXES)


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


CARD_X, CARD_Y, CARD_W, CARD_H = 60, 372, 960, 600
CARD_OVERSCAN = 1.12  # extra size for the slow Ken Burns zoom


def cover(img: Image.Image, size: Tuple[int, int]) -> Image.Image:
    """Scale and centre-crop to fill `size` (like CSS object-fit: cover)."""
    tw, th = size
    scale = max(tw / img.width, th / img.height)
    resized = img.resize((max(tw, int(img.width * scale + 0.5)), max(th, int(img.height * scale + 0.5))), Image.LANCZOS)
    left, top = (resized.width - tw) // 2, (resized.height - th) // 2
    return resized.crop((left, top, left + tw, top + th))


def image_background(photo: Image.Image, theme: Theme) -> Image.Image:
    bg = cover(photo, (W // 4, H // 4)).filter(ImageFilter.GaussianBlur(10)).resize((W, H), Image.BILINEAR)
    tint = Image.new("RGB", (W, H), theme.bg_bottom)
    return Image.blend(bg, tint, 0.62)


FULL_OVERSCAN = 1.10


def full_shade() -> Image.Image:
    """Alpha mask darkening the top (brand) and lower half (text) over full-bleed art."""
    mask = Image.new("L", (1, H), 0)
    for y in range(H):
        if y < 400:
            a = 170 * (1 - y / 400)
        elif y < 780:
            a = 0
        elif y < 1180:
            a = 225 * (y - 780) / 400
        else:
            a = 225 + 20 * (y - 1180) / (H - 1180)
        mask.putpixel((0, y), int(a))
    return mask.resize((W, H))


def compose_full(art: Image.Image) -> Image.Image:
    """Canvas for full-bleed art. Square art (FLUX.1 schnell) is shown whole across the
    top and fades into a blurred extension of itself, instead of losing its sides."""
    size = (int(W * FULL_OVERSCAN), int(H * FULL_OVERSCAN))
    if not 0.85 <= art.width / art.height <= 1.15:
        return cover(art, size)
    canvas = cover(art, (size[0] // 6, size[1] // 6)).filter(ImageFilter.GaussianBlur(6)).resize(size, Image.BILINEAR)
    canvas = Image.blend(canvas, Image.new("RGB", size, (0, 0, 0)), 0.35)
    side = size[0]
    sharp = art.resize((side, side), Image.LANCZOS)
    fade = Image.new("L", (side, side), 255)
    fd = ImageDraw.Draw(fade)
    for i in range(220):  # feather the bottom edge into the blurred background
        fd.line([(0, side - 220 + i), (side, side - 220 + i)], fill=int(255 * (1 - i / 220)))
    canvas.paste(sharp, (0, int(size[1] * 0.11)), fade)
    return canvas


POSTER_TOP, POSTER_MAX_W, POSTER_MAX_H, POSTER_MAX_SCALE = 350, 720, 640, 1.7


def prepare_poster(img: Image.Image) -> Image.Image:
    """Official key art scaled near native size (never more than 1.7x) with rounded corners."""
    scale = min(POSTER_MAX_W / img.width, POSTER_MAX_H / img.height, POSTER_MAX_SCALE)
    poster = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", poster.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, poster.width - 1, poster.height - 1], radius=28, fill=255)
    poster.putalpha(mask)
    return poster


def poster_background(bg: Image.Image, poster: Image.Image, theme: Theme) -> Image.Image:
    """Blurred banner/poster backdrop plus a soft shadow where the poster sits."""
    base = cover(bg, (W // 6, H // 6)).filter(ImageFilter.GaussianBlur(4)).resize((W, H), Image.BILINEAR)
    base = Image.blend(base, Image.new("RGB", (W, H), theme.bg_bottom), 0.55)
    shadow = Image.new("L", (W, H), 0)
    x0 = (W - poster.width) // 2
    y0 = POSTER_TOP + (POSTER_MAX_H - poster.height) // 2
    ImageDraw.Draw(shadow).rounded_rectangle([x0 + 10, y0 + 22, x0 + poster.width + 10, y0 + poster.height + 22], radius=30, fill=190)
    base.paste((0, 0, 0), mask=shadow.filter(ImageFilter.GaussianBlur(22)))
    return base


def card_mask() -> Image.Image:
    mask = Image.new("L", (CARD_W, CARD_H), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, CARD_W - 1, CARD_H - 1], radius=34, fill=255)
    return mask


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


def headline_image(text: str, emphasis: List[str], theme: Theme, max_width: int = 900,
                   sizes: Tuple[int, ...] = (170, 150, 132, 116, 100, 88), max_lines: int = 3) -> Image.Image:
    words = clean_text(text).upper().split()
    keys = {clean_text(e).upper().strip(".,!?") for e in emphasis}
    for size in sizes:
        f = font("Anton-Regular.ttf", size)
        lines = wrap(words, f, max_width)
        if len(lines) <= max_lines:
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
    text = clean_text(text)
    f = font("Poppins-Bold.ttf", size)
    tw = f.getlength(text)
    pad_x, h = 30, int(size * 1.7)
    x0 = center_x - tw / 2 - pad_x
    draw.rounded_rectangle([x0, y, x0 + tw + 2 * pad_x, y + h], radius=h // 2, fill=fill)
    draw.text((center_x, y + h / 2), text, font=f, fill=color, anchor="mm")


# ---------------------------------------------------------------- renderer

class FrameRenderer:
    def __init__(self, script: dict, narration: Narration, handle: str = "", sources_note: str = "",
                 images: Optional[List[Optional[dict]]] = None):
        """images: per scene None or {"path": ..., "credit": "Photo: X · CC BY 2.0"}."""
        self.script = script
        self.narration = narration
        self.theme = theme_for(script["anime"])
        self.handle = handle
        self.sources_note = sources_note
        self.duration = narration.duration
        self.scenes = narration.scenes
        self.embers = Embers(self.theme)
        images = images or [None] * len(self.scenes)
        self.mask = card_mask()
        self.shade = full_shade()
        self.fulls: List[Optional[Image.Image]] = []
        self.posters: List[Optional[Image.Image]] = []

        base = base_background(self.theme)
        self.backgrounds, self.headlines, self.tags, self.cards, self.credits = [], [], [], [], []
        fact_no = 0
        numbered_total = sum(1 for sc in self.scenes if sc.role not in ("hook", "outro"))
        for scene, spec, image in zip(self.scenes, script["scenes"], images):
            number = None
            if scene.role not in ("hook", "outro"):
                fact_no += 1
                number = fact_no
            if image and image.get("layout") == "poster":
                art = Image.open(image["path"]).convert("RGB")
                poster = prepare_poster(art)
                bg = Image.open(image["background"]).convert("RGB") if image.get("background") else art
                self.posters.append(poster)
                self.fulls.append(None)
                self.backgrounds.append(poster_background(bg, poster, self.theme))
                self.cards.append(None)
                self.credits.append("")
                self.headlines.append(headline_image(spec["headline"], spec.get("emphasis", []), self.theme,
                                                     sizes=(124, 112, 100, 90, 80), max_lines=2))
                self.tags.append(self.scene_tag(scene, spec, number, script, numbered_total))
                continue
            self.posters.append(None)
            if image and image.get("layout") == "full":
                art = Image.open(image["path"]).convert("RGB")
                self.fulls.append(compose_full(art))
                self.backgrounds.append(None)
                self.cards.append(None)
                self.credits.append(image.get("credit", ""))
                self.headlines.append(headline_image(spec["headline"], spec.get("emphasis", []), self.theme,
                                                     sizes=(132, 118, 106, 94, 84), max_lines=2))
                self.tags.append(self.scene_tag(scene, spec, number, script, numbered_total))
                continue
            self.fulls.append(None)
            if image:
                photo = Image.open(image["path"]).convert("RGB")
                self.backgrounds.append(image_background(photo, self.theme))
                self.cards.append(cover(photo, (int(CARD_W * CARD_OVERSCAN), int(CARD_H * CARD_OVERSCAN))))
                self.credits.append(image.get("credit", ""))
                self.headlines.append(headline_image(spec["headline"], spec.get("emphasis", []), self.theme,
                                                     sizes=(124, 112, 100, 90, 80), max_lines=2))
            else:
                self.backgrounds.append(scene_background(base, self.theme, scene, number))
                self.cards.append(None)
                self.credits.append("")
                self.headlines.append(headline_image(spec["headline"], spec.get("emphasis", []), self.theme))
            self.tags.append(self.scene_tag(scene, spec, number, script, numbered_total))
        self.chunks = [caption_chunks(s.words) for s in self.scenes]
        self.brand = ("ANIME NEWS  •  SENPAI SECRETS" if script["anime"] == "Anime"
                      else f"{clean_text(script['anime']).upper()[:28]}  •  ANIME SECRETS")

    @staticmethod
    def scene_tag(scene: SceneTiming, spec: dict, number: Optional[int], script: dict, total: int) -> str:
        """Label pill: explicit script tag > role tag > countdown (#3..#1) > FACT nn."""
        if spec.get("tag"):
            return str(spec["tag"]).upper()[:26]
        if scene.role in ROLE_TAGS:
            return ROLE_TAGS[scene.role]
        if script.get("mode") == "ranking":
            return f"#{total - number + 1}"
        return f"FACT {number:02d}"

    def scene_at(self, t: float) -> int:
        for i, s in enumerate(self.scenes):
            if t < s.end:
                return i
        return len(self.scenes) - 1

    def frame(self, t: float, captions: bool = True) -> Image.Image:
        i = self.scene_at(t)
        scene = self.scenes[i]
        u = t - scene.start
        full = self.fulls[i] is not None
        if full:
            img = self.full_frame(i, u, scene.end - scene.start)
        else:
            img = self.backgrounds[i].copy()
        d = ImageDraw.Draw(img)
        self.embers.draw(d, t)

        # top: brand pill + progress bar
        pill(d, W // 2, 232, self.brand, mix(self.theme.bg_top, WHITE, 0.12), WHITE, size=30)
        bar_y = 312
        d.rounded_rectangle([140, bar_y, W - 140, bar_y + 8], radius=4, fill=mix(self.theme.bg_top, WHITE, 0.18))
        d.rounded_rectangle([140, bar_y, 140 + (W - 280) * min(t / self.duration, 1), bar_y + 8], radius=4, fill=self.theme.accent)

        if self.posters[i] is not None:
            self.draw_poster(img, i, u, scene.end - scene.start)
        has_card = self.cards[i] is not None or self.posters[i] is not None
        if self.cards[i] is not None:
            self.draw_card(img, d, i, u, scene.end - scene.start)

        # scene tag
        tag = self.tags[i]
        warn = scene.role in ("theory", "what_if") or tag.startswith(("FAN THEORY", "WHAT IF"))
        tag_alpha_in = min(max(u / 0.2, 0), 1)
        tag_y = 930 if full else (CARD_Y + CARD_H - 34 if has_card else 470)
        if tag_alpha_in > 0:
            pill(d, W // 2, int(tag_y - 30 * (1 - tag_alpha_in)), tag,
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
        cy = 1150 if full else (1175 if has_card else 800)
        img.paste(head, (W // 2 - head.width // 2 + dx, cy - head.height // 2 + dy), head)

        # karaoke captions
        caption_y = 1365 if (has_card or full) else 1200
        if captions:
            self.draw_caption(d, i, t, y=caption_y)

        # outro: follow call-to-action + sources
        if scene.role == "outro":
            # always a clear gap below the subtitle line (captions are ~90px tall incl. outline),
            # and still above Instagram's bottom UI
            follow_y = caption_y + 125
            src_y = follow_y + 95
            if self.handle and u > 0.4:
                pill(d, W // 2, follow_y, f"FOLLOW {self.handle}", self.theme.accent2, WHITE, size=34)
            if self.sources_note:
                d.text((W // 2, src_y), self.sources_note, font=font("Poppins-SemiBold.ttf", 24),
                       fill=mix(WHITE, self.theme.bg_top, 0.35), anchor="mm")

        # flash on scene change
        if u < 0.1 and i > 0:
            flash = Image.new("RGB", (W, H), WHITE)
            img = Image.blend(img, flash, 0.55 * (1 - u / 0.1))
        return img

    def draw_poster(self, img: Image.Image, i: int, u: float, length: float) -> None:
        poster = self.posters[i]
        progress = min(max(u / max(length, 0.1), 0.0), 1.0)
        scale = 1.0 + 0.045 * progress  # gentle push-in
        if scale != 1.0:
            poster = poster.resize((int(poster.width * scale), int(poster.height * scale)), Image.BILINEAR)
        slide = int(70 * (1 - ease_out_back(u / 0.4)))
        x = (W - poster.width) // 2
        y = POSTER_TOP + (POSTER_MAX_H - poster.height) // 2 + slide
        img.paste(poster, (x, y), poster)

    def full_frame(self, i: int, u: float, length: float) -> Image.Image:
        """Full-screen artwork with slow zoom-in and readability shading."""
        big = self.fulls[i]
        progress = min(max(u / max(length, 0.1), 0.0), 1.0)
        span = 1.0 + (FULL_OVERSCAN - 1.0) * (1.0 - progress)
        cw, ch = int(W * span), int(H * span)
        left, top = (big.width - cw) // 2, int((big.height - ch) * 0.35)
        img = big.crop((left, top, left + cw, top + ch)).resize((W, H), Image.BILINEAR)
        img.paste((0, 0, 0), mask=self.shade)
        credit = self.credits[i]
        if credit:
            ImageDraw.Draw(img).text((W - 150, 372), credit, font=font("Poppins-SemiBold.ttf", 20),
                                     fill=(230, 230, 230), anchor="rm", stroke_width=3, stroke_fill=(0, 0, 0))
        return img

    def draw_card(self, img: Image.Image, d: ImageDraw.ImageDraw, i: int, u: float, length: float) -> None:
        """Photo card with slow Ken Burns zoom, slide-in, accent border and credit."""
        big = self.cards[i]
        progress = min(max(u / max(length, 0.1), 0.0), 1.0)
        span = 1.0 + (CARD_OVERSCAN - 1.0) * (1.0 - progress)  # zoom in over the scene
        cw, ch = int(CARD_W * span), int(CARD_H * span)
        left = (big.width - cw) // 2 + int((big.width - cw) * 0.3 * (progress - 0.5))
        top = (big.height - ch) // 2
        frame = big.crop((left, top, left + cw, top + ch)).resize((CARD_W, CARD_H), Image.BILINEAR)
        slide = int(60 * (1 - ease_out_back(u / 0.35)))
        y = CARD_Y + slide
        img.paste(frame, (CARD_X, y), self.mask)
        d.rounded_rectangle([CARD_X, y, CARD_X + CARD_W - 1, y + CARD_H - 1], radius=34,
                            outline=self.theme.accent, width=5)
        credit = self.credits[i]
        if credit:
            f = font("Poppins-SemiBold.ttf", 20)
            tw = f.getlength(credit)
            x0, y0 = CARD_X + 24, y + 22
            d.rounded_rectangle([x0 - 12, y0 - 8, x0 + tw + 12, y0 + 28], radius=10, fill=(0, 0, 0))
            d.text((x0, y0), credit, font=f, fill=(225, 225, 225))

    def draw_caption(self, d: ImageDraw.ImageDraw, i: int, t: float, y: int = 1200) -> None:
        chunks = self.chunks[i]
        current = None
        for chunk in chunks:
            if chunk[0].start - 0.05 <= t <= chunk[-1].end + 0.25:
                current = chunk
        if current is None:
            return
        f = font("Poppins-ExtraBold.ttf", 68)
        texts = [clean_text(w.text).upper() for w in current]
        space = f.getlength(" ") + 18  # extra room for the 9px outline on both words
        total = sum(f.getlength(x) for x in texts) + space * (len(texts) - 1)
        x = (W - total) / 2
        spoken = [w for w in current if w.start <= t]
        active_word = spoken[-1] if spoken else None  # exactly one highlighted word
        for word, text in zip(current, texts):
            active = word is active_word
            color = self.theme.accent if active else WHITE
            lift = -6 if active else 0
            d.text((x, y + lift), text, font=f, fill=color, stroke_width=9, stroke_fill=DARK)
            x += f.getlength(text) + space
