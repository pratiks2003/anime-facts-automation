"""Download the Kokoro TTS model files (MIT/Apache-2.0) with checksum verification.

  python -m video.fetch_models
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request

from .tts import MODEL_DIR

BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
FILES = {
    "kokoro-v1.0.onnx": "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for name, expected in FILES.items():
        path = MODEL_DIR / name
        if path.exists() and sha256(path) == expected:
            print(f"ok (cached) {name}")
            continue
        print(f"downloading {name} ...", flush=True)
        tmp = path.with_suffix(path.suffix + ".part")
        urllib.request.urlretrieve(f"{BASE}/{name}", tmp)
        actual = sha256(tmp)
        if actual != expected:
            tmp.unlink()
            print(f"checksum mismatch for {name}: {actual}", file=sys.stderr)
            return 1
        tmp.replace(path)
        print(f"ok {name}")
    from .image_check import fetch_clip  # CLIP for the Image Inspector (also checksum verified)

    if not fetch_clip():
        print("CLIP download failed; the inspector will run technical checks only", file=sys.stderr)
    else:
        print("ok clip")
    return 0


if __name__ == "__main__":
    sys.exit(main())
