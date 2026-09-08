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

        server_thread = threading.Thread(
            target=run_webserver,
            args=(config_path, port),
            kwargs={"host": "127.0.0.1"},
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
        ctx.exception.close()

    def test_webserver_authentication(self):
        root = prepare_case_dir("webserver_auth")
        config_path = root / "config.yaml"
        status_file = root / "status" / "status.json"
        status_file.parent.mkdir(parents=True, exist_ok=True)
        status_file.write_text(json.dumps({"version": 1, "state": "success", "from_file": True}), encoding="utf-8")

        config_path.write_text(
            f"""
paths:
  archive: {root}/archive
  state_db: {root}/state/state.sqlite
  status_file: {status_file}
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

        port = 18093
        secret_token = "secure-test-token-xyz"

        server_thread = threading.Thread(
            target=run_webserver,
            args=(config_path, port),
            kwargs={"host": "127.0.0.1", "auth_token": secret_token},
            daemon=True,
        )
        server_thread.start()
        time.sleep(0.5)

        # 1. Request without auth -> 401
        url = f"http://127.0.0.1:{port}/status"
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(url)
        self.assertEqual(ctx.exception.code, 401)
        self.assertEqual(ctx.exception.headers.get("WWW-Authenticate"), "Bearer")
        ctx.exception.close()

        # 2. Request with wrong token -> 401
        bad_req = urllib.request.Request(url, headers={"Authorization": "Bearer wrong-token"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(bad_req)
        self.assertEqual(ctx.exception.code, 401)
        ctx.exception.close()

        # 3. Request with Bearer header -> 200
        good_req = urllib.request.Request(url, headers={"Authorization": f"Bearer {secret_token}"})
        with urllib.request.urlopen(good_req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data.get("from_file"))

        # 4. Request with query param -> 200
        query_url = f"http://127.0.0.1:{port}/status.json?token={secret_token}"
        with urllib.request.urlopen(query_url) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data.get("from_file"))


if __name__ == "__main__":
    unittest.main()
