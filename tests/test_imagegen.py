import base64
import io
import json
import tempfile
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path

from PIL import Image

from video.imagegen import CloudflareImageGenerator, ImageGenError, QuotaExceeded, build_prompt


def png_b64():
    buf = BytesIO()
    Image.new("RGB", (768, 1344), (200, 50, 50)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


class Resp(io.BytesIO):
    headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, body):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(body.encode()))


class FakeCF:
    def __init__(self, outcomes):
        self.outcomes, self.requests = list(outcomes), []

    def __call__(self, request, timeout):
        self.requests.append(request)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return Resp(json.dumps(out).encode())


def gen(fake, model="@cf/black-forest-labs/flux-2-klein-9b"):
    return CloudflareImageGenerator("acct", "SECRET", model, opener=fake, sleep=lambda s: None)


class ImageGenTests(unittest.TestCase):
    def test_multipart_request_and_cache(self):
        fake = FakeCF([{"result": {"image": png_b64()}, "success": True}])
        with tempfile.TemporaryDirectory() as tmp:
            path, model = gen(fake).generate("a pirate", Path(tmp))
            self.assertTrue(path.exists())
            self.assertEqual(model, "@cf/black-forest-labs/flux-2-klein-9b")
            again, _ = gen(FakeCF([])).generate("a pirate", Path(tmp))  # served from cache, no request
            self.assertEqual(again, path)
        req = fake.requests[0]
        self.assertTrue(req.full_url.endswith("/accounts/acct/ai/run/@cf/black-forest-labs/flux-2-klein-9b"))
        self.assertEqual(req.get_header("Authorization"), "Bearer SECRET")
        self.assertTrue(req.get_header("Content-type").startswith("multipart/form-data; boundary="))
        body = req.data.decode()
        self.assertIn('name="width"\r\n\r\n768', body)
        self.assertIn('name="height"\r\n\r\n1344', body)

    def test_schnell_uses_json(self):
        fake = FakeCF([{"result": {"image": png_b64()}, "success": True}])
        with tempfile.TemporaryDirectory() as tmp:
            gen(fake, "@cf/black-forest-labs/flux-1-schnell").generate("x", Path(tmp))
        self.assertEqual(json.loads(fake.requests[0].data)["steps"], 8)

    def test_falls_back_to_next_model(self):
        fake = FakeCF([http_error(400, '{"errors":[{"message":"bad input"}]}'),
                       {"result": {"image": png_b64()}, "success": True}])
        with tempfile.TemporaryDirectory() as tmp:
            _, model = gen(fake).generate("x", Path(tmp))
        self.assertEqual(model, "@cf/black-forest-labs/flux-2-klein-4b")

    def test_character_scenes_use_schnell_first(self):
        fake = FakeCF([{"result": {"image": png_b64()}, "success": True}])
        with tempfile.TemporaryDirectory() as tmp:
            _, model = gen(fake).generate("x", Path(tmp), character=True)
        self.assertEqual(model, "@cf/black-forest-labs/flux-1-schnell")

    def test_flagged_scene_falls_back(self):
        flagged = http_error(400, '{"errors":[{"message":"AiError: Your output has been flagged","code":3030}]}')
        fake = FakeCF([flagged, flagged, {"result": {"image": png_b64()}, "success": True}])
        with tempfile.TemporaryDirectory() as tmp:
            _, model = gen(fake).generate("x", Path(tmp))
        self.assertEqual(model, "@cf/black-forest-labs/flux-1-schnell")

    def test_quota_exceeded_stops_without_fallback(self):
        fake = FakeCF([http_error(429, '{"errors":[{"message":"you have used up your daily free allocation of 10,000 neurons"}]}')])
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(QuotaExceeded):
            gen(fake).generate("x", Path(tmp))
        self.assertEqual(len(fake.requests), 1)

    def test_all_models_fail(self):
        fake = FakeCF([http_error(400, "nope")] * 3)
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ImageGenError):
            gen(fake).generate("x", Path(tmp))

    def test_requires_credentials_and_prompt_style(self):
        with self.assertRaises(ValueError):
            CloudflareImageGenerator("", "")
        prompt = build_prompt("a pirate on a ship.", "One Piece")
        self.assertIn("inspired by the anime One Piece", prompt)
        self.assertIn("no text", prompt)


if __name__ == "__main__":
    unittest.main()
