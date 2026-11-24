"""AI-generated scene artwork via Cloudflare Workers AI (free plan: 10,000 Neurons/day).

On the Workers Free plan, requests beyond the daily allocation FAIL instead of being
billed, so this can never cost money. Env vars (GitHub Secrets in CI):
  CF_ACCOUNT_ID, CF_API_TOKEN   (token template "Workers AI": Read + Edit)
  CF_IMAGE_MODEL                optional, default below

Approx cost per 768x1344 portrait (Cloudflare pricing page, 2026-11):
  flux-2-klein-9b  ~1,370 Neurons  (best quality that fits ~7 images/day)
  flux-2-klein-4b  ~100-160 Neurons
  flux-1-schnell   ~60 Neurons, square only (no width/height)
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

API = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
DEFAULT_MODEL = "@cf/black-forest-labs/flux-2-klein-9b"
FALLBACK_MODELS = ["@cf/black-forest-labs/flux-2-klein-4b", "@cf/black-forest-labs/flux-1-schnell"]
MULTIPART_MODELS = {"flux-2-dev", "flux-2-klein-4b", "flux-2-klein-9b"}
PORTRAIT = (768, 1344)  # 9:16-ish, multiples of 64

STYLE = (
    "high quality anime key visual illustration, cel shading, vibrant saturated colors, "
    "dramatic cinematic lighting, dynamic composition, vertical 9:16 poster framing, "
    "detailed background, no text, no letters, no watermark, no logo, no speech bubbles"
)


class ImageGenError(RuntimeError):
    pass


class QuotaExceeded(ImageGenError):
    """Daily free Neuron allocation used up (requests are blocked, not billed)."""


def build_prompt(scene_prompt: str, anime: str) -> str:
    return f"{scene_prompt.strip().rstrip('.')}. Setting and art style inspired by the anime {anime}. {STYLE}"


def _multipart(fields: Dict[str, str]) -> Tuple[bytes, str]:
    boundary = f"----animefacts{uuid.uuid4().hex}"
    parts = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class CloudflareImageGenerator:
    def __init__(self, account_id: str, api_token: str, model: str = DEFAULT_MODEL,
                 opener: Callable = urllib.request.urlopen, sleep: Callable[[float], None] = time.sleep):
        if not account_id or not api_token:
            raise ValueError("CF_ACCOUNT_ID and CF_API_TOKEN are required")
        self._account = account_id
        self._token = api_token
        self.model = model
        self._opener = opener
        self._sleep = sleep

    @classmethod
    def from_env(cls) -> Optional["CloudflareImageGenerator"]:
        account, token = os.environ.get("CF_ACCOUNT_ID", ""), os.environ.get("CF_API_TOKEN", "")
        if not account or not token:
            return None
        return cls(account, token, os.environ.get("CF_IMAGE_MODEL") or DEFAULT_MODEL)

    def _request(self, model: str, prompt: str, size: Tuple[int, int], seed: int) -> bytes:
        short = model.rsplit("/", 1)[-1]
        url = API.format(account=self._account, model=model)
        if short in MULTIPART_MODELS:
            body, ctype = _multipart({"prompt": prompt, "width": str(size[0]), "height": str(size[1]),
                                      "seed": str(seed)})
        else:  # flux-1-schnell: JSON, square output, prompt + steps only
            body, ctype = json.dumps({"prompt": prompt[:2048], "steps": 8}).encode(), "application/json"
        request = urllib.request.Request(url, data=body, method="POST")
        request.add_header("Authorization", f"Bearer {self._token}")
        request.add_header("Content-Type", ctype)

        for attempt in range(1, 4):
            try:
                with self._opener(request, timeout=180) as response:
                    raw = response.read()
                    if response.headers.get("Content-Type", "").startswith("image/"):
                        return raw
                    payload = json.loads(raw.decode("utf-8"))
                    image = (payload.get("result") or {}).get("image")
                    if not payload.get("success", True) or not image:
                        raise ImageGenError(f"{short}: no image in response ({str(payload.get('errors'))[:300]})")
                    return base64.b64decode(image)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                low = detail.lower()
                if exc.code == 429 and ("neuron" in low or "daily" in low or "allocation" in low or "limit" in low):
                    raise QuotaExceeded(f"{short}: daily free allocation exhausted") from None
                if exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                    self._sleep(10 * attempt)
                    continue
                raise ImageGenError(f"{short}: HTTP {exc.code}: {detail}") from None
            except urllib.error.URLError as exc:
                if attempt < 3:
                    self._sleep(5 * attempt)
                    continue
                raise ImageGenError(f"{short}: network error {type(exc).__name__}") from None
        raise ImageGenError("unreachable")

    def generate(self, prompt: str, cache_dir: Path, size: Tuple[int, int] = PORTRAIT, seed: int = 7) -> Tuple[Path, str]:
        """Return (path to JPEG, model used). Cached by model+prompt+size+seed."""
        from io import BytesIO

        from PIL import Image

        errors: List[str] = []
        for model in [self.model] + [m for m in FALLBACK_MODELS if m != self.model]:
            key = hashlib.sha1(f"{model}|{prompt}|{size}|{seed}".encode()).hexdigest()[:16]
            path = cache_dir / f"ai_{key}.jpg"
            if path.exists():
                return path, model
            try:
                raw = self._request(model, prompt, size, seed)
                with Image.open(BytesIO(raw)) as im:
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    im.convert("RGB").save(path, quality=93)
                return path, model
            except QuotaExceeded:
                raise
            except ImageGenError as exc:
                errors.append(str(exc))
                continue
        raise ImageGenError("all models failed: " + " | ".join(errors))
