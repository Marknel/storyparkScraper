import http.server
import json
import socketserver
from pathlib import Path

from storypark_scraper.config import load_config
from storypark_scraper.state import StateStore
from storypark_scraper.status import build_status_payload


def run_webserver(config_path: Path, port: int) -> int:
    config = load_config(config_path)

    class StatusHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path in ("/", "/status", "/status.json"):
                try:
                    store = StateStore(config.paths.state_db)
                    store.initialize()
                    payload = build_status_payload(store)
                    store.close()
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
            if self.path in ("/", "/status", "/status.json"):
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

    with ThreadedServer(("", port), StatusHandler) as httpd:
        print(f"Serving status on port {port}...")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
