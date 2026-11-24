"""Text generation via Cloudflare Workers AI's OpenAI-compatible endpoint (free plan).

GitHub Models was retired on 2026-07-30, so script writing runs on the same free
Cloudflare allocation as the artwork. gpt-oss-120b costs ~$0.35/M input and
$0.75/M output tokens (~230 Neurons per script), far inside 10,000 Neurons/day.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, List, Optional

ENDPOINT = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1/chat/completions"
DEFAULT_MODEL = "@cf/openai/gpt-oss-120b"
FALLBACK_MODELS = ["@cf/meta/llama-4-scout-17b-16e-instruct", "@cf/mistralai/mistral-small-3.1-24b-instruct"]


class LLMError(RuntimeError):
    pass


def extract_json(text: str) -> dict:
    """Parse a JSON object from a model reply (tolerates ```json fences / stray prose)."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise LLMError("model reply contained no JSON object")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"model reply was not valid JSON: {exc}") from None


class LLM:
    def __init__(self, account_id: str, api_token: str, model: str = DEFAULT_MODEL,
                 opener: Callable = urllib.request.urlopen, sleep: Callable[[float], None] = time.sleep):
        if not account_id or not api_token:
            raise ValueError("CF_ACCOUNT_ID and CF_API_TOKEN are required for the LLM")
        self._url = ENDPOINT.format(account=account_id)
        self._token = api_token
        self.model = model
        self._opener = opener
        self._sleep = sleep
        self.neurons_used = 0.0

    @classmethod
    def from_env(cls) -> "LLM":
        return cls(os.environ.get("CF_ACCOUNT_ID", ""), os.environ.get("CF_API_TOKEN", ""),
                   os.environ.get("CF_TEXT_MODEL") or DEFAULT_MODEL)

    def _call(self, model: str, messages: List[Dict[str, str]], max_tokens: int, temperature: float) -> str:
        body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
                "response_format": {"type": "json_object"}}
        req = urllib.request.Request(self._url, data=json.dumps(body).encode(), method="POST")
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "anime-facts-automation/0.1")
        for attempt in range(1, 4):
            try:
                with self._opener(req, timeout=180) as resp:
                    data = json.loads(resp.read().decode())
                usage = data.get("usage") or {}
                self.neurons_used += float(usage.get("neurons") or 0)
                content = data["choices"][0]["message"].get("content") or ""
                if not content.strip():
                    raise LLMError(f"{model}: empty reply")
                return content
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")[:300]
                if exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                    self._sleep(8 * attempt)
                    continue
                raise LLMError(f"{model}: HTTP {exc.code} {detail}") from None
            except urllib.error.URLError as exc:
                if attempt < 3:
                    self._sleep(5 * attempt)
                    continue
                raise LLMError(f"{model}: network error {type(exc).__name__}") from None
        raise LLMError("unreachable")

    def json(self, system: str, user: str, max_tokens: int = 3000, temperature: float = 0.8) -> dict:
        """Ask for a JSON object; falls back to other models on errors or unparseable replies."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        errors = []
        for model in [self.model] + [m for m in FALLBACK_MODELS if m != self.model]:
            try:
                return extract_json(self._call(model, messages, max_tokens, temperature))
            except LLMError as exc:
                errors.append(str(exc))
        raise LLMError("all text models failed: " + " | ".join(errors))
