from __future__ import annotations

from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from storypark_scraper.credentials import load_credentials, store_credentials


RUNTIME_ROOT = Path(__file__).parent / ".runtime"


def prepare_case_dir(name: str) -> Path:
    path = RUNTIME_ROOT / name
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


class CredentialTests(unittest.TestCase):
    @unittest.skipUnless(__import__("os").name == "nt", "Windows-only credential fallback test")
    def test_store_credentials_falls_back_to_dpapi_file(self):
        root = prepare_case_dir("credentials")
        with patch(
            "storypark_scraper.credentials.store_windows_credentials",
            side_effect=__import__("storypark_scraper.credentials", fromlist=["CredentialError"]).CredentialError("credential manager unavailable"),
        ):
            outcome = store_credentials(root, "storypark-scraper", "user@example.com", "secret-pass")

        self.assertEqual(outcome.backend, "dpapi-local-file")
        self.assertIsNotNone(outcome.path)
        creds = load_credentials(root, "storypark-scraper")
        self.assertEqual(creds.username, "user@example.com")
        self.assertEqual(creds.password, "secret-pass")
        self.assertEqual(creds.source, "dpapi-file")


if __name__ == "__main__":
    unittest.main()

