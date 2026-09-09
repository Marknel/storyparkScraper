from __future__ import annotations

import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import warnings

from storypark_scraper.credentials import (
    _credential_fallback_path,
    _dpapi_protect,
    _load_dotenv,
    _load_from_dpapi_file,
    _load_from_windows_credential_manager,
    _store_dpapi_file,
    delete_windows_credentials,
    load_credentials,
    store_credentials,
    store_windows_credentials,
)


RUNTIME_ROOT = Path(__file__).parent / ".runtime"


def prepare_case_dir(name: str) -> Path:
    path = RUNTIME_ROOT / name
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


class CredentialTests(unittest.TestCase):
    @unittest.skipUnless(__import__("os").name == "nt", "Windows-only credential test")
    def test_store_and_load_windows_credentials_in_process(self):
        target = "storypark-scraper-unit-test"
        username = "test_user@example.com"
        password = "SuperSecretPassword123!"

        try:
            store_windows_credentials(target, username, password)
            creds = _load_from_windows_credential_manager(target)
            self.assertIsNotNone(creds)
            self.assertEqual(creds.username, username)
            self.assertEqual(creds.password, password)
            self.assertEqual(creds.source, "windows-credential-manager")
        finally:
            delete_windows_credentials(target)

    @unittest.skipUnless(__import__("os").name == "nt", "Windows-only credential fallback test")
    def test_store_credentials_falls_back_to_dpapi_file(self):
        root = prepare_case_dir("credentials_dpapi")
        with patch(
            "storypark_scraper.credentials.store_windows_credentials",
            side_effect=__import__("storypark_scraper.credentials", fromlist=["CredentialError"]).CredentialError("credential manager unavailable"),
        ):
            outcome = store_credentials(root, "storypark-scraper-fallback", "user@example.com", "secret-pass")

        self.assertEqual(outcome.backend, "dpapi-local-file")
        self.assertIsNotNone(outcome.path)

        # Verify DPAPI file payload has user scope
        payload = json.loads(outcome.path.read_text(encoding="utf-8"))
        self.assertEqual(payload.get("scope"), "current_user")
        self.assertEqual(payload.get("version"), 2)

        creds = load_credentials(root, "storypark-scraper-fallback")
        self.assertEqual(creds.username, "user@example.com")
        self.assertEqual(creds.password, "secret-pass")
        self.assertEqual(creds.source, "dpapi-file")

    @unittest.skipUnless(__import__("os").name == "nt", "Windows-only DPAPI compatibility test")
    def test_dpapi_backwards_compatible_with_machine_scope(self):
        root = prepare_case_dir("credentials_machine_dpapi")
        target = "storypark-scraper-machine-compat"
        password = "machine-pass-123"
        encrypted = _dpapi_protect(password.encode("utf-8"), local_machine=True)
        import base64
        payload = {
            "version": 1,
            "backend": "dpapi-local-file",
            "scope": "local_machine",
            "target": target,
            "username": "legacy@example.com",
            "password": base64.b64encode(encrypted).decode("ascii"),
        }
        fb_path = _credential_fallback_path(root)
        fb_path.parent.mkdir(parents=True, exist_ok=True)
        fb_path.write_text(json.dumps(payload), encoding="utf-8")

        creds = _load_from_dpapi_file(fb_path, target)
        self.assertIsNotNone(creds)
        self.assertEqual(creds.username, "legacy@example.com")
        self.assertEqual(creds.password, password)

    def test_plaintext_dotenv_warning(self):
        root = prepare_case_dir("credentials_dotenv")
        env_file = root / ".env"
        env_file.write_text("SP_USER=testenv@example.com\nSP_PASS=plaintextpass\n", encoding="utf-8")

        with patch("storypark_scraper.credentials._load_from_windows_credential_manager", return_value=None), \
             patch("storypark_scraper.credentials._load_from_dpapi_file", return_value=None):
            with warnings.catch_warnings(record=True) as recorded_warnings:
                warnings.simplefilter("always")
                creds = load_credentials(root, "nonexistent-target")

                self.assertEqual(creds.username, "testenv@example.com")
                self.assertEqual(creds.password, "plaintextpass")
                self.assertEqual(creds.source, "dotenv/env")

                self.assertTrue(any(
                    "plaintext .env file" in str(w.message) for w in recorded_warnings
                ))

    def test_load_dotenv_parsing(self):
        root = prepare_case_dir("credentials_dotenv_parsing")
        env_file = root / ".env"
        env_file.write_text(
            """
            # Leading comment
            export SP_USER="quoted_user@example.com"
            SP_PASS='secret\\'single\\'' # inline comment
            COMPLEX_KEY = "value with # hash and = equals"
            EMPTY_VAR=
            ANOTHER="hello \\"world\\""
            """.strip(),
            encoding="utf-8",
        )
        parsed = _load_dotenv(env_file)
        self.assertEqual(parsed.get("SP_USER"), "quoted_user@example.com")
        self.assertEqual(parsed.get("SP_PASS"), "secret'single'")
        self.assertEqual(parsed.get("COMPLEX_KEY"), "value with # hash and = equals")
        self.assertEqual(parsed.get("EMPTY_VAR"), "")
        self.assertEqual(parsed.get("ANOTHER"), 'hello "world"')


if __name__ == "__main__":
    unittest.main()

