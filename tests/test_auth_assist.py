from __future__ import annotations

from http.client import HTTPConnection
import json
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from storypark_scraper import auth_assist


class FakePage:
    def __init__(self):
        self.viewport_size = {"width": 640, "height": 480}
        self.done = threading.Event()
        self.calls = []
        self.wait_entered = threading.Event()
        self.wait_gate = threading.Event()
        self.wait_gate.set()
        self.finish_during_screenshot = False
        self.screenshot_error = None
        self.mouse = SimpleNamespace(click=lambda x, y: self.record("click", x, y))
        self.keyboard = SimpleNamespace(type=lambda text: self.record("type", text), press=self.press)

    @property
    def url(self):
        raise AssertionError("The helper must not read or disclose the browser URL")

    def record(self, *action):
        self.calls.append((threading.get_ident(), action))

    def press(self, key):
        self.record("key", key)
        if key == "Enter":
            self.done.set()

    def screenshot(self, **kwargs):
        self.record("screenshot", self.done.is_set())
        if self.screenshot_error:
            raise self.screenshot_error
        if self.finish_during_screenshot:
            self.done.set()
            return b"private authenticated home page"
        return b"login-view-jpeg"

    def wait_for_timeout(self, ms):
        self.wait_entered.set()
        self.wait_gate.wait(3)
        time.sleep(0.005)


class RunningAssist:
    def __init__(self, page=None, timeout=3):
        self.page = page or FakePage()
        self.ready = threading.Event()
        self.url = None
        self.error = None
        self.thread_id = None

        def run():
            self.thread_id = threading.get_ident()
            try:
                auth_assist.wait_for_user(self.page, lambda p: p.done.is_set(), port=0,
                                          timeout=timeout, on_ready=self.on_ready)
            except BaseException as exc:
                self.error = exc

        self.thread = threading.Thread(target=run, daemon=True)

    def on_ready(self, url):
        self.url = url
        self.ready.set()

    def start(self):
        self.thread.start()
        if not self.ready.wait(2):
            raise AssertionError(f"Login helper did not start: {self.error!r}")
        return self

    @property
    def token(self):
        return parse_qs(urlsplit(self.url).fragment)["token"][0]

    def request(self, method="GET", path="/status", data=None, authorized=True, headers=None, body=None):
        parsed = urlsplit(self.url)
        request_headers = {"Authorization": "Bearer " + self.token} if authorized else {}
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update(headers)
        connection = HTTPConnection(parsed.hostname, parsed.port, timeout=2)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def wait_for_image(self):
        for _ in range(100):
            code, _, body = self.request(path="/screenshot.jpg")
            if code == 200:
                return body
            time.sleep(0.005)
        raise AssertionError("No login screenshot was published")

    def stop(self):
        self.page.done.set()
        self.page.wait_gate.set()
        self.thread.join(2)
        if self.thread.is_alive():
            raise AssertionError("Login helper did not shut down")


