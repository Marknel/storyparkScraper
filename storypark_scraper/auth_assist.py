"""Short-lived, owner-operated login assistance for Storypark's browser.

Adapted from AustralianSimon/storyparkScraper auth_assist.py, commit b27237e,
by Simon Wise, distributed under the repository's MIT licence. The original
licence is retained in LICENSE. Authentication and bounded input are added here.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import math
import queue
import secrets
import socket
import threading
import time
from urllib.parse import urlsplit


_MAX_BODY_BYTES = 8192
_MAX_TEXT_LENGTH = 4096
_MAX_ACTIONS = 64
_KEYS = frozenset({"Enter", "Tab", "Backspace", "Escape", "ControlOrMeta+A"})

_PAGE_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Storypark sign-in assistance</title>
<style>
body{font:16px system-ui,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;background:#f7f8fa;color:#192333}
#screen{display:block;max-width:100%;height:auto;border:1px solid #b5bdc8;cursor:crosshair}
.controls{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0}input,button{font:inherit;padding:8px}
input{flex:1;min-width:180px}button{cursor:pointer}#status{min-height:1.5em}
</style></head><body><h1>Complete Storypark sign-in</h1>
<p>Use the live browser view to complete Storypark's sign-in and verification steps.
Sign-in is checked automatically. Keep this private link to yourself.</p>
<p id="status" role="status">Connecting to the scraper…</p>
<div class="controls"><input id="text" type="password" maxlength="4096" aria-label="Text or verification code"
placeholder="Text or verification code" autocomplete="one-time-code">
<button id="type">Type</button><button data-key="Enter">Enter</button>
<button data-key="Tab">Tab</button><button data-key="Backspace">Backspace</button>
<button data-key="Escape">Escape</button><button data-key="ControlOrMeta+A">Select all</button></div>
<img id="screen" alt="Storypark sign-in browser view" hidden>
<script>
(() => {
  const token = new URLSearchParams(location.hash.slice(1)).get('token') || '';
  history.replaceState(null, '', location.pathname);
  const screen = document.getElementById('screen');
  const status = document.getElementById('status');
  const input = document.getElementById('text');
  let stopped = false, imageUrl = null;
  function finish(message) {
    stopped = true; screen.hidden = true; screen.removeAttribute('src');
    if (imageUrl) { URL.revokeObjectURL(imageUrl); imageUrl = null; }
    input.value = '';
    document.querySelectorAll('input,button').forEach(e => e.disabled = true);
    status.textContent = message;
  }
  async function request(path, body) {
    const options = {headers: {'Authorization': 'Bearer ' + token}, credentials: 'omit',
      cache: 'no-store', redirect: 'error'};
    if (body !== undefined) {
      options.method = 'POST'; options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    if (response.status === 401 || response.status === 403) {
      finish('This private link is missing or expired. Reopen the link printed by the scraper.');
      throw new Error('Authorization failed');
    }
    return response;
  }
  async function send(path, body) {
    if (stopped) return;
    try {
      const response = await request(path, body);
      if (!response.ok) status.textContent = 'That input was not accepted. Please try again.';
      return response.ok;
    } catch (_) {
      if (!stopped) finish('The connection has closed. Check the scraper for the sign-in result.');
      return false;
    }
  }
  async function typeText() {
    const text = input.value; input.value = '';
    return text ? await send('/type', {text}) : true;
  }
  document.getElementById('type').onclick = typeText;
  document.querySelectorAll('[data-key]').forEach(button => {
    button.onclick = () => send('/key', {key: button.dataset.key});
  });
  input.onkeydown = async event => {
    if (event.key === 'Enter') {
      event.preventDefault();
      if (await typeText()) await send('/key', {key: 'Enter'});
    }
  };
  screen.onclick = event => {
    const r = screen.getBoundingClientRect();
    send('/click', {x: (event.clientX-r.left)*screen.naturalWidth/r.width,
                    y: (event.clientY-r.top)*screen.naturalHeight/r.height});
  };
  async function refresh() {
    if (stopped) return;
    try {
      const response = await request('/status');
      if (!response.ok) throw new Error('Disconnected');
      const state = await response.json();
      if (state.done) { finish('Sign-in is complete. You can close this page.'); return; }
      status.textContent = 'Waiting for Storypark sign-in. Complete verification in the browser view below.';
      const shot = await request('/screenshot.jpg');
      if (shot.status === 410) { finish('Sign-in is complete. You can close this page.'); return; }
      if (shot.ok) {
        const next = URL.createObjectURL(await shot.blob());
        if (imageUrl) URL.revokeObjectURL(imageUrl);
        imageUrl = next; screen.src = next; screen.hidden = false;
      } else if (shot.status !== 503) { throw new Error('Disconnected'); }
    } catch (_) {
      if (!stopped) finish('The connection has closed. Check the scraper for the sign-in result.');
    }
    if (!stopped) setTimeout(refresh, 500);
  }
  if (!token) finish('Reopen the complete private link printed by the scraper.');
  else refresh();
})();
</script></body></html>"""


