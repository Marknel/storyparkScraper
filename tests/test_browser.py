from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
import traceback
import unittest
from unittest.mock import MagicMock, patch

from storypark_scraper.browser import AuthenticationRequired, StoryparkBrowser
from storypark_scraper.config import AppConfig, FiltersConfig, PathsConfig, RuntimeConfig, StoryparkConfig
from storypark_scraper.credentials import CredentialError
from storypark_scraper.models import Credentials


LOGIN_URL = "https://app.storypark.com/users/sign_in"
HOME_URL = "https://app.storypark.com/children"
FIXTURES = Path(__file__).parent / "fixtures"


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.events = []

    def monotonic(self):
        return self.now

    def at(self, when, action):
        self.events.append((when, action))

    def advance(self, seconds):
        self.now += seconds
        due = [event for event in self.events if event[0] <= self.now]
        self.events = [event for event in self.events if event[0] > self.now]
        for _, action in sorted(due, key=lambda event: event[0]):
            action()


class FakeLocator:
    def __init__(self, page, selector, *, index=None):
        self.page = page
        self.selector = selector
        self.index = index

    @property
    def first(self):
        return self.nth(0)

    def nth(self, index):
        return FakeLocator(self.page, self.selector, index=index)

    def count(self):
        count = self.page.element_count(self.selector)
        return count if self.index is None else int(self.index < count)

    def is_visible(self):
        return bool(self.count() and self.page.element_visible(self.selector, self.index or 0))

    def wait_for(self, *, state, timeout):
        if state != "visible" or not self.is_visible():
            raise TimeoutError("Fixture element is not visible")

    def fill(self, value, **kwargs):
        if not self.is_visible():
            raise AssertionError("Cannot fill a hidden fixture element")
        self.page.fill(self.selector, value, **kwargs)

    def click(self, **kwargs):
        if not self.is_visible():
            raise AssertionError("Cannot click a missing fixture element")
        if self.selector == "button[data-action='signin']":
            self.page.click(self.selector, **kwargs)
        else:
            self.page.menu_open = True


class FakePage:
    """A login form, verification page or authenticated app shell, without a browser."""

    def __init__(self, clock, *, state="login", menu_kind="legacy", html=""):
        self.clock = clock
        self.state = state
        self.menu_kind = menu_kind
        self.html = html
        self.url = LOGIN_URL
        self.redirect_url = None
        self.closed = False
        self.menu_open = False
        self.fills = []
        self.form_timeouts = []
        self.clicks = []
        self.navigations = []
        self.after_submit = None
        self.set_default_timeout = MagicMock()
        if state == "authenticated":
            self.url = HOME_URL
        elif state == "mfa":
            self.url = "https://app.storypark.com/users/two_factor"

    def goto(self, url):
        self.navigations.append(url)
        if self.redirect_url:
            self.url = self.redirect_url
        elif self.state == "authenticated":
            self.url = HOME_URL
        elif self.state == "mfa":
            self.url = "https://app.storypark.com/users/two_factor"
        else:
            self.url = url

    def authenticate(self):
        self.state = "authenticated"
        self.url = HOME_URL

    def is_closed(self):
        return self.closed

    def locator(self, selector):
        return FakeLocator(self, selector)

    def has_element(self, selector):
        if selector in {"#user_email", "#user_password", "button[data-action='signin']"}:
            return self.state == "login"
        if self.menu_kind == "legacy" and "[data-action='open-menu']" in selector:
            return self.state in {"authenticated", "mfa"}
        if self.menu_kind == "current" and "button.v-btn" in selector and "span:text-is('Menu')" in selector:
            return self.state in {"authenticated", "mfa"}
        # Child menu fixtures deliberately use the parser fallback after the menu opens.
        return False

    def element_count(self, selector):
        return int(self.has_element(selector))

    def element_visible(self, selector, index=0):
        return self.has_element(selector)

    def fill(self, selector, value, **kwargs):
        if not self.has_element(selector):
            raise AssertionError("Tried to fill a field that is not present")
        self.fills.append((selector, value))
        self.form_timeouts.append(kwargs.get("timeout"))

    def click(self, selector, **kwargs):
        # Fixture sign-in button has data-action=signin and no presentation classes.
        if selector != "button[data-action='signin']":
            raise AssertionError("Sign-in must not depend on transient CSS classes")
        self.clicks.append(selector)
        self.form_timeouts.append(kwargs.get("timeout"))
        if self.after_submit:
            self.after_submit()

    def wait_for_timeout(self, milliseconds):
        self.clock.advance(milliseconds / 1000)

    def content(self):
        return self.html if self.menu_open else ""


