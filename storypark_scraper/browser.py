from __future__ import annotations

from typing import Callable
import re
from urllib.parse import quote, urljoin

from storypark_scraper.config import AppConfig
from storypark_scraper.models import ChildTarget, Credentials, StoryContent
from storypark_scraper.parsing import parse_child_targets_from_html, parse_story_from_html, parse_story_ids_from_html


LOGIN_TITLE_SNIPPET = "Log in"
MAIN_MENU_SELECTOR = "[data-action='open-menu']"
CHILD_MENU_SELECTOR = "[data-action='child-menu']"
CHILD_MENU_LABEL_SELECTOR = "[data-action='open-child-menu'], .v-list-item__title, .v-list-item-title"
CHILD_STORIES_LINK_SELECTOR = "a[href*='/children/'][href$='/stories']"
USERNAME_SELECTOR = "#user_email"
PASSWORD_SELECTOR = "#user_password"
SUBMIT_SELECTOR = ".sp-c-btn.sp-c-btn--med.sp-c-primary-btn.sp-c-primary-btn--lg.sp-u-padding-horizontal--med[data-action='signin']"


class StoryparkBrowser:
    def __init__(
        self,
        config: AppConfig,
        credential_provider: Callable[[], Credentials],
        logger,
    ):
        self._config = config
        self._credential_provider = credential_provider
        self._logger = logger
        self._playwright = None
        self._context = None
        self._page = None

    def __enter__(self):
        sync_playwright = _require_sync_playwright()
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self._config.paths.browser_profile),
            headless=True,
            user_agent=self._config.runtime.user_agent,
        )
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._page.set_default_timeout(self._config.runtime.page_timeout_ms)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._context is not None:
            self._context.close()
        if self._playwright is not None:
            self._playwright.stop()

    def discover_children(self) -> list[ChildTarget]:
        self._ensure_authenticated()
        self._page.goto(self._config.storypark.login_url)
        self._page.locator(MAIN_MENU_SELECTOR).first.click()
        self._page.wait_for_timeout(1500)

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

        while iteration < max_iterations:
            story_ids = parse_story_ids_from_html(self._page.content())
            current_count = len(story_ids)
            if max_story_ids is not None and current_count >= max_story_ids:
                return story_ids[:max_story_ids]
            if current_count == last_count:
                stable_rounds += 1
            else:
                stable_rounds = 0
            if stable_rounds >= 1:
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
                header = group.locator("[data-action='open-child-menu']").first
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

    def _ensure_authenticated(self) -> None:
        self._page.goto(self._config.storypark.login_url)
        if not self._is_login_page():
            return
        credentials = self._credential_provider()
        self._logger.info("Authenticating to Storypark", extra={"credential_source": credentials.source})
        self._page.fill(USERNAME_SELECTOR, credentials.username)
        self._page.fill(PASSWORD_SELECTOR, credentials.password)
        self._page.click(SUBMIT_SELECTOR)
        self._page.wait_for_timeout(2000)
        if self._is_login_page():
            raise RuntimeError("Storypark login did not complete successfully")

    def _is_login_page(self) -> bool:
        try:
            title = self._page.title()
        except Exception:
            title = ""
        if LOGIN_TITLE_SNIPPET.lower() in title.lower():
            return True
        try:
            return self._page.locator(USERNAME_SELECTOR).count() > 0
        except Exception:
            return False


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
