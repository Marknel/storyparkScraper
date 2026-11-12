from __future__ import annotations

from typing import Callable
import platform
import re
import time
from urllib.parse import quote, urljoin, urlsplit

from storypark_scraper.config import AppConfig
from storypark_scraper.credentials import CredentialError
from storypark_scraper.models import ChildTarget, Credentials, StoryContent
from storypark_scraper.parsing import parse_child_targets_from_html, parse_story_from_html, parse_story_ids_from_html


MAIN_MENU_SELECTOR = (
    "[data-action='open-menu'], "
    "button.v-btn:has(span:text-is('Menu')):not(nav button)"
)
CHILD_MENU_SELECTOR = "[data-action='child-menu']"
CHILD_MENU_LABEL_SELECTOR = "[data-action='open-child-menu'], .v-list-item__title, .v-list-item-title"
CHILD_STORIES_LINK_SELECTOR = "a[href*='/children/'][href$='/stories']"
USERNAME_SELECTOR = "#user_email"
PASSWORD_SELECTOR = "#user_password"
SUBMIT_SELECTOR = "button[data-action='signin']"


class AuthenticationRequired(RuntimeError):
    """The account owner must renew the persistent browser session."""


def build_user_agent(browser_version: str) -> str:
    """Match Chromium in both headed/headless modes (adapted from upstream 1.1)."""
    system = platform.system()
    os_part = {
        "Windows": "Windows NT 10.0; Win64; x64",
        "Darwin": "Macintosh; Intel Mac OS X 10_15_7",
    }.get(system, "X11; Linux x86_64")
    major = browser_version.split(".")[0]
    return (
        f"Mozilla/5.0 ({os_part}) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{major}.0.0.0 Safari/537.36"
    )


