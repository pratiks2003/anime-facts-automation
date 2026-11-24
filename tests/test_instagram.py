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
        self.headers = []

    def __call__(self, request, timeout):
        parsed = urllib.parse.urlparse(request.full_url)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        if request.data and request.get_header("Content-type") == "application/x-www-form-urlencoded":
            params.update(urllib.parse.parse_qsl(request.data.decode()))
        self.calls.append((request.get_method(), parsed.path, params))
        self.headers.append(dict(request.header_items()))
        expected, outcome = self.script.pop(0)
        assert parsed.path.endswith(expected), f"expected {expected}, got {parsed.path}"
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(json.dumps(outcome).encode())


def client(graph, user_id="1789", login="unified"):
    return InstagramClient(TOKEN, user_id=user_id, login=login, opener=graph, sleep=lambda s: None)


class InstagramClientTests(unittest.TestCase):
    def test_full_publish_flow(self):
        graph = FakeGraph([
            ("/1789/media", {"id": "C1"}),
            ("/C1", {"status_code": "IN_PROGRESS"}),
            ("/C1", {"status_code": "FINISHED"}),
            ("/1789/media_publish", {"id": "M1"}),
            ("/M1", {"permalink": "https://www.instagram.com/reel/abc/"}),
        ])
        result = client(graph).publish_reel("Hello #anime", video_url="https://example.org/v.mp4", publish=True)
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
        result = client(graph).publish_reel("x", video_url="https://example.org/v.mp4")
        self.assertFalse(result.published)
        self.assertIsNone(result.media_id)
        self.assertEqual(len(graph.calls), 2)

    def test_container_error_never_publishes(self):
        graph = FakeGraph([("/1789/media", {"id": "C1"}), ("/C1", {"status_code": "ERROR", "status": "bad codec"})] * 3)
        with self.assertRaises(ContainerError) as ctx:
            client(graph).publish_reel("x", video_url="https://example.org/v.mp4", publish=True)
        self.assertIn("bad codec", str(ctx.exception))
        self.assertFalse(any(p.endswith("media_publish") for _, p, _ in graph.calls))

    def test_container_timeout(self):
        graph = FakeGraph([("/C1", {"status_code": "IN_PROGRESS"})] * 4)
        with self.assertRaises(ContainerError):
            client(graph).wait_for_container("C1", interval=10, timeout=30)

    def test_user_id_looked_up_when_missing(self):
        graph = FakeGraph([("/me/assigned_instagram_accounts", {"data": [{"id": "999", "username": "u"}]}),
                           ("/999/content_publishing_limit", {"data": [{"quota_usage": 0}]})])
        self.assertEqual(client(graph, user_id=None).publishing_limit(), {"quota_usage": 0})

    def test_transient_error_retried(self):
        graph = FakeGraph([("/me/assigned_instagram_accounts", http_error(500, code=2)), ("/me/assigned_instagram_accounts", {"data": [{"id": "1", "username": "u"}]})])
        self.assertEqual(client(graph, user_id=None).account(), {"user_id": "1", "username": "u"})

    def test_token_error_not_retried_and_token_not_leaked(self):
        graph = FakeGraph([("/me/assigned_instagram_accounts", http_error(400, code=190, message="Invalid OAuth access token"))])
        with self.assertRaises(InstagramAPIError) as ctx:
            client(graph).account()
        self.assertEqual(ctx.exception.code, 190)
        self.assertEqual(len(graph.calls), 1)
        self.assertNotIn(TOKEN, str(ctx.exception))

    def test_network_error_does_not_leak_token(self):
        graph = FakeGraph([("/me/assigned_instagram_accounts", urllib.error.URLError("down"))] * 3)
        with self.assertRaises(InstagramAPIError) as ctx:
            client(graph).account()
        self.assertNotIn(TOKEN, str(ctx.exception))

    def test_refresh_uses_unversioned_endpoint(self):
        graph = FakeGraph([("/refresh_access_token", {"access_token": "NEW", "expires_in": 5184000})])
        self.assertEqual(client(graph, login="instagram").refresh_token()["access_token"], "NEW")
        self.assertEqual(graph.calls[0][1], "/refresh_access_token")
        self.assertEqual(graph.calls[0][2]["grant_type"], "ig_refresh_token")

    def test_resumable_file_upload(self):
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"fake-mp4-bytes")
        try:
            graph = FakeGraph([
                ("/1789/media", {"id": "C9", "uri": "https://rupload.facebook.com/ig-api-upload/v27.0/C9"}),
                ("/ig-api-upload/v27.0/C9", {"success": True}),
                ("/C9", {"status_code": "FINISHED"}),
            ])
            result = client(graph).publish_reel("cap", video_path=f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(result.container_id, "C9")
        self.assertFalse(result.published)
        self.assertEqual(graph.calls[0][2]["upload_type"], "resumable")
        self.assertNotIn("video_url", graph.calls[0][2])
        self.assertEqual(graph.headers[1]["Authorization"], f"OAuth {TOKEN}")
        self.assertEqual(graph.headers[1]["File_size"], "14")
        self.assertEqual(graph.headers[1]["Offset"], "0")

    def test_resumable_rejected_for_instagram_login(self):
        with self.assertRaises(ValueError):
            client(FakeGraph([]), login="instagram").upload_reel_file(__file__)

    def test_unexpected_upload_host_rejected(self):
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"x")
        try:
            graph = FakeGraph([("/1789/media", {"id": "C9", "uri": "https://evil.example/upload"})])
            with self.assertRaises(InstagramAPIError):
                client(graph).upload_reel_file(f.name)
        finally:
            os.unlink(f.name)

    def test_unified_hosts(self):
        self.assertEqual(client(FakeGraph([])).host, "https://graph.facebook.com")
        self.assertEqual(client(FakeGraph([]), login="instagram").host, "https://graph.instagram.com")

    def test_multiple_accounts_require_user_id(self):
        accounts = {"data": [{"id": "1", "username": "a"}, {"id": "2", "username": "b"}]}
        with self.assertRaises(InstagramAPIError):
            client(FakeGraph([("/me/assigned_instagram_accounts", accounts)]), user_id=None).account()
        picked = client(FakeGraph([("/me/assigned_instagram_accounts", accounts)]), user_id="2").account()
        self.assertEqual(picked, {"user_id": "2", "username": "b"})

    def test_unified_refresh_not_supported(self):
        with self.assertRaises(ValueError):
            client(FakeGraph([])).refresh_token()

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


