"""Cinematic renderer (style layout "cinematic"): full-bleed shots that change every few seconds,
constant motion, grading, glitch/flash transitions and one-word captions.

Every visual choice comes from the Style Spec (video/style.py); this module only executes it.
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from . import motion
from .clips import clip_frames, is_clip
from .tts import Narration, Word
from .visuals import H, W, base_background, clean_text, cover, font, pill, theme_for

OVERSCAN = 1.16
STOPWORDS = {"the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at", "for", "is", "it", "its", "this", "that",
             "with", "as", "by", "be", "are", "was", "were", "from", "his", "her", "their", "they", "he", "she", "you",
             "your", "we", "our", "i", "my", "me", "so", "if", "not", "do", "does", "did", "just", "than", "then"}


@dataclass
class Shot:
    start: float
    end: float
    image: Optional[Image.Image]  # graded, overscanned canvas (None for clips)
    zoom_in: bool
    pan: Tuple[float, float]      # direction of drift (-1..1)
    clip: Optional[List[str]] = None  # 30 fps frames of a video clip (already 1080x1920 + graded)
    layers: Optional[Tuple[Image.Image, Image.Image]] = None  # 2.5D parallax: (background, subject RGBA)


def grade(img: Image.Image, mode: str) -> Image.Image:
    if mode == "dark":
        img = ImageEnhance.Contrast(img).enhance(1.18)
        img = ImageEnhance.Brightness(img).enhance(0.78)
        img = ImageEnhance.Color(img).enhance(1.1)
    elif mode == "vivid":
        img = ImageEnhance.Color(img).enhance(1.35)
        img = ImageEnhance.Contrast(img).enhance(1.12)
    if mode != "none":  # vignette keeps the eye centred and captions readable
        w, h = img.size
        vignette = Image.new("L", (w // 8, h // 8), 0)
        ImageDraw.Draw(vignette).ellipse([-w // 40, -h // 40, w // 8 + w // 40, h // 8 + h // 40], fill=255)
        vignette = vignette.filter(ImageFilter.GaussianBlur(w // 30)).resize((w, h))
        img = Image.composite(img, Image.new("RGB", (w, h), (0, 0, 0)), vignette.point(lambda v: 90 + v * 165 // 255))
    return img


def prepare(img: Image.Image, mode: str) -> Image.Image:
    """Fill a 9:16 overscanned canvas. Big portrait art is covered; wide/square art - and any picture too small
    to fill the frame sharply - sits on a blurred copy of itself instead of being blown up."""
    size = (int(W * OVERSCAN), int(H * OVERSCAN))
    ratio = img.width / img.height
    scale_needed = max(size[0] / img.width, size[1] / img.height)
    if ratio <= 0.75 and scale_needed > 1.6:  # small portrait (e.g. 460x690 poster): framed, at most 2x
        canvas = cover(img, (size[0] // 8, size[1] // 8)).filter(ImageFilter.GaussianBlur(4)).resize(size, Image.BILINEAR)
        canvas = ImageEnhance.Brightness(canvas).enhance(0.5)
        k = min(2.0, size[0] * 0.86 / img.width, size[1] * 0.7 / img.height)
        sharp = img.resize((int(img.width * k), int(img.height * k)), Image.LANCZOS)
        canvas.paste(sharp, ((size[0] - sharp.width) // 2, (size[1] - sharp.height) // 2))
        return grade(canvas, mode)
    if ratio > 0.75:  # landscape or square: show it whole over a blurred, darkened extension
        canvas = cover(img, (size[0] // 8, size[1] // 8)).filter(ImageFilter.GaussianBlur(4)).resize(size, Image.BILINEAR)
        canvas = ImageEnhance.Brightness(canvas).enhance(0.55)
        # wide shots are shown big (sides cropped a little) so the subject fills the frame
        target_h = int(size[1] * (0.62 if ratio < 1.25 else 0.56))
        target_w = int(target_h * ratio)
        sharp = img.resize((target_w, target_h), Image.LANCZOS)
        if target_w > size[0]:
            cut = (target_w - size[0]) // 2
            sharp = sharp.crop((cut, 0, cut + size[0], target_h))
            target_w = size[0]
        canvas.paste(sharp, ((size[0] - target_w) // 2, (size[1] - target_h) // 2))
    else:
        canvas = cover(img, size)
    return grade(canvas, mode)


def is_keyword(word: str, index: int, emphasis: set) -> bool:
    bare = re.sub(r"[^\w'-]", "", word)
    if not bare:
        return False
    if bare.upper() in emphasis:
        return True
    if re.search(r"\d", bare):
        return True  # numbers stick
    return index > 0 and bare[0].isupper() and bare.lower() not in STOPWORDS  # proper nouns


class CinematicRenderer:
    def __init__(self, script: dict, narration: Narration, style: dict, scene_images: List[List[str]],
                 handle: str = "", beats: Optional[List[float]] = None):
        """scene_images: for every scene, the ordered list of image paths for its shots."""
        self.script, self.narration, self.style = script, narration, style
        self.theme = theme_for(script["anime"])
        self.duration = narration.duration
        self.handle = handle
        self.rng = random.Random(script.get("id", "reel"))
        self.fallback = base_background(self.theme)
        self.shots: List[Shot] = []
        self._clip_cache: Tuple[Optional[str], Optional[Image.Image]] = (None, None)
        cache: Dict[str, Image.Image] = {}
        layer_cache: Dict[str, Optional[Tuple[Image.Image, Image.Image]]] = {}
        interval = style["image_interval"]
        for scene, paths in zip(narration.scenes, scene_images):
            length = max(0.5, scene.end - scene.start)
            count = max(1, round(length / interval))
            if paths:
                count = min(count, len(paths))  # one shot per distinct picture: fewer, longer shots beat repeats
            for k in range(count):
                path = paths[k] if paths else None
                if path and is_clip(path):
                    frames = [str(f) for f in clip_frames(path)]
                    if frames:
                        start = scene.start + length * k / count
                        end = scene.start + length * (k + 1) / count
                        self.shots.append(Shot(start, end, None, True, (0.0, 0.0), clip=frames))
                        continue
                    path = None
                key = f"{path}|{style['grading']}"
                if path and key not in cache:
                    try:
                        cache[key] = prepare(ImageOps.exif_transpose(Image.open(path)).convert("RGB"), style["grading"])
                    except Exception:
                        path = None
                img = cache.get(key) if path else prepare(self.fallback, style["grading"])
                layers = None
                if path and style.get("parallax", 0) > 0:
                    if key not in layer_cache:
                        depth = motion.Depth.get()
                        layer_cache[key] = motion.split_layers(img, depth(img)) if depth else None
                    layers = layer_cache[key]
                start = scene.start + length * k / count
                end = scene.start + length * (k + 1) / count
                self.shots.append(Shot(start, end, img, self.rng.random() < 0.6,
                                       (self.rng.uniform(-1, 1), self.rng.uniform(-0.6, 0.6)), layers=layers))
        if beats and len(self.shots) > 1:  # land the cuts on the music's beat
            from .audio_library import snap_to_beats

            bounds = snap_to_beats([sh.start for sh in self.shots[1:]], beats)
            for k, t in enumerate(bounds, start=1):
                t = min(t, self.shots[k].end - 0.4, self.duration - 0.4)
                self.shots[k].start = t
                self.shots[k - 1].end = t
        self.words: List[Tuple[Word, bool]] = []
        for scene, spec in zip(narration.scenes, script["scenes"]):
            emphasis = {clean_text(e).upper().strip(".,!?") for e in spec.get("emphasis", [])}
            for i, w in enumerate(scene.words):
                self.words.append((w, style["keyword_color"] and is_keyword(w.text, i, emphasis)))
        self.font = font("Anton-Regular.ttf", int(style["caption_size"]))
        self.punch_beats = (beats or [])[::2] if style.get("beat_punch", 0) < 0.8 else list(beats or [])
        self.shake_times = sorted({round(w.start, 2) for w, key in self.words if key} |
                                  {round(sc.start, 2) for sc in narration.scenes[1:]})
        self.line_times = [w.start for w, _ in self.words if re.sub(r"[^a-z]", "", w.text.lower()) in motion.ACTION_WORDS]
        self.lines_img = motion.speed_lines((W, H), seed=len(self.words)) if style.get("speed_lines") else None

    # -------------------------------------------------------------- frame

    def shot_at(self, t: float) -> Tuple[int, Shot]:
        for i, s in enumerate(self.shots):
            if t < s.end:
                return i, s
        return len(self.shots) - 1, self.shots[-1]

    def frame(self, t: float, captions: bool = True) -> Image.Image:
        i, shot = self.shot_at(t)
        u = t - shot.start
        progress = min(1.0, max(0.0, u / max(0.1, shot.end - shot.start)))
        m = self.style["motion"]
        if shot.clip:
            img = self.clip_frame(shot, u, progress)
            return self.finish(img, t, u, i, captions)
        big = shot.image
        span = 1.0 + (OVERSCAN - 1.0) * m * (1 - progress if shot.zoom_in else progress)
        cw, ch = int(W * span), int(H * span)
        cw, ch = min(cw, big.width), min(ch, big.height)
        free_x, free_y = big.width - cw, big.height - ch
        px = 0.5 + 0.45 * m * shot.pan[0] * (progress - 0.5)
        py = 0.5 + 0.45 * m * shot.pan[1] * (progress - 0.5)
        left, top = int(free_x * min(1, max(0, px))), int(free_y * min(1, max(0, py)))
        if shot.layers is None:
            img = big.crop((left, top, left + cw, top + ch)).resize((W, H), Image.BILINEAR)
            return self.finish(img, t, u, i, captions)
        # 2.5D: background follows the camera; the subject drifts further and grows a little
        bg, fg = shot.layers
        img = bg.crop((left, top, left + cw, top + ch)).resize((W, H), Image.BILINEAR)
        par = self.style["parallax"]
        grow = 1.0 + 0.03 * par * (progress if shot.zoom_in else 1 - progress)
        fw, fh = int(cw / grow), int(ch / grow)
        dx = int(0.035 * par * cw * (progress - 0.5) * (1 if shot.pan[0] >= 0 else -1))
        fl, ft = left + (cw - fw) // 2 - dx, top + (ch - fh) // 2
        layer = fg.crop((fl, ft, fl + fw, ft + fh)).resize((W, H), Image.BILINEAR)
        img.paste(layer, (0, 0), layer)
        return self.finish(img, t, u, i, captions)

    def clip_frame(self, shot: Shot, u: float, progress: float) -> Image.Image:
        """Play the clip at its own speed; hold the last frame if the shot outlasts it. Gentle push-in."""
        index = min(len(shot.clip) - 1, int(u * 30))
        path = shot.clip[index]
        if self._clip_cache[0] != path:
            self._clip_cache = (path, Image.open(path).convert("RGB"))
        img = self._clip_cache[1]
        zoom = 1.0 + 0.06 * self.style["motion"] * progress
        if zoom > 1.001:
            cw, ch = int(W / zoom), int(H / zoom)
            left, top = (W - cw) // 2, (H - ch) // 2
            img = img.crop((left, top, left + cw, top + ch)).resize((W, H), Image.BILINEAR)
        else:
            img = img.copy()
        return img

    def effects(self, img: Image.Image, t: float) -> Image.Image:
        """Beat punch + shake (one crop) and speed lines."""
        zoom, ox, oy = 1.0, 0.0, 0.0
        strength = self.style.get("beat_punch", 0)
        if strength and self.punch_beats:
            b = max((x for x in self.punch_beats if x <= t), default=None)
            if b is not None and t - b < 0.2:
                zoom += 0.055 * strength * (1 - motion.ease((t - b) / 0.2))
        amp = 18 * self.style.get("shake", 0)
        if amp:
            hit = max((x for x in self.shake_times if x <= t), default=None)
            if hit is not None and t - hit < 0.25:
                fade = 1 - (t - hit) / 0.25
                ox, oy = amp * fade * math.sin(t * 90), amp * fade * math.cos(t * 70)
                zoom = max(zoom, 1.03)
        if zoom > 1.001:
            cw, ch = W / zoom, H / zoom
            left = min(W - cw, max(0.0, (W - cw) / 2 + ox))
            top = min(H - ch, max(0.0, (H - ch) / 2 + oy))
            img = img.crop((int(left), int(top), int(left + cw), int(top + ch))).resize((W, H), Image.BILINEAR)
        if self.style.get("speed_lines") and self.line_times:
            hit = max((x for x in self.line_times if x <= t), default=None)
            if hit is not None and t - hit < 0.4:
                lines = self.lines_img.copy()
                lines.putalpha(lines.getchannel("A").point(lambda v, f=0.75 * (1 - (t - hit) / 0.4): int(v * f)))
                img.paste(lines, (0, 0), lines)
        return img

    def finish(self, img: Image.Image, t: float, u: float, i: int, captions: bool = True) -> Image.Image:
        img = self.effects(img, t)
        img = self.transition(img, u, i)
        d = ImageDraw.Draw(img)
        if self.style["watermark"]:
            d.text((W // 2, 1395), self.style["watermark"], font=font("Poppins-Bold.ttf", 26),
                   fill=(255, 255, 255), anchor="mm", stroke_width=2, stroke_fill=(0, 0, 0))
        if captions and self.style["caption_mode"] != "none":
            self.caption(img, t)
        last = self.narration.scenes[-1]
        if self.style["outro_cta"] and self.handle and t >= last.start + 0.4:
            pill(d, W // 2, int(H * self.style["caption_y"]) + 190, f"FOLLOW {self.handle}", self.theme.accent2, (255, 255, 255), size=32)
        return img

    def transition(self, img: Image.Image, u: float, index: int) -> Image.Image:
        kind = self.style["transition"]
        if index == 0 and u < 0.12:
            return Image.blend(img, Image.new("RGB", (W, H), (255, 255, 255)), 0.6 * (1 - u / 0.12))
        if kind == "cut" or u >= 0.12:
            return img
        strength = 1 - u / 0.12
        if kind == "flash":
            return Image.blend(img, Image.new("RGB", (W, H), (255, 255, 255)), 0.5 * strength)
        # glitch: RGB split + a few shifted slices
        r, g, b = img.split()
        shift = int(28 * strength)
        img = Image.merge("RGB", (ImageChops.offset(r, shift, 0), g, ImageChops.offset(b, -shift, 0)))
        rng = random.Random(index * 97 + int(u * 1000))
        for _ in range(4):
            y0 = rng.randint(0, H - 80)
            hgt = rng.randint(20, 80)
            band = img.crop((0, y0, W, y0 + hgt))
            img.paste(ImageChops.offset(band, rng.randint(-60, 60), 0), (0, y0))
        return img

    def caption(self, img: Image.Image, t: float) -> None:
        current = None
        for idx, (w, key) in enumerate(self.words):
            if w.start - 0.04 <= t <= w.end + 0.12:
                current = (w, key, idx)
        if current is None:
            return
        w, key, idx = current
        if self.style["caption_mode"] == "chunk":
            text = " ".join(x.text for x, _ in self.words[max(0, idx - 1): idx + 2])
        else:
            text = w.text
        text = clean_text(text).upper().strip(" ,;:")
        if not text:
            return
        fnt = self.font
        while fnt.getlength(text) > W - 120 and fnt.size > 60:
            fnt = font("Anton-Regular.ttf", fnt.size - 8)
        pop = 1.0 + 0.10 * max(0.0, 1 - (t - w.start) / 0.12)  # tiny pop as the word lands
        if pop > 1.001:
            fnt = font("Anton-Regular.ttf", int(fnt.size * pop))
        y = int(H * self.style["caption_y"])
        color = self.theme.accent if key else (255, 255, 255)
        glow = Image.new("L", (W, H), 0)
        ImageDraw.Draw(glow).text((W // 2, y), text, font=fnt, fill=200, anchor="mm", stroke_width=14, stroke_fill=200)
        img.paste((0, 0, 0), mask=glow.filter(ImageFilter.GaussianBlur(10)))
        ImageDraw.Draw(img).text((W // 2, y), text, font=fnt, fill=color, anchor="mm", stroke_width=6, stroke_fill=(0, 0, 0))
