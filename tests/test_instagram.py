import io
import json
import unittest
import urllib.error
import urllib.parse

from integrations.instagram import ContainerError, InstagramAPIError, InstagramClient

TOKEN = "SECRET-TOKEN-123"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(status, code=None, message="boom"):
    body = json.dumps({"error": {"message": message, "code": code, "fbtrace_id": "T1"}}).encode()
    return urllib.error.HTTPError("https://x", status, "err", {}, io.BytesIO(body))


class FakeGraph:
    """Scripted Graph API: list of (expected path suffix, response dict or exception)."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, request, timeout):
        parsed = urllib.parse.urlparse(request.full_url)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        if request.data:
            params.update(urllib.parse.parse_qsl(request.data.decode()))
        self.calls.append((request.get_method(), parsed.path, params))
        expected, outcome = self.script.pop(0)
        assert parsed.path.endswith(expected), f"expected {expected}, got {parsed.path}"
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(json.dumps(outcome).encode())


def client(graph, user_id="1789"):
    return InstagramClient(TOKEN, user_id=user_id, opener=graph, sleep=lambda s: None)


class InstagramClientTests(unittest.TestCase):
    def test_full_publish_flow(self):
        graph = FakeGraph([
            ("/1789/media", {"id": "C1"}),
            ("/C1", {"status_code": "IN_PROGRESS"}),
            ("/C1", {"status_code": "FINISHED"}),
            ("/1789/media_publish", {"id": "M1"}),
            ("/M1", {"permalink": "https://www.instagram.com/reel/abc/"}),
        ])
        result = client(graph).publish_reel("https://example.org/v.mp4", "Hello #anime", publish=True)
        self.assertTrue(result.published)
        self.assertEqual(result.media_id, "M1")
        self.assertEqual(result.permalink, "https://www.instagram.com/reel/abc/")
        method, path, params = graph.calls[0]
        self.assertEqual((method, path), ("POST", "/v27.0/1789/media"))
        self.assertEqual(params["media_type"], "REELS")
        self.assertEqual(params["share_to_feed"], "true")
        self.assertEqual(graph.calls[3][2]["creation_id"], "C1")

    def test_dry_run_stops_before_publish(self):
        graph = FakeGraph([("/1789/media", {"id": "C1"}), ("/C1", {"status_code": "FINISHED"})])
        result = client(graph).publish_reel("https://example.org/v.mp4", "x")
        self.assertFalse(result.published)
        self.assertIsNone(result.media_id)
        self.assertEqual(len(graph.calls), 2)

    def test_container_error_never_publishes(self):
        graph = FakeGraph([("/1789/media", {"id": "C1"}), ("/C1", {"status_code": "ERROR", "status": "bad codec"})])
        with self.assertRaises(ContainerError) as ctx:
            client(graph).publish_reel("https://example.org/v.mp4", "x", publish=True)
        self.assertIn("bad codec", str(ctx.exception))
        self.assertFalse(any(p.endswith("media_publish") for _, p, _ in graph.calls))

    def test_container_timeout(self):
        graph = FakeGraph([("/C1", {"status_code": "IN_PROGRESS"})] * 4)
        with self.assertRaises(ContainerError):
            client(graph).wait_for_container("C1", interval=10, timeout=30)

    def test_user_id_looked_up_when_missing(self):
        graph = FakeGraph([("/me", {"user_id": "999", "username": "u"}), ("/999/content_publishing_limit", {"data": [{"quota_usage": 0}]})])
        self.assertEqual(client(graph, user_id=None).publishing_limit(), {"quota_usage": 0})

    def test_transient_error_retried(self):
        graph = FakeGraph([("/me", http_error(500, code=2)), ("/me", {"user_id": "1"})])
        self.assertEqual(client(graph).me(), {"user_id": "1"})

    def test_token_error_not_retried_and_token_not_leaked(self):
        graph = FakeGraph([("/me", http_error(400, code=190, message="Invalid OAuth access token"))])
        with self.assertRaises(InstagramAPIError) as ctx:
            client(graph).me()
        self.assertEqual(ctx.exception.code, 190)
        self.assertEqual(len(graph.calls), 1)
        self.assertNotIn(TOKEN, str(ctx.exception))

    def test_network_error_does_not_leak_token(self):
        graph = FakeGraph([("/me", urllib.error.URLError("down"))] * 3)
        with self.assertRaises(InstagramAPIError) as ctx:
            client(graph).me()
        self.assertNotIn(TOKEN, str(ctx.exception))

    def test_refresh_uses_unversioned_endpoint(self):
        graph = FakeGraph([("/refresh_access_token", {"access_token": "NEW", "expires_in": 5184000})])
        self.assertEqual(client(graph).refresh_token()["access_token"], "NEW")
        self.assertEqual(graph.calls[0][1], "/refresh_access_token")
        self.assertEqual(graph.calls[0][2]["grant_type"], "ig_refresh_token")

    def test_input_validation(self):
        c = client(FakeGraph([]))
        with self.assertRaises(ValueError):
            c.create_reel_container("http://insecure/v.mp4")
        with self.assertRaises(ValueError):
            c.create_reel_container("https://ok/v.mp4", caption="x" * 2201)
        with self.assertRaises(ValueError):
            c.create_reel_container("https://ok/v.mp4", caption=" ".join(f"#t{i}" for i in range(31)))
        with self.assertRaises(ValueError):
            InstagramClient("")


if __name__ == "__main__":
    unittest.main()