class RetryTests(unittest.TestCase):
    def test_processing_failed_is_retried_then_published(self):
        graph = FakeGraph([
            ("/1789/media", http_error(400, message="Request processing failed")),
            ("/1789/media", {"id": "C2"}),
            ("/C2", {"status_code": "FINISHED"}),
            ("/1789/media_publish", {"id": "M2"}),
            ("/M2", {"permalink": "https://www.instagram.com/reel/x/"}),
        ])
        result = client(graph).publish_reel("cap", video_url="https://example.org/v.mp4", publish=True)
        self.assertTrue(result.published)
        self.assertEqual(result.media_id, "M2")

    def test_hard_error_not_retried(self):
        graph = FakeGraph([("/1789/media", http_error(400, code=100, message="Invalid parameter"))])
        with self.assertRaises(InstagramAPIError):
            client(graph).publish_reel("cap", video_url="https://example.org/v.mp4", publish=True)
        self.assertEqual(len(graph.calls), 1)


class MetricsTests(unittest.TestCase):
    def test_basic_counts_when_insights_permission_missing(self):
        graph = FakeGraph([
            ("/M1", {"like_count": 4, "comments_count": 2, "permalink": "p", "thumbnail_url": "t"}),
            ("/M1/insights", http_error(400, code=10, message="Application does not have permission")),
        ])
        m = client(graph).media_metrics("M1")
        self.assertEqual((m["likes"], m["comments"], m["insights_available"]), (4, 2, False))

    def test_insights_parsed_when_available(self):
        graph = FakeGraph([
            ("/M1", {"like_count": 4, "comments_count": 2}),
            ("/M1/insights", {"data": [{"name": "views", "values": [{"value": 1200}]},
                                       {"name": "ig_reels_avg_watch_time", "values": [{"value": 8400}]}]}),
        ])
        m = client(graph).media_metrics("M1")
        self.assertEqual((m["views"], m["avg_watch_ms"], m["insights_available"]), (1200, 8400, True))