class _State:
    def __init__(self, token: str):
        self.token = token
        self.lock = threading.Lock()
        self.screenshot = b""
        self.width = 0
        self.height = 0
        self.done = False
        self.closed = False
        self.actions = queue.Queue(maxsize=_MAX_ACTIONS)

    def complete(self) -> None:
        with self.lock:
            self.done = True
            self._discard_locked()

    def close(self) -> None:
        with self.lock:
            self.closed = True
            self._discard_locked()

    def _discard_locked(self) -> None:
        self.screenshot = b""
        self.width = self.height = 0
        while True:
            try:
                self.actions.get_nowait()
            except queue.Empty:
                break


def _make_handler(state: _State):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(2)

        def log_message(self, format, *args):
            # Requests can carry credentials; never log headers, bodies or URLs.
            pass

        def _send(self, code: int, body: bytes = b"", content_type: str = "text/plain") -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; "
                             "style-src 'unsafe-inline'; img-src 'self' blob:; connect-src 'self'; "
                             "base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            if code == 401:
                self.send_header("WWW-Authenticate", "Bearer")
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass

        def _authorized(self) -> bool:
            header = self.headers.get("Authorization", "")
            scheme, _, value = header.partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(value.encode("utf-8"), state.token.encode("ascii")):
                self._send(401)
                return False
            origin = self.headers.get("Origin")
            if origin is not None:
                try:
                    parsed = urlsplit(origin)
                except ValueError:
                    self._send(403)
                    return False
                host = self.headers.get("Host", "")
                if (parsed.scheme != "http" or parsed.netloc.lower() != host.lower()
                        or parsed.path or parsed.query or parsed.fragment or parsed.username is not None):
                    self._send(403)
                    return False
            return True

        def do_GET(self):
            if self.path == "/":
                self._send(200, _PAGE_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if not self._authorized():
                return
            if self.path == "/status":
                with state.lock:
                    body = json.dumps({"done": state.done, "ready": bool(state.screenshot)}).encode("utf-8")
                self._send(200, body, "application/json")
            elif self.path == "/screenshot.jpg":
                with state.lock:
                    shot, done = state.screenshot, state.done
                self._send(410 if done else 200 if shot else 503, shot, "image/jpeg")
            else:
                self._send(404)

        def do_POST(self):
            if not self._authorized():
                return
            if self.path not in {"/click", "/type", "/key"}:
                self._send(404)
                return
            if self.headers.get("Transfer-Encoding"):
                self._send(400)
                return
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                self._send(411)
                return
            try:
                length = int(raw_length)
            except ValueError:
                self._send(400)
                return
            if length < 0:
                self._send(400)
                return
            if length > _MAX_BODY_BYTES:
                self._send(413)
                return
            if self.headers.get_content_type() != "application/json":
                self._send(415)
                return
            try:
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("Incomplete input")
                data = json.loads(raw.decode("utf-8"))
                _validate_action(self.path[1:], data, state)
            except (ValueError, UnicodeDecodeError, TimeoutError, RecursionError):
                self._send(400)
                return
            with state.lock:
                if state.done or state.closed:
                    code = 410
                else:
                    try:
                        state.actions.put_nowait((self.path[1:], data))
                        code = 204
                    except queue.Full:
                        code = 429
            self._send(code)

    return Handler


def _validate_action(action: str, data, state: _State) -> None:
    if not isinstance(data, dict):
        raise ValueError("Expected an object")
    if action == "click":
        if set(data) != {"x", "y"}:
            raise ValueError("Expected coordinates")
        x, y = data["x"], data["y"]
        if any(isinstance(v, bool) or not isinstance(v, (int, float))
               or isinstance(v, float) and not math.isfinite(v) for v in (x, y)):
            raise ValueError("Invalid coordinates")
        with state.lock:
            if not (0 <= x < state.width and 0 <= y < state.height):
                raise ValueError("Coordinates outside the browser view")
    elif action == "type":
        if set(data) != {"text"} or not isinstance(data["text"], str) or not 0 < len(data["text"]) <= _MAX_TEXT_LENGTH:
            raise ValueError("Invalid text")
        data["text"].encode("utf-8")
    elif action == "key":
        if set(data) != {"key"} or not isinstance(data["key"], str) or data["key"] not in _KEYS:
            raise ValueError("Unsupported key")
    else:
        raise ValueError("Unsupported action")


def _perform(page, action: str, data, state: _State) -> None:
    _validate_action(action, data, state)
    if action == "click":
        page.mouse.click(data["x"], data["y"])
    elif action == "type":
        page.keyboard.type(data["text"])
    elif action == "key":
        page.keyboard.press(data["key"])


def wait_for_user(page, is_done, *, host="127.0.0.1", port=8000, timeout=600, on_ready=None):
    """Wait for owner-completed sign-in, never treating a button as proof of login.

    ``on_ready(url)`` receives a private link with a fresh token in its fragment.
    API requests require that token in an Authorization header. This helper only
    holds screenshots/input in memory; it never stores credentials or sessions.
    The caller must supply a reliable positive authentication predicate.
    """
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Login assistance timeout must be positive and finite")
    if not isinstance(host, str) or not host:
        raise ValueError("Login assistance host is required")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("Invalid login assistance port")
    if is_done(page):
        return
    state = _State(secrets.token_urlsafe(32))

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        block_on_close = False
        address_family = socket.AF_INET6 if ":" in host else socket.AF_INET

    server = Server((host, port), _make_handler(state))
    worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    worker.start()
    display_host = "127.0.0.1" if host == "0.0.0.0" else "::1" if host == "::" else host
    if ":" in display_host:
        display_host = f"[{display_host}]"
    url = f"http://{display_host}:{server.server_address[1]}/#token={state.token}"
    deadline = time.monotonic() + timeout
    try:
        if on_ready is not None:
            on_ready(url)
        while True:
            if is_done(page):
                state.complete()
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for manual Storypark sign-in")
            for _ in range(_MAX_ACTIONS):
                try:
                    action, data = state.actions.get_nowait()
                except queue.Empty:
                    break
                if is_done(page):
                    state.complete()
                    return
                if time.monotonic() >= deadline:
                    raise TimeoutError("Timed out waiting for manual Storypark sign-in")
                _perform(page, action, data, state)
            if is_done(page):
                state.complete()
                return
            viewport = page.viewport_size
            if viewport is None:
                viewport = page.evaluate("({width: innerWidth, height: innerHeight})")
            shot = page.screenshot(type="jpeg", quality=70, scale="css")
            # A navigation may finish during the screenshot request. Never publish
            # that frame if it is now the authenticated application.
            if is_done(page):
                state.complete()
                return
            with state.lock:
                state.screenshot = shot
                state.width, state.height = viewport["width"], viewport["height"]
            page.wait_for_timeout(200)
    finally:
        state.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
