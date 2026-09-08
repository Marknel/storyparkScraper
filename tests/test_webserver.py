from __future__ import annotations

import json
from pathlib import Path
import shutil
import threading
import time
import unittest
import urllib.request
import urllib.error

from storypark_scraper.config import load_config
from storypark_scraper.webserver import run_webserver


RUNTIME_ROOT = Path(__file__).parent / ".runtime"


def prepare_case_dir(name: str) -> Path:
    path = RUNTIME_ROOT / name
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


class WebserverTests(unittest.TestCase):
    def test_webserver_status_and_errors(self):
        root = prepare_case_dir("webserver")
        config_path = root / "config.yaml"
        config_path.write_text(
            f"""
paths:
  archive: {root}/archive
  state_db: {root}/state/state.sqlite
  status_file: {root}/status/status.json
  browser_profile: {root}/state/browser-profile
  lock_file: {root}/state/run.lock
storypark:
  credential_target: demo
filters:
  exclude_keywords: []
runtime:
  stop_after_existing_stories: true
""".strip(),
            encoding="utf-8",
        )

        port = 18092
        stop_event = threading.Event()

        # Start webserver in background thread
        import socketserver
        import http.server
        from storypark_scraper.state import StateStore
        from storypark_scraper.status import build_status_payload

        server_thread = threading.Thread(
            target=run_webserver,
            args=(config_path, port),
            daemon=True,
        )
        server_thread.start()
        time.sleep(0.5)

        # GET /status should return 200
        url = f"http://127.0.0.1:{port}/status"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["version"], 1)

        # GET /invalid should return 404
        bad_url = f"http://127.0.0.1:{port}/not-found"
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(bad_url)
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
