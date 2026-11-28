"""One-time YouTube login for the Shorts uploader. Run on your Mac:

  python3 tools/youtube_auth.py ~/Downloads/client_secret_XXXX.json

Opens Google sign-in in your browser (choose the account that owns the channel; on "Google hasn't verified this app"
click Advanced -> Go to Senpai Secrets Uploader), then stores YT_CLIENT_ID, YT_CLIENT_SECRET and YT_REFRESH_TOKEN as
GitHub secrets with the gh CLI. No secret is ever printed. Only the youtube.upload permission is requested.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

REPO = "pratiks2003/anime-facts-automation"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"


def main(path: str) -> int:
    cfg = json.load(open(path))
    cfg = cfg.get("installed") or cfg.get("web") or {}
    client_id, client_secret = cfg["client_id"], cfg["client_secret"]
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] == state and "code" in q:
                result["code"] = q["code"][0]
                msg = "Signed in. You can close this tab and go back to the terminal."
            else:
                result["error"] = q.get("error", ["unexpected response"])[0]
                msg = "Sign-in failed: " + result["error"]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(msg.encode())

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}"
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    threading.Thread(target=server.handle_request, daemon=True).start()
    print("Opening Google sign-in in your browser...")
    webbrowser.open(url)
    print("If it did not open, copy this link into your browser:\n" + url)
    while not result:
        threading.Event().wait(0.5)
    if "code" not in result:
        print("Sign-in failed:", result.get("error"))
        return 1
    body = urllib.parse.urlencode({"code": result["code"], "client_id": client_id, "client_secret": client_secret,
                                   "redirect_uri": redirect, "grant_type": "authorization_code",
                                   "code_verifier": verifier}).encode()
    with urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=body), timeout=30) as r:
        tokens = json.loads(r.read().decode())
    refresh = tokens.get("refresh_token")
    if not refresh:
        print("Google returned no refresh token. Remove the app at myaccount.google.com/permissions and run this again.")
        return 1
    for name, value in (("YT_CLIENT_ID", client_id), ("YT_CLIENT_SECRET", client_secret), ("YT_REFRESH_TOKEN", refresh)):
        done = subprocess.run(["gh", "secret", "set", name, "-R", REPO], input=value, text=True, capture_output=True)
        print(f"{name}: {'saved to GitHub' if done.returncode == 0 else 'FAILED - ' + done.stderr.strip()[:200]}")
    print("Done. YouTube Shorts uploads switch on automatically with the next Reel.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
