from pathlib import Path
import tempfile
import unittest

from storypark_scraper.locking import RunLockError, acquire_run_lock


class LockingTests(unittest.TestCase):
    def test_contender_does_not_mask_lock_error_or_release_owner(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "run.lock"
            with acquire_run_lock(path):
                for _ in range(2):
                    with self.assertRaisesRegex(RunLockError, "Another run already holds"):
                        with acquire_run_lock(path):
                            self.fail("A contender acquired the owner's lock")
            # Once the owner exits, a new login or run can use the profile.
            with acquire_run_lock(path):
                pass
