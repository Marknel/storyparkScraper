import http.server
import json
from pathlib import Path
import socketserver
import urllib.parse

from storypark_scraper.config import load_config
from storypark_scraper.state import StateStore
from storypark_scraper.status import build_status_payload


def create_webserver(
    config_path: Path,
    port: int,
    host: str = "127.0.0.1",
    auth_token: str | None = None,
) -> socketserver.ThreadingTCPServer:
    config = load_config(config_path)

    class StatusHandler(http.server.BaseHTTPRequestHandler):
        def _is_authorized(self) -> bool:
            if not auth_token:
                return True
            auth_header = self.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:].strip()
                if token == auth_token:
                    return True
            parsed_url = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed_url.query)
            if query.get("token", [None])[0] == auth_token:
                return True
            return False

        def _get_status_payload(self) -> dict:
            if config.paths.status_file.exists():
                try:
                    return json.loads(config.paths.status_file.read_text(encoding="utf-8"))
                except Exception:
                    pass
            store = StateStore(config.paths.state_db)
            try:
                store.initialize()
                return build_status_payload(store)
            finally:
                store.close()

        def do_GET(self) -> None:
            parsed_path = urllib.parse.urlparse(self.path).path
            if parsed_path in ("/", "/status", "/status.json"):
                if not self._is_authorized():
                    body = json.dumps({"error": "Unauthorized"}).encode("utf-8")
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", "Bearer")
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                try:
                    payload = self._get_status_payload()
                    body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as e:
                    body = json.dumps({"error": str(e)}, indent=2).encode("utf-8")
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def do_HEAD(self) -> None:
            parsed_path = urllib.parse.urlparse(self.path).path
            if parsed_path in ("/", "/status", "/status.json"):
                if not self._is_authorized():
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", "Bearer")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            # Suppress default noisy stderr logging in production
            pass

    class ThreadedServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True

    return ThreadedServer((host, port), StatusHandler)


def run_webserver(
    config_path: Path,
    port: int,
    host: str = "127.0.0.1",
    auth_token: str | None = None,
) -> int:
    with create_webserver(config_path, port, host=host, auth_token=auth_token) as httpd:
        print(f"Serving status on {host}:{port}...")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