def make_config():
    root = Path(__file__).parent / ".runtime" / "browser"
    return AppConfig(
        root_dir=root,
        paths=PathsConfig(
            archive=root / "archive", state_db=root / "state.sqlite",
            status_file=root / "status.json", browser_profile=root / "profile",
            lock_file=root / "run.lock",
        ),
        storypark=StoryparkConfig(
            login_url=LOGIN_URL,
            stories_url_template="https://app.storypark.com/children/{child_code}/stories",
            credential_target="browser-tests",
        ),
        filters=FiltersConfig(),
        runtime=RuntimeConfig(page_timeout_ms=1000, auth_timeout_seconds=3, user_agent=""),
    )


class BrowserAuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.config = make_config()
        self.credentials = MagicMock(return_value=Credentials("owner@example.test", "not-a-real-password", "test"))
        self.logger = logging.getLogger("storypark_scraper.test.browser")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.timer = patch("storypark_scraper.browser.time.monotonic", side_effect=self.clock.monotonic)
        self.timer.start()
        self.addCleanup(self.timer.stop)

    def browser(self, page, *, headless=True):
        browser = StoryparkBrowser(self.config, self.credentials, self.logger, headless=headless)
        browser._page = page
        return browser

    def test_persisted_session_avoids_loading_credentials(self):
        for menu_kind in ("legacy", "current"):
            with self.subTest(menu_kind=menu_kind):
                page = FakePage(self.clock, state="authenticated", menu_kind=menu_kind)
                self.browser(page).authenticate()
                self.assertEqual(page.fills, [])
                self.assertEqual(page.clicks, [])
        self.credentials.assert_not_called()

    def test_signin_uses_stable_button_and_requires_authenticated_menu(self):
        page = FakePage(self.clock, menu_kind="current")
        page.after_submit = lambda: self.clock.at(self.clock.now + 0.5, page.authenticate)
        self.browser(page).authenticate()
        self.credentials.assert_called_once()
        self.assertEqual(page.fills, [
            ("#user_email", "owner@example.test"), ("#user_password", "not-a-real-password"),
        ])
        self.assertEqual(page.clicks, ["button[data-action='signin']"])
        self.assertTrue(all(0 < timeout <= 5000 for timeout in page.form_timeouts))
        self.assertEqual(page.state, "authenticated")

    def test_failed_form_submission_does_not_expose_the_password_in_tracebacks(self):
        page = FakePage(self.clock)
        page.fill = MagicMock(side_effect=RuntimeError("Playwright fill failed: not-a-real-password"))
        try:
            self.browser(page).authenticate()
        except AuthenticationRequired as exc:
            rendered = "".join(traceback.format_exception(exc))
            self.assertNotIn("not-a-real-password", rendered)
            self.assertIn("Renew the session with the login command", rendered)
            self.assertTrue(exc.__suppress_context__)
        else:
            self.fail("Broken form submission must fail authentication")

    def test_disabled_submit_still_allows_explicit_owner_assistance(self):
        page = FakePage(self.clock)
        page.click = MagicMock(side_effect=TimeoutError("Submit is disabled behind verification"))

        def owner_completes(actual_page, authenticated, **kwargs):
            self.assertFalse(authenticated(actual_page))
            actual_page.authenticate()

        with patch("storypark_scraper.auth_assist.wait_for_user", side_effect=owner_completes) as helper:
            self.browser(page).authenticate(interactive=True, assist=True)
        helper.assert_called_once()
        self.assertEqual(page.state, "authenticated")

    def test_assistance_input_errors_do_not_expose_typed_secrets(self):
        page = FakePage(self.clock, state="challenge")
        with patch("storypark_scraper.auth_assist.wait_for_user", side_effect=RuntimeError("typed private-code-123")):
            try:
                self.browser(page).authenticate(interactive=True, assist=True)
            except AuthenticationRequired as exc:
                rendered = "".join(traceback.format_exception(exc))
                self.assertNotIn("private-code-123", rendered)
                self.assertIn("Retry the login command", rendered)
            else:
                self.fail("Failed browser input must not count as authentication")

    def test_mfa_does_not_count_as_authenticated_even_with_app_menu(self):
        page = FakePage(self.clock)

        def require_mfa():
            page.state = "mfa"
            page.url = "https://app.storypark.com/users/two_factor"

        page.after_submit = require_mfa
        with patch("storypark_scraper.auth_assist.wait_for_user") as helper:
            with self.assertRaisesRegex(AuthenticationRequired, "login.*scheduled-task account"):
                self.browser(page).authenticate()
        helper.assert_not_called()
        self.assertEqual(len(page.clicks), 1)
        self.credentials.assert_called_once()
        self.assertGreaterEqual(self.clock.now, 15)

    def test_unexpected_challenge_without_login_fields_does_not_load_credentials(self):
        page = FakePage(self.clock, state="challenge")
        page.redirect_url = "https://app.storypark.com/security-check"
        with patch("storypark_scraper.auth_assist.wait_for_user") as helper:
            with self.assertRaisesRegex(AuthenticationRequired, "CAPTCHA, MFA or an expired session"):
                self.browser(page).authenticate(assist=True)
        self.credentials.assert_not_called()
        self.assertEqual(page.fills, [])
        helper.assert_not_called()

    def test_foreign_origin_login_fields_never_receive_credentials(self):
        for url in (
            "https://not-storypark.example/users/sign_in",
            "https://app.storypark.com.evil.example/users/sign_in",
            "http://app.storypark.com/users/sign_in",
        ):
            with self.subTest(url=url):
                page = FakePage(self.clock)
                page.redirect_url = url
                with self.assertRaises(AuthenticationRequired):
                    self.browser(page).authenticate()
                self.assertEqual(page.fills, [])
        self.credentials.assert_not_called()

    def test_foreign_origin_menu_is_not_proof_of_authentication(self):
        page = FakePage(self.clock, state="authenticated")
        page.redirect_url = "https://not-storypark.example/children"
        with self.assertRaises(AuthenticationRequired):
            self.browser(page).authenticate()
        self.credentials.assert_not_called()

    def test_explicit_headless_assistance_receives_options_and_live_predicate(self):
        page = FakePage(self.clock, state="challenge")
        on_ready = MagicMock()

        def assist(actual_page, authenticated, **kwargs):
            self.assertIs(actual_page, page)
            self.assertFalse(authenticated(page))
            self.assertEqual(kwargs["host"], "127.0.0.1")
            self.assertEqual(kwargs["port"], 9123)
            self.assertEqual(kwargs["timeout"], 7)
            self.assertIs(kwargs["on_ready"], on_ready)
            kwargs["on_ready"]("http://127.0.0.1:9123/#token=test")
            page.authenticate()
            self.assertTrue(authenticated(page))

        with patch("storypark_scraper.auth_assist.wait_for_user", side_effect=assist) as helper:
            self.browser(page, headless=True).authenticate(
                interactive=True, assist=True, host="127.0.0.1", port=9123,
                timeout_seconds=7, on_ready=on_ready,
            )
        helper.assert_called_once()
        on_ready.assert_called_once()

    def test_assistance_returning_without_positive_authentication_fails(self):
        page = FakePage(self.clock, state="challenge")
        with patch("storypark_scraper.auth_assist.wait_for_user") as helper:
            with self.assertRaisesRegex(AuthenticationRequired, "not verified"):
                self.browser(page).authenticate(interactive=True, assist=True)
        self.assertEqual(helper.call_args.kwargs["timeout"], 3)
        self.assertEqual(helper.call_args.kwargs["host"], "127.0.0.1")
        self.assertEqual(helper.call_args.kwargs["port"], 8000)

    def test_assistance_timeout_becomes_actionable_authentication_failure(self):
        page = FakePage(self.clock, state="challenge")
        with patch("storypark_scraper.auth_assist.wait_for_user", side_effect=TimeoutError("fixture timeout")):
            with self.assertRaisesRegex(AuthenticationRequired, "Timed out waiting for the owner") as caught:
                self.browser(page).authenticate(interactive=True, assist=True)
        self.assertIsInstance(caught.exception.__cause__, TimeoutError)

    def test_manual_login_can_succeed_without_stored_credentials(self):
        page = FakePage(self.clock)
        self.credentials.side_effect = CredentialError("No test credentials")
        self.clock.at(0.5, page.authenticate)
        self.browser(page, headless=False).authenticate(interactive=True)
        self.assertEqual(page.fills, [])
        self.assertEqual(page.clicks, [])
        self.assertEqual(page.state, "authenticated")

    def test_manual_login_timeout_is_bounded_without_real_sleep(self):
        page = FakePage(self.clock, state="challenge")
        with self.assertRaisesRegex(AuthenticationRequired, "Timed out waiting for the owner"):
            self.browser(page, headless=False).authenticate(interactive=True, timeout_seconds=2)
        self.assertGreaterEqual(self.clock.now, 2)
        self.assertLessEqual(self.clock.now, 3.5)

    def test_closing_manual_login_window_reports_clear_failure(self):
        page = FakePage(self.clock, state="challenge")
        self.clock.at(0.5, lambda: setattr(page, "closed", True))
        with self.assertRaisesRegex(AuthenticationRequired, "window was closed"):
            self.browser(page, headless=False).authenticate(interactive=True)
        self.assertEqual(page.fills, [])

    def test_window_closing_during_authentication_wait_has_safe_actionable_error(self):
        page = FakePage(self.clock)
        self.credentials.side_effect = CredentialError("No test credentials")

        def close_during_wait(milliseconds):
            page.closed = True
            raise RuntimeError("Target page closed while waiting; private-code-456")

        page.wait_for_timeout = MagicMock(side_effect=close_during_wait)
        try:
            self.browser(page, headless=False).authenticate(interactive=True)
        except AuthenticationRequired as exc:
            rendered = "".join(traceback.format_exception(exc))
            self.assertIn("window closed or became unavailable", rendered)
            self.assertIn("Retry the login command", rendered)
            self.assertNotIn("private-code-456", rendered)
            self.assertTrue(exc.__suppress_context__)
        else:
            self.fail("A window closed during polling must fail authentication")
        page.wait_for_timeout.assert_called_once_with(250)

    def test_invalid_manual_timeout_cannot_start_helper(self):
        page = FakePage(self.clock, state="challenge")
        with patch("storypark_scraper.auth_assist.wait_for_user") as helper:
            with self.assertRaisesRegex(ValueError, "greater than zero"):
                self.browser(page).authenticate(interactive=True, assist=True, timeout_seconds=0)
        helper.assert_not_called()

    def test_discovery_supports_current_and_legacy_menu_fixtures(self):
        for menu_kind, fixture, expected_code in (
            ("legacy", "menu.html", "child-1"),
            ("current", "menu_current.html", "5331262"),
        ):
            with self.subTest(menu_kind=menu_kind):
                self.config.storypark.child_allowlist = ["Alice"]
                page = FakePage(self.clock, state="authenticated", menu_kind=menu_kind,
                                html=(FIXTURES / fixture).read_text(encoding="utf-8"))
                children = self.browser(page).discover_children()
                self.assertEqual([(child.name, child.code) for child in children], [("Alice", expected_code)])
                self.assertEqual(children[0].stories_url, f"https://app.storypark.com/children/{expected_code}/stories")
                self.assertTrue(page.menu_open)
        self.credentials.assert_not_called()

    def test_discovery_uses_visible_menu_when_first_match_is_hidden(self):
        class MixedMenuPage(FakePage):
            def element_count(self, selector):
                if "[data-action='open-menu']" in selector and "button.v-btn" in selector:
                    return 2
                return super().element_count(selector)

            def element_visible(self, selector, index=0):
                if "[data-action='open-menu']" in selector and "button.v-btn" in selector:
                    return index == 1
                return super().element_visible(selector, index)

        page = MixedMenuPage(self.clock, state="authenticated", menu_kind="current",
                             html=(FIXTURES / "menu_current.html").read_text(encoding="utf-8"))
        self.config.storypark.child_allowlist = ["Alice"]
        children = self.browser(page).discover_children()
        self.assertEqual([(child.name, child.code) for child in children], [("Alice", "5331262")])
        self.assertTrue(page.menu_open)
        self.assertEqual(page.navigations, [LOGIN_URL])


class BrowserLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.config = make_config()
        self.page = FakePage(FakeClock(), state="authenticated")
        self.context = MagicMock()
        self.context.pages = [self.page]
        self.context.new_page.return_value = self.page
        self.probe = SimpleNamespace(version="147.0.7524.11", close=MagicMock())
        self.engine = MagicMock()
        self.engine.chromium.launch.return_value = self.probe
        self.engine.chromium.launch_persistent_context.return_value = self.context
        manager = MagicMock()
        manager.start.return_value = self.engine
        self.require = patch("storypark_scraper.browser._require_sync_playwright", return_value=lambda: manager)
        self.require.start()
        self.addCleanup(self.require.stop)
        self.provider = MagicMock()
        self.logger = logging.getLogger("storypark_scraper.test.browser.lifecycle")

    def test_profile_launch_selects_requested_mode_and_current_engine_user_agent(self):
        with patch("storypark_scraper.browser.platform.system", return_value="Windows"):
            with StoryparkBrowser(self.config, self.provider, self.logger, headless=False) as browser:
                self.assertIn("Chrome/147.0.0.0", browser.get_user_agent())
                self.assertNotIn("HeadlessChrome", browser.get_user_agent())
                self.assertIn("Windows NT 10.0; Win64; x64", browser.get_user_agent())
                options = self.engine.chromium.launch_persistent_context.call_args.kwargs
                self.assertEqual(options["user_data_dir"], str(self.config.paths.browser_profile))
                self.assertFalse(options["headless"])
                self.assertEqual(options["user_agent"], browser.get_user_agent())
                self.assertEqual(options["viewport"], {"width": 1280, "height": 900})
        self.probe.close.assert_called_once()
        self.context.close.assert_called_once()
        self.engine.stop.assert_called_once()
        self.page.set_default_timeout.assert_called_once_with(1000)

    def test_scheduled_browser_defaults_to_headless(self):
        with StoryparkBrowser(self.config, self.provider, self.logger):
            options = self.engine.chromium.launch_persistent_context.call_args.kwargs
            self.assertTrue(options["headless"])

    def test_explicit_user_agent_override_is_preserved_without_engine_probe(self):
        self.config.runtime.user_agent = "configured-browser-agent"
        with StoryparkBrowser(self.config, self.provider, self.logger) as browser:
            self.assertEqual(browser.get_user_agent(), "configured-browser-agent")
        self.engine.chromium.launch.assert_not_called()
        self.assertEqual(self.engine.chromium.launch_persistent_context.call_args.kwargs["user_agent"], "configured-browser-agent")

    def test_context_without_an_initial_tab_gets_a_page(self):
        self.context.pages = []
        with StoryparkBrowser(self.config, self.provider, self.logger):
            self.context.new_page.assert_called_once()

    def test_failed_context_launch_stops_playwright_and_closes_engine_probe(self):
        self.engine.chromium.launch_persistent_context.side_effect = RuntimeError("profile unavailable")
        browser = StoryparkBrowser(self.config, self.provider, self.logger)
        with self.assertRaisesRegex(RuntimeError, "profile unavailable"):
            browser.__enter__()
        self.probe.close.assert_called_once()
        self.context.close.assert_not_called()
        self.engine.stop.assert_called_once()
        self.assertIsNone(browser._playwright)

    def test_failed_page_setup_closes_context_and_stops_playwright(self):
        self.page.set_default_timeout.side_effect = RuntimeError("tab disconnected")
        browser = StoryparkBrowser(self.config, self.provider, self.logger)
        with self.assertRaisesRegex(RuntimeError, "tab disconnected"):
            browser.__enter__()
        self.context.close.assert_called_once()
        self.engine.stop.assert_called_once()
        self.assertIsNone(browser._context)
        self.assertIsNone(browser._playwright)


if __name__ == "__main__":
    unittest.main()
