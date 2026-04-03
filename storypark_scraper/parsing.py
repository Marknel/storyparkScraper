from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from typing import Callable, Iterable, Optional
import re
from urllib.parse import urljoin

from storypark_scraper.models import ChildTarget, StoryAsset, StoryContent


VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


@dataclass(slots=True)
class HtmlNode:
    tag: str
    attrs: dict[str, str]
    parent: Optional["HtmlNode"] = None
    children: list["HtmlNode"] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)

    def attr(self, key: str, default: str = "") -> str:
        return self.attrs.get(key, default)

    def classes(self) -> set[str]:
        return {item for item in self.attr("class").split() if item}

    def text_content(self) -> str:
        parts = list(self.text_parts)
        for child in self.children:
            child_text = child.text_content()
            if child_text:
                parts.append(child_text)
        return _normalize_whitespace(" ".join(parts))


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = HtmlNode(tag="document", attrs={})
        self.stack: list[HtmlNode] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = HtmlNode(tag=tag, attrs={key: value or "" for key, value in attrs}, parent=self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = HtmlNode(tag=tag, attrs={key: value or "" for key, value in attrs}, parent=self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        while len(self.stack) > 1:
            node = self.stack.pop()
            if node.tag == tag:
                return

    def handle_data(self, data: str) -> None:
        text = _normalize_whitespace(data)
        if text:
            self.stack[-1].text_parts.append(text)


def parse_child_targets_from_html(
    html: str,
    stories_url_template: str,
    allowlist: Optional[Iterable[str]] = None,
) -> list[ChildTarget]:
    root = _parse_html(html)
    allowed = {item.casefold() for item in (allowlist or []) if item}

    children = _parse_current_child_targets(root, stories_url_template, allowed)
    if children:
        return children
    return _parse_legacy_child_targets(root, stories_url_template, allowed)


def _parse_current_child_targets(
    root: HtmlNode,
    stories_url_template: str,
    allowed: set[str],
) -> list[ChildTarget]:
    children: list[ChildTarget] = []
    seen_codes: set[str] = set()
    for node in iter_nodes(root):
        if node.attr("data-action") != "child-menu":
            continue
        label = _extract_current_child_label(node)
        story_link = _find_first(node, _is_child_stories_link)
        if not label or story_link is None:
            continue
        code = _extract_child_code_from_href(story_link.attr("href"))
        if not code or code in seen_codes:
            continue
        if allowed and label.casefold() not in allowed:
            continue
        children.append(
            ChildTarget(
                name=label,
                code=code,
                stories_url=_normalize_story_url(story_link.attr("href"), stories_url_template, code),
            )
        )
        seen_codes.add(code)
    return children


def _parse_legacy_child_targets(
    root: HtmlNode,
    stories_url_template: str,
    allowed: set[str],
) -> list[ChildTarget]:
    children: list[ChildTarget] = []
    seen_codes: set[str] = set()
    for node in iter_nodes(root):
        href = node.attr("href")
        if node.tag != "a" or node.attr("data-toggle") != "menu" or not href.startswith("#child-navigation-"):
            continue
        label = _find_descendant_text(
            node,
            lambda child: child.tag == "span" and "sp-c-nav-drawer__link-label" in child.classes(),
        )
        code = href.removeprefix("#child-navigation-")
        if not code or not label or code in seen_codes:
            continue
        if allowed and label.casefold() not in allowed:
            continue
        children.append(
            ChildTarget(
                name=label,
                code=code,
                stories_url=stories_url_template.format(child_code=code),
            )
        )
        seen_codes.add(code)
    return children


def _extract_current_child_label(node: HtmlNode) -> str:
    for predicate in [
        lambda child: child.attr("data-action") == "open-child-menu",
        lambda child: child.tag == "button" and "v-list-item-title" in child.classes(),
        lambda child: child.tag == "div" and "v-list-item-title" in child.classes(),
        lambda child: child.tag == "div" and "v-list-item__title" in child.classes(),
    ]:
        label = _find_descendant_text(node, predicate)
        if label:
            return label
    return ""


def _is_child_stories_link(node: HtmlNode) -> bool:
    if node.tag != "a":
        return False
    href = node.attr("href")
    return "/children/" in href and href.endswith("/stories")


def _extract_child_code_from_href(href: str) -> str:
    match = re.search(r"/children/([^/]+)/stories/?$", href)
    return match.group(1) if match else ""


def _normalize_story_url(href: str, stories_url_template: str, child_code: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return urljoin(stories_url_template.format(child_code=child_code), href)


def parse_story_ids_from_html(html: str) -> list[str]:
    root = _parse_html(html)
    story_ids: list[str] = []
    seen: set[str] = set()
    for node in iter_nodes(root):
        story_id = node.attr("storyparkid")
        if not story_id or story_id in seen:
            continue
        story_ids.append(story_id)
        seen.add(story_id)
    return story_ids


def parse_story_from_html(html: str, story_id: str, child_name: str, source_url: str) -> StoryContent:
    root = _parse_html(html)
    title = _first_non_empty_text(
        root,
        [
            lambda node: node.tag == "h1",
            lambda node: node.tag == "h2",
            lambda node: node.attr("data-testid") == "story-title",
        ],
    )
    published_text = _extract_published_text(root)
    published_at = _normalize_story_date(published_text)
    author = _extract_author(root)
    content_type = _extract_content_type(root)
    body = _extract_body_text(root, title, published_text, author)
    assets = _extract_assets(root, source_url)

    return StoryContent(
        story_id=story_id,
        child_name=child_name,
        source_url=source_url,
        title=title,
        body=body,
        published_at=published_at,
        published_text=published_text,
        author=author,
        content_type=content_type,
        assets=assets,
    )


def iter_nodes(root: HtmlNode) -> Iterable[HtmlNode]:
    for child in root.children:
        yield child
        yield from iter_nodes(child)


def _parse_html(html: str) -> HtmlNode:
    parser = _TreeBuilder()
    parser.feed(html)
    return parser.root


def _extract_published_text(root: HtmlNode) -> str:
    date_box = _find_first(root, lambda node: "date-box" in node.classes())
    if date_box:
        return date_box.text_content()

    author_box = _find_first(root, lambda node: "author-box" in node.classes())
    if author_box:
        details_row = _find_first(
            author_box,
            lambda node: node.tag == "div" and "justify-space-between" in node.classes() and "align-center" in node.classes(),
        )
        if details_row:
            for child in details_row.children:
                if "flex-shrink-0" not in child.classes():
                    continue
                text = child.text_content()
                if text and text.casefold() != "story date":
                    return text
        for node in iter_nodes(author_box):
            text = node.text_content()
            if _looks_like_story_date(text):
                return text

    return _first_non_empty_text(
        root,
        [
            lambda node: {"text-h6", "grey2--text"}.issubset(node.classes()) and _looks_like_story_date(node.text_content()),
            lambda node: node.attr("data-testid") == "story-date",
        ],
    )


def _extract_author(root: HtmlNode) -> str:
    author_box = _find_first(root, lambda node: "author-box" in node.classes())
    if author_box:
        details_row = _find_first(
            author_box,
            lambda node: node.tag == "div" and "justify-space-between" in node.classes() and "align-center" in node.classes(),
        )
        if details_row:
            for child in details_row.children:
                if "flex-shrink-0" in child.classes():
                    continue
                for node in iter_nodes(child):
                    if node.tag != "div":
                        continue
                    text = node.text_content()
                    if not text or text.casefold() in {"author", "story date"}:
                        continue
                    if "font-weight-regular" in node.classes() or "text-h6" in node.classes():
                        return text

    return _first_non_empty_text(
        root,
        [
            lambda node: node.attr("rel") == "author",
            lambda node: node.tag == "a"
            and any(segment in node.attr("href") for segment in ["/educators/", "/parents/"]),
        ],
    )


def _extract_content_type(root: HtmlNode) -> str:
    explicit = _find_first(root, lambda node: bool(node.attr("data-type")))
    if explicit:
        return explicit.attr("data-type")
    badge = _find_first(root, lambda node: "story-type" in node.classes())
    if badge:
        return badge.text_content() or "story"
    return "story"


def _extract_body_text(root: HtmlNode, title: str, published_text: str, author: str) -> str:
    paragraphs: list[str] = []
    seen: set[str] = set()
    for node in iter_nodes(root):
        if node.tag != "p":
            continue
        if _has_ancestor(node, lambda ancestor: "date-box" in ancestor.classes()):
            continue
        text = node.text_content()
        if not text or text in {title, published_text, author} or text in seen:
            continue
        paragraphs.append(text)
        seen.add(text)

    if not paragraphs:
        for node in iter_nodes(root):
            if node.tag != "div":
                continue
            if not {"trix-content", "story-body"} & node.classes():
                continue
            text = node.text_content()
            if text and text not in seen:
                paragraphs.append(text)
                seen.add(text)

    return "\n\n".join(paragraphs)


def _extract_assets(root: HtmlNode, base_url: str) -> list[StoryAsset]:
    preferred_images: list[StoryAsset] = []
    fallback_images: list[StoryAsset] = []
    videos: list[StoryAsset] = []
    seen_urls: set[str] = set()

    for node in iter_nodes(root):
        if node.tag == "img":
            source = node.attr("src")
            if not source:
                continue
            if any(token in node.classes() for token in {"avatar", "icon", "logo"}):
                continue
            asset = _build_asset(source, base_url, seen_urls, kind="image")
            if asset is None:
                continue
            if node.attr("data-action") == "zoom-media" or _has_ancestor(
                node,
                lambda ancestor: "image-container" in ancestor.classes(),
            ):
                preferred_images.append(asset)
            else:
                fallback_images.append(asset)
            continue

        if node.tag == "video":
            asset = _build_asset(node.attr("src"), base_url, seen_urls, kind="video")
            if asset is not None:
                videos.append(asset)
            continue

        if node.tag == "source":
            source_type = node.attr("type").casefold()
            if source_type.startswith("video/") or _has_ancestor(node, lambda ancestor: ancestor.tag == "video"):
                asset = _build_asset(node.attr("src"), base_url, seen_urls, kind="video")
                if asset is not None:
                    videos.append(asset)

    images = preferred_images or fallback_images
    return images + videos


def _build_asset(source: str, base_url: str, seen_urls: set[str], *, kind: str) -> StoryAsset | None:
    if not source:
        return None
    absolute = urljoin(base_url, source)
    if absolute in seen_urls:
        return None
    seen_urls.add(absolute)
    return StoryAsset(source_url=absolute, kind=kind)


def _find_first(root: HtmlNode, predicate: Callable[[HtmlNode], bool]) -> Optional[HtmlNode]:
    for node in iter_nodes(root):
        if predicate(node):
            return node
    return None


def _find_descendant_text(root: HtmlNode, predicate: Callable[[HtmlNode], bool]) -> str:
    node = _find_first(root, predicate)
    return node.text_content() if node else ""


def _first_non_empty_text(root: HtmlNode, predicates: list[Callable[[HtmlNode], bool]]) -> str:
    for predicate in predicates:
        node = _find_first(root, predicate)
        if node:
            text = node.text_content()
            if text:
                return text
    return ""


def _has_ancestor(node: HtmlNode, predicate: Callable[[HtmlNode], bool]) -> bool:
    ancestor = node.parent
    while ancestor:
        if predicate(ancestor):
            return True
        ancestor = ancestor.parent
    return False


def _looks_like_story_date(value: str) -> bool:
    normalized = _normalize_story_date(value)
    if not normalized:
        return False
    return normalized != value or bool(re.search(r"\b\d{4}\b", value))


def _normalize_story_date(value: str) -> str | None:
    cleaned = _normalize_whitespace(value)
    if not cleaned:
        return None
    for fmt in [
        "%d %B %Y",
        "%d %b %Y",
        "%B %d, %Y",
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d %B %Y %I:%M%p",
        "%d %B %Y %H:%M",
    ]:
        try:
            parsed = datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
        if parsed.hour or parsed.minute or parsed.second:
            return parsed.isoformat()
        return parsed.date().isoformat()
    iso_match = re.search(r"\d{4}-\d{2}-\d{2}(?:[T ][0-9:\-+]+)?", cleaned)
    if iso_match:
        candidate = iso_match.group(0).replace(" ", "T")
        try:
            return datetime.fromisoformat(candidate).isoformat()
        except ValueError:
            return candidate
    return cleaned


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
