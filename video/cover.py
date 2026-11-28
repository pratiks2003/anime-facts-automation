"""Reel cover designer: the picture people see on the profile grid and in feeds before playing.

Candidates are built from the Reel's own best pictures (hook subject first): the full-bleed image,
a dark gradient for contrast and a big 2-4 word title. Everything important sits inside the
centre 3:4 area, because the profile grid crops covers to 3:4. The quality checker (Gemini) picks
the most eye-catching, readable candidate and may tighten the title; without it the first
candidate (the hook's own picture) is used.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Optional

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .visuals import H, W, clean_text, cover as cover_fit, font, headline_image, theme_for

GRID_TOP = (H - int(W * 4 / 3)) // 2  # the 1080x1440 band the profile grid shows
GRID_BOTTOM = GRID_TOP + int(W * 4 / 3)


def log(msg: str) -> None:
    print(f"[cover] {msg}", file=sys.stderr, flush=True)


def cover_title(script: dict) -> str:
    """Short punchy title: the hook headline, else the script title (max 5 words)."""
    scenes = script.get("scenes") or [{}]
    text = scenes[0].get("headline") or script.get("title") or script.get("anime", "")
    words = clean_text(text).split()
    return " ".join(words[:5])


def design(image_path: str, title: str, script: dict, handle: str = "") -> Image.Image:
    theme = theme_for(script.get("anime", ""))
    img = ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")
    ratio = img.width / img.height
    if ratio > 0.8:  # wide picture: show it big over a blurred copy instead of cropping the subject away
        canvas = cover_fit(img, (W // 8, H // 8)).filter(ImageFilter.GaussianBlur(4)).resize((W, H), Image.BILINEAR)
        target_w = W
        target_h = int(W / ratio)
        if target_h < int(H * 0.45):
            target_h = int(H * 0.45)
            target_w = int(target_h * ratio)
        sharp = img.resize((target_w, target_h), Image.LANCZOS)
        left = (target_w - W) // 2 if target_w > W else 0
        sharp = sharp.crop((left, 0, left + min(W, target_w), target_h))
        canvas.paste(sharp, ((W - sharp.width) // 2, GRID_TOP + 120))
    else:
        # compose for the 3:4 grid band, anchored near the top (faces), then extend to 9:16 with a blurred copy
        band = (W, GRID_BOTTOM - GRID_TOP)
        scale = max(band[0] / img.width, band[1] / img.height)
        resized = img.resize((int(img.width * scale + 0.5), int(img.height * scale + 0.5)), Image.LANCZOS)
        left = (resized.width - band[0]) // 2
        top = int((resized.height - band[1]) * 0.12)
        framed = resized.crop((left, top, left + band[0], top + band[1]))
        canvas = cover_fit(img, (W // 8, H // 8)).filter(ImageFilter.GaussianBlur(4)).resize((W, H), Image.BILINEAR)
        canvas.paste(framed, (0, GRID_TOP))
    # bottom gradient inside the grid band so the title always reads
    shade = Image.new("L", (1, 256))
    for y in range(256):
        shade.putpixel((0, y), int(230 * (y / 255) ** 1.6))
    band_h = 820
    mask = shade.resize((W, band_h))
    canvas.paste((0, 0, 0), (0, GRID_BOTTOM - band_h + 140), mask)
    title_img = headline_image(title, [w for w in title.split() if len(w) > 3][-1:], theme, max_width=960,
                               sizes=(190, 170, 150, 132, 116), max_lines=3)
    y = GRID_BOTTOM - title_img.height - 70
    canvas.paste(title_img, ((W - title_img.width) // 2, y), title_img)
    d = ImageDraw.Draw(canvas)
    if handle:
        d.text((W // 2, GRID_BOTTOM - 40), handle, font=font("Poppins-Bold.ttf", 30), fill=(255, 255, 255),
               anchor="mm", stroke_width=2, stroke_fill=(0, 0, 0))
    return canvas


def grid_view(img: Image.Image) -> Image.Image:
    """What the profile grid shows (centre 3:4)."""
    return img.crop((0, GRID_TOP, W, GRID_BOTTOM))


def candidates(renderer, script: dict, limit: int = 4) -> List[str]:
    """Best still pictures of the Reel, hook first, distinct, no video clips."""
    out: List[str] = []
    for shot in renderer.shots:
        src = getattr(shot, "source", None)
        if src and not Path(src).is_dir() and src not in out:
            out.append(src)
        if len(out) >= limit:
            break
    return out


COVER_PROMPT = """You are the cover designer of "Senpai Secrets" - the person top creators hire because their covers get the
tap. You know the profile grid is the shop window: one clear, iconic image and a 2-4 word hook beat everything. Channel: {anime}.
Below are candidate Reel covers exactly as they will look on the profile grid. Pick the one most likely to make a fan stop
and tap: a clear, recognisable character or iconic moment, strong contrast, title readable at small size, nothing ugly
(blurry, cut-off face, clutter, wrong show). You may rewrite the title (max 4 punchy words, curiosity, no clickbait lies).
Current title: "{title}". The Reel is about: {topic}

Return ONLY JSON: {{"pick": candidate_number, "title": "better title or the same", "why": str}}"""


def make_cover(renderer, script: dict, out: Path, gemini=None, handle: str = "") -> Optional[dict]:
    """Writes out/cover.jpg; returns {"source", "title", "why"} or None if there is no usable picture."""
    from .visual_qa import jpeg_part

    pics = candidates(renderer, script)
    if not pics:
        return None
    title = cover_title(script)
    designs = []
    for p in pics:
        try:
            designs.append((p, design(p, title, script, handle)))
        except Exception as exc:
            log(f"skipped {Path(p).name} ({exc})")
    if not designs:
        return None
    choice, why = 0, "hook picture (no cover editor available)"
    if gemini is not None and len(designs) > 1:
        topic = " ".join(sc.get("narration", "") for sc in script.get("scenes", [])[:2])[:300]
        parts = [{"text": COVER_PROMPT.format(anime=script.get("anime", ""), title=title, topic=topic)}]
        for i, (_, img) in enumerate(designs):
            parts.append({"text": f"Candidate {i + 1}"})
            parts.append(jpeg_part(grid_view(img), width=360))
        result = gemini.json(parts)
        if result:
            try:
                choice = max(0, min(len(designs) - 1, int(result.get("pick", 1)) - 1))
            except (TypeError, ValueError):
                choice = 0
            new_title = clean_text(str(result.get("title") or "")).strip()
            if new_title and len(new_title.split()) <= 5 and new_title.lower() != title.lower() \
                    and re.search(r"[A-Za-z]", new_title):
                title = new_title
                designs[choice] = (designs[choice][0], design(designs[choice][0], title, script, handle))
            why = str(result.get("why") or "")[:200]
    source, img = designs[choice]
    img.save(out / "cover.jpg", quality=92)
    log(f"cover: {Path(source).name} - \"{title}\" ({why})")
    return {"source": source, "title": title, "why": why}
