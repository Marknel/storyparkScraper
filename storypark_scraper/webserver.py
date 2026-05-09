import http.server
import json
import socketserver
from pathlib import Path

from storypark_scraper.config import load_config
from storypark_scraper.state import StateStore
from storypark_scraper.status import build_status_payload


def run_webserver(config_path: Path, port: int) -> int:
    config = load_config(config_path)

    class StatusHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path in ("/", "/status", "/status.json"):
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                
                store = StateStore(config.paths.state_db)
                try:
                    store.initialize()
                    payload = build_status_payload(store)
                    self.wfile.write(json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"))
                except Exception as e:
                    self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                finally:
                    store.close()
            else:
                self.send_response(404)
                self.end_headers()

    with socketserver.TCPServer(("", port), StatusHandler) as httpd:
        print(f"Serving status on port {port}...")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