class StoryparkBrowser:
    def __init__(
        self,
        config: AppConfig,
        credential_provider: Callable[[], Credentials],
        logger,
        *,
        headless: bool = True,
    ):
        self._config = config
        self._credential_provider = credential_provider
        self._logger = logger
        self._playwright = None
        self._context = None
        self._page = None
        self._headless = headless
        self._user_agent = config.runtime.user_agent

    def __enter__(self):
        sync_playwright = _require_sync_playwright()
        self._playwright = sync_playwright().start()
        try:
            if not self._user_agent:
                # Query the installed engine instead of maintaining a stale UA constant.
                probe = self._playwright.chromium.launch(headless=True)
                try:
                    self._user_agent = build_user_agent(probe.version)
                finally:
                    probe.close()
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self._config.paths.browser_profile),
                headless=self._headless,
                user_agent=self._user_agent,
                viewport={"width": 1280, "height": 900},
            )
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
            self._page.set_default_timeout(self._config.runtime.page_timeout_ms)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def get_user_agent(self) -> str:
        return self._user_agent

    def get_cookies(self) -> list[dict]:
        if self._context is not None:
            return self._context.cookies()
        return []

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._context is not None:
                self._context.close()
        finally:
            self._context = None
            if self._playwright is not None:
                self._playwright.stop()
                self._playwright = None

    def authenticate(
        self, *, interactive: bool = False, timeout_seconds: int | None = None,
        assist: bool = False, host: str | None = None, port: int | None = None,
        on_ready: Callable[[str], None] | None = None,
    ) -> None:
        """Establish a verified session; only explicit login permits human interaction."""
        self._ensure_authenticated(
            interactive=interactive,
            timeout_seconds=timeout_seconds,
            assist=assist,
            host=host,
            port=port,
            on_ready=on_ready,
        )

    def discover_children(self) -> list[ChildTarget]:
        self._ensure_authenticated()
        menu_button = self._visible_element(MAIN_MENU_SELECTOR)
        if menu_button is None:
            raise AuthenticationRequired("Storypark's authenticated menu is unavailable; renew the session with the login command.")
        menu_button.wait_for(state="visible", timeout=self._config.runtime.page_timeout_ms)
        menu_button.click()
        try:
            self._page.locator(CHILD_MENU_SELECTOR).first.wait_for(
                state="visible", timeout=5000
            )
        except Exception:
            pass

        children = self._discover_children_from_menu()
        if not children:
            children = parse_child_targets_from_html(
                self._page.content(),
                self._config.storypark.stories_url_template,
                allowlist=self._config.storypark.child_allowlist,
            )
        if not children:
            raise RuntimeError("No Storypark children were discovered with the current selectors/session")
        return children

    def collect_story_ids(self, child: ChildTarget, *, full_scan: bool, max_story_ids: int | None = None) -> list[str]:
        self._ensure_authenticated()
        self._page.goto(child.stories_url)
        self._page.wait_for_timeout(1000)

        story_ids: list[str] = []
        last_count = -1
        stable_rounds = 0
        iteration = 0
        max_iterations = 50 if full_scan else max(1, self._config.runtime.recent_scroll_iterations)
        target_stable_rounds = max(1, self._config.runtime.scroll_stable_rounds)

        while iteration < max_iterations:
            story_ids = parse_story_ids_from_html(self._page.content())
            current_count = len(story_ids)
            if max_story_ids is not None and current_count >= max_story_ids:
                return story_ids[:max_story_ids]
            if current_count == last_count:
                stable_rounds += 1
            else:
                stable_rounds = 0
            if stable_rounds >= target_stable_rounds:
                break
            last_count = current_count
            iteration += 1
            if iteration >= max_iterations:
                break
            self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            self._page.wait_for_timeout(self._config.runtime.scroll_pause_ms)
        return story_ids[:max_story_ids] if max_story_ids is not None else story_ids

    def fetch_story(self, child: ChildTarget, story_id: str) -> StoryContent:
        self._ensure_authenticated()
        source_url = _build_story_detail_url(child, story_id)
        self._page.goto(source_url)
        self._page.wait_for_load_state("networkidle", timeout=self._config.runtime.network_idle_timeout_ms)
        return parse_story_from_html(self._page.content(), story_id, child.name, self._page.url)

    def _discover_children_from_menu(self) -> list[ChildTarget]:
        allowed = {item.casefold() for item in self._config.storypark.child_allowlist if item}
        children: list[ChildTarget] = []
        seen_codes: set[str] = set()
        groups = self._page.locator(CHILD_MENU_SELECTOR)

        for index in range(groups.count()):
            group = groups.nth(index)
            label_locator = group.locator(CHILD_MENU_LABEL_SELECTOR).first
            label = label_locator.inner_text().strip() if label_locator.count() > 0 else ""
            if not label:
                continue
            if allowed and label.casefold() not in allowed:
                continue

            stories_links = group.locator(CHILD_STORIES_LINK_SELECTOR)
            if stories_links.count() == 0:
                header = group.locator("[data-action='open-child-menu'], .v-list-group__header").first
                if header.count() > 0:
                    try:
                        header.click()
                        self._page.wait_for_timeout(500)
                    except Exception as exc:
                        self._logger.warning(
                            "Failed to expand child menu",
                            extra={"child_name": label, "error": str(exc)},
                        )
                stories_links = group.locator(CHILD_STORIES_LINK_SELECTOR)
            if stories_links.count() == 0:
                continue

            href = stories_links.first.get_attribute("href") or ""
            story_url = urljoin(self._page.url, href)
            code = _extract_child_code(story_url)
            if not code or code in seen_codes:
                continue
            children.append(
                ChildTarget(
                    name=label,
                    code=code,
                    stories_url=story_url,
                )
            )
            seen_codes.add(code)
        return children

    def _ensure_authenticated(
        self, *, interactive: bool = False, timeout_seconds: int | None = None,
        assist: bool = False, host: str | None = None, port: int | None = None,
        on_ready: Callable[[str], None] | None = None,
    ) -> None:
        self._page.goto(self._config.storypark.login_url)
        if self._wait_for_login_state():
            return
        if self._has_login_fields():
            try:
                credentials = self._credential_provider()
            except CredentialError:
                if not interactive:
                    raise
            else:
                self._logger.info("Authenticating to Storypark", extra={"credential_source": credentials.source})
                try:
                    form_timeout = min(5000, self._config.runtime.page_timeout_ms)
                    self._visible_element(USERNAME_SELECTOR).fill(credentials.username, timeout=form_timeout)
                    self._visible_element(PASSWORD_SELECTOR).fill(credentials.password, timeout=form_timeout)
                    self._visible_element(SUBMIT_SELECTOR).click(timeout=form_timeout)
                except Exception:
                    # Playwright call logs may contain a fill/type argument. Do not
                    # allow credentials into tracebacks, telemetry or status JSON.
                    if not interactive:
                        raise AuthenticationRequired(
                            "The Storypark sign-in form changed or was unavailable. Renew the session with the login command."
                        ) from None
                    # A CAPTCHA can deliberately disable submit. The owner must
                    # still be able to reach the assist view and finish the form.
                else:
                    if self._wait_until_authenticated(15):
                        return
        if not interactive:
            raise AuthenticationRequired(
                "Storypark sign-in needs the account owner (CAPTCHA, MFA or an expired session). "
                "Run 'python -m storypark_scraper --config <config.yaml> login' on the scraper host "
                "under the scheduled-task account, then retry the sync. Use 'login --headless' on a headless host."
            )
        timeout = timeout_seconds if timeout_seconds is not None else self._config.runtime.auth_timeout_seconds
        if timeout <= 0:
            raise ValueError("Authentication timeout must be greater than zero")
        if assist:
            from storypark_scraper.auth_assist import wait_for_user
            try:
                wait_for_user(
                    self._page, lambda page: self._is_authenticated(),
                    host=host if host is not None else self._config.runtime.auth_assist_host,
                    port=port if port is not None else self._config.runtime.auth_assist_port,
                    timeout=timeout, on_ready=on_ready,
                )
            except TimeoutError as exc:
                raise AuthenticationRequired("Timed out waiting for the owner to complete Storypark sign-in.") from exc
            except AuthenticationRequired:
                raise
            except Exception:
                raise AuthenticationRequired(
                    "Storypark login assistance stopped. Retry the login command and complete the verification."
                ) from None
        elif not self._wait_until_authenticated(timeout):
            raise AuthenticationRequired("Timed out waiting for the owner to complete Storypark sign-in.")
        if not self._is_authenticated():
            raise AuthenticationRequired("Storypark sign-in was not verified; retry the login command.")

    def _same_origin(self) -> bool:
        expected = urlsplit(self._config.storypark.login_url)
        current = urlsplit(self._page.url)
        return (current.scheme, current.netloc) == (expected.scheme, expected.netloc)

    def _has_login_fields(self) -> bool:
        return self._same_origin() and self._visible(USERNAME_SELECTOR) and self._visible(PASSWORD_SELECTOR)

    def _visible(self, selector: str) -> bool:
        return self._visible_element(selector) is not None

    def _visible_element(self, selector: str):
        try:
            elements = self._page.locator(selector)
            for index in range(elements.count()):
                element = elements.nth(index)
                if element.is_visible():
                    return element
        except Exception:
            pass
        return None

    def _is_authenticated(self) -> bool:
        if self._page.is_closed():
            raise AuthenticationRequired("The Storypark login window was closed before sign-in completed.")
        return (
            self._same_origin()
            and not urlsplit(self._page.url).path.startswith("/users/")
            and self._visible(MAIN_MENU_SELECTOR)
        )

    def _wait_until_authenticated(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            if self._is_authenticated():
                return True
            if time.monotonic() >= deadline:
                return False
            self._auth_pause()

    def _auth_pause(self) -> None:
        try:
            self._page.wait_for_timeout(250)
        except Exception:
            raise AuthenticationRequired(
                "The Storypark sign-in window closed or became unavailable. Retry the login command."
            ) from None

    def _wait_for_login_state(self) -> bool:
        deadline = time.monotonic() + min(10, self._config.runtime.page_timeout_ms / 1000)
        while True:
            if self._is_authenticated():
                return True
            if self._has_login_fields() or time.monotonic() >= deadline:
                return False
            self._auth_pause()

    def _is_login_page(self) -> bool:
        return not self._is_authenticated()


def _build_story_detail_url(child: ChildTarget, story_id: str) -> str:
    back = quote(child.stories_url, safe='')
    return f"https://app.storypark.com/stories/{story_id}/spa_show?back={back}"


def _extract_child_code(story_url: str) -> str:
    match = re.search(r"/children/([^/]+)/stories/?$", story_url)
    return match.group(1) if match else ""


def _require_sync_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "playwright is required at runtime. Install dependencies and run "
            "'python -m playwright install chromium'."
        ) from exc
    return sync_playwright