class LoginAssistTests(unittest.TestCase):
    def start_assist(self, *args, **kwargs):
        assist = RunningAssist(*args, **kwargs).start()
        self.addCleanup(assist.stop)
        return assist

    def test_private_fragment_and_bearer_protected_data(self):
        assist = self.start_assist()
        parsed = urlsplit(assist.url)
        self.assertEqual(parsed.hostname, "127.0.0.1")
        self.assertEqual(parsed.query, "")
        self.assertGreaterEqual(len(assist.token), 32)
        code, headers, body = assist.request(path="/", authorized=False)
        self.assertEqual(code, 200)
        self.assertNotIn(assist.token.encode(), body)
        self.assertIn(b"location.hash", body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        for path in ("/status", "/screenshot.jpg"):
            self.assertEqual(assist.request(path=path, authorized=False)[0], 401)
            self.assertEqual(assist.request(path=path, headers={"Authorization": "Bearer invalid"})[0], 401)
        self.assertEqual(assist.request("POST", "/type", {"text": "123456"}, authorized=False)[0], 401)
        self.assertEqual(assist.request(headers={"Authorization": "Bearer \xff"})[0], 401)
        self.assertEqual(assist.wait_for_image(), b"login-view-jpeg")
        code, headers, body = assist.request()
        self.assertEqual(code, 200)
        self.assertEqual(set(json.loads(body)), {"done", "ready"})
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertNotIn("Set-Cookie", headers)

    def test_cross_origin_controls_and_reads_are_rejected(self):
        assist = self.start_assist()
        for origin in ("http://attacker.invalid", "null", "http://[", "https://" + urlsplit(assist.url).netloc):
            with self.subTest(origin=origin):
                self.assertEqual(assist.request(headers={"Origin": origin})[0], 403)
                self.assertEqual(assist.request("POST", "/key", {"key": "Tab"}, headers={"Origin": origin})[0], 403)
        self.assertEqual(assist.request(headers={"Origin": "http://" + urlsplit(assist.url).netloc})[0], 200)

    def test_invalid_inputs_and_manual_done_do_not_authenticate(self):
        assist = self.start_assist()
        assist.wait_for_image()
        invalid = [
            ("/click", {"x": -1, "y": 1}), ("/click", {"x": 640, "y": 1}),
            ("/click", {"x": 1, "y": 480}), ("/click", {"x": True, "y": 1}),
            ("/click", {"x": float("nan"), "y": 1}), ("/click", {"x": float("inf"), "y": 1}),
            ("/click", {"x": 10**400, "y": 1}), ("/click", {"x": 1}),
            ("/key", {"key": "Control+L"}), ("/key", {"key": "F12"}),
            ("/type", {"text": "x" * 4097}), ("/type", {"text": ""}),
            ("/type", {"text": 123}), ("/type", {"text": "\ud800"}),
            ("/type", {"text": "x", "script": "alert(1)"}),
            ("/type", []),
        ]
        for path, payload in invalid:
            with self.subTest(path=path, payload=str(payload)[:70]):
                self.assertEqual(assist.request("POST", path, payload)[0], 400)
        self.assertEqual(assist.request("POST", "/done", {})[0], 404)
        self.assertEqual(assist.request("POST", "/evaluate", {})[0], 404)
        self.assertEqual(assist.request("POST", "/type", body=b"{", headers={"Content-Type": "application/json"})[0], 400)
        self.assertEqual(assist.request("POST", "/type", body=b"\xff", headers={"Content-Type": "application/json"})[0], 400)
        self.assertEqual(assist.request("POST", "/type", body=b"[" * 2000 + b"]" * 2000,
                                        headers={"Content-Type": "application/json"})[0], 400)
        self.assertEqual(assist.request("POST", "/type", body=b"{}", headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(assist.request("POST", "/type", body=b"{}", headers={"Content-Type": "application/json", "Content-Length": "8193"})[0], 413)
        self.assertEqual(assist.request("POST", "/type", body=b"{}", headers={"Content-Length": "bad"})[0], 400)
        self.assertTrue(assist.thread.is_alive())
        self.assertFalse(assist.page.done.is_set())
        self.assertFalse(any(call[0] in {"click", "type", "key"} for _, call in assist.page.calls))

    def test_actions_are_executed_on_the_playwright_calling_thread(self):
        assist = self.start_assist()
        assist.wait_for_image()
        self.assertEqual(assist.request("POST", "/click", {"x": 50.5, "y": 40})[0], 204)
        self.assertEqual(assist.request("POST", "/type", {"text": "123456"})[0], 204)
        self.assertEqual(assist.request("POST", "/key", {"key": "Tab"})[0], 204)
        self.assertEqual(assist.request("POST", "/key", {"key": "Enter"})[0], 204)
        assist.thread.join(2)
        self.assertFalse(assist.thread.is_alive())
        self.assertIsNone(assist.error)
        actions = [call for _, call in assist.page.calls if call[0] in {"click", "type", "key"}]
        self.assertEqual(actions, [("click", 50.5, 40), ("type", "123456"), ("key", "Tab"), ("key", "Enter")])
        self.assertTrue(all(thread_id == assist.thread_id for thread_id, _ in assist.page.calls))
        self.assertNotIn(("screenshot", True), [call for _, call in assist.page.calls])

    def test_action_queue_is_bounded(self):
        page = FakePage()
        page.wait_gate.clear()
        assist = self.start_assist(page)
        self.assertTrue(page.wait_entered.wait(1))
        for _ in range(auth_assist._MAX_ACTIONS):
            self.assertEqual(assist.request("POST", "/key", {"key": "Tab"})[0], 204)
        self.assertEqual(assist.request("POST", "/key", {"key": "Tab"})[0], 429)
        self.assertEqual(assist.request()[0], 200)

    def test_timeout_shuts_down_without_claiming_success(self):
        states = []
        original = auth_assist._State

        def capture(token):
            state = original(token)
            states.append(state)
            return state

        with patch.object(auth_assist, "_State", side_effect=capture):
            assist = self.start_assist(timeout=0.05)
            assist.thread.join(2)
        self.assertIsInstance(assist.error, TimeoutError)
        self.assertFalse(assist.thread.is_alive())
        self.assertTrue(states[0].closed)
        self.assertFalse(states[0].done)
        self.assertEqual(states[0].screenshot, b"")
        with self.assertRaises(OSError):
            assist.request()

    def test_frame_captured_during_success_is_discarded(self):
        page = FakePage()
        page.finish_during_screenshot = True
        states = []
        original = auth_assist._State

        def capture(token):
            state = original(token)
            states.append(state)
            return state

        with patch.object(auth_assist, "_State", side_effect=capture):
            assist = self.start_assist(page)
            assist.thread.join(2)
        self.assertIsNone(assist.error)
        self.assertTrue(states[0].done)
        self.assertEqual(states[0].screenshot, b"")
        self.assertTrue(states[0].actions.empty())

    def test_browser_failure_and_callback_failure_clean_up(self):
        page = FakePage()
        page.screenshot_error = RuntimeError("Browser closed")
        assist = self.start_assist(page)
        assist.thread.join(2)
        self.assertIsInstance(assist.error, RuntimeError)
        with self.assertRaises(OSError):
            assist.request()
        links = []

        def fail(url):
            links.append(url)
            raise RuntimeError("Cannot notify owner")

        with self.assertRaisesRegex(RuntimeError, "Cannot notify owner"):
            auth_assist.wait_for_user(FakePage(), lambda page: False, port=0, on_ready=fail)
        parsed = urlsplit(links[0])
        connection = HTTPConnection(parsed.hostname, parsed.port, timeout=1)
        try:
            with self.assertRaises(OSError):
                connection.request("GET", "/")
        finally:
            connection.close()

    def test_completed_login_does_not_start_a_server(self):
        page = FakePage()
        callbacks = []
        auth_assist.wait_for_user(page, lambda page: True, on_ready=callbacks.append)
        self.assertEqual(callbacks, [])
        self.assertEqual(page.calls, [])

    def test_invalid_server_parameters_fail_before_startup(self):
        for kwargs in ({"timeout": 0}, {"timeout": float("nan")}, {"timeout": True},
                       {"port": -1}, {"port": 65536}, {"port": True}, {"host": ""}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                auth_assist.wait_for_user(FakePage(), lambda page: False, **kwargs)


if __name__ == "__main__":
    unittest.main()
