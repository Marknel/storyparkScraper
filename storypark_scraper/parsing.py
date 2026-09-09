from __future__ import annotations

from datetime import datetime
import re
from typing import Iterable, Optional
from urllib.parse import urljoin
from bs4 import BeautifulSoup, Tag

from storypark_scraper.models import ChildTarget, StoryAsset, StoryContent


def _get_classes(tag: Tag) -> set[str]:
    cls = tag.get("class")
    if isinstance(cls, list):
        return set(cls)
    if isinstance(cls, str):
        return set(cls.split())
    return set()


def _tag_text(tag: Optional[Tag]) -> str:
    if tag is None:
        return ""
    return _normalize_whitespace(tag.get_text(" "))


def parse_child_targets_from_html(
    html: str,
    stories_url_template: str,
    allowlist: Optional[Iterable[str]] = None,
) -> list[ChildTarget]:
    soup = BeautifulSoup(html, "html.parser")
    allowed = {item.casefold() for item in (allowlist or []) if item}
    children = _parse_current_child_targets(soup, stories_url_template, allowed)
    if children:
        return children
    return _parse_legacy_child_targets(soup, stories_url_template, allowed)


def _parse_current_child_targets(
    soup: BeautifulSoup,
    stories_url_template: str,
    allowed: set[str],
) -> list[ChildTarget]:
    children: list[ChildTarget] = []
    seen_codes: set[str] = set()
    for node in soup.find_all(attrs={"data-action": "child-menu"}):
        label = _extract_current_child_label(node)
        story_link = None
        for a in node.find_all("a"):
            href = a.get("href", "")
            if "/children/" in href and href.endswith("/stories"):
                story_link = a
                break
        if not label or story_link is None:
            continue
        href = story_link.get("href", "")
        code = _extract_child_code_from_href(href)
        if not code or code in seen_codes:
            continue
        if allowed and label.casefold() not in allowed:
            continue
        children.append(
            ChildTarget(
                name=label,
                code=code,
                stories_url=_normalize_story_url(href, stories_url_template, code),
            )
        )
        seen_codes.add(code)
    return children


def _parse_legacy_child_targets(
    soup: BeautifulSoup,
    stories_url_template: str,
    allowed: set[str],
) -> list[ChildTarget]:
    children: list[ChildTarget] = []
    seen_codes: set[str] = set()
    for node in soup.find_all("a", attrs={"data-toggle": "menu"}):
        href = node.get("href", "")
        if not href.startswith("#child-navigation-"):
            continue
        label_span = node.find("span", class_="sp-c-nav-drawer__link-label")
        label = _tag_text(label_span)
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


def _extract_current_child_label(node: Tag) -> str:
    for target in node.find_all(attrs={"data-action": "open-child-menu"}):
        text = _tag_text(target)
        if text:
            return text
    for cls in ("v-list-item-title", "v-list-item__title"):
        for target in node.find_all(class_=cls):
            text = _tag_text(target)
            if text:
                return text
    return ""


def _extract_child_code_from_href(href: str) -> str:
    match = re.search(r"/children/([^/]+)/stories/?$", href)
    return match.group(1) if match else ""


def _normalize_story_url(href: str, stories_url_template: str, child_code: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return urljoin(stories_url_template.format(child_code=child_code), href)


def parse_story_ids_from_html(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    story_ids: list[str] = []
    seen: set[str] = set()
    for node in soup.find_all(attrs={"storyparkid": True}):
        story_id = node["storyparkid"]
        if not story_id or story_id in seen:
            continue
        story_ids.append(story_id)
        seen.add(story_id)
    return story_ids


def parse_story_from_html(html: str, story_id: str, child_name: str, source_url: str) -> StoryContent:
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    for tag in soup.find_all(["h1", "h2"]):
        t = _tag_text(tag)
        if t:
            title = t
            break
    if not title:
        target = soup.find(attrs={"data-testid": "story-title"})
        if target:
            title = _tag_text(target)

    published_text = _extract_published_text(soup)
    published_at = _normalize_story_date(published_text)
    author = _extract_author(soup)
    included_children = _extract_included_children(soup)
    content_type = _extract_content_type(soup)
    body = _extract_body_text(soup, title, published_text, author)
    assets = _extract_assets(soup, source_url)

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
        included_children=included_children,
    )


def _extract_published_text(soup: BeautifulSoup) -> str:
    date_box = soup.find(class_="date-box")
    if date_box:
        return _tag_text(date_box)

    author_box = soup.find(class_="author-box")
    if author_box:
        details_row = author_box.find(
            "div",
            class_=lambda c: c and "justify-space-between" in c and "align-center" in c,
        )
        if details_row:
            for child in details_row.find_all("div", class_=lambda c: c and "flex-shrink-0" in c):
                text = _tag_text(child)
                if text and text.casefold() != "story date":
                    return text
        for tag in author_box.find_all(True):
            text = _tag_text(tag)
            if _looks_like_story_date(text):
                return text

    for tag in soup.find_all(class_=lambda c: c and "text-h6" in c and "grey2--text" in c):
        text = _tag_text(tag)
        if _looks_like_story_date(text):
            return text

    test_id = soup.find(attrs={"data-testid": "story-date"})
    if test_id:
        return _tag_text(test_id)
    return ""


def _extract_author(soup: BeautifulSoup) -> str:
    author_box = soup.find(class_="author-box")
    if author_box:
        details_row = author_box.find(
            "div",
            class_=lambda c: c and "justify-space-between" in c and "align-center" in c,
        )
        if details_row:
            for child in details_row.find_all("div", recursive=False):
                if child.get("class") and "flex-shrink-0" in child["class"]:
                    continue
                for node in child.find_all("div"):
                    text = _tag_text(node)
                    if not text or text.casefold() in {"author", "story date"}:
                        continue
                    cls = _get_classes(node)
                    if "font-weight-regular" in cls or "text-h6" in cls:
                        return text

    rel_author = soup.find(attrs={"rel": "author"})
    if rel_author:
        text = _tag_text(rel_author)
        if text:
            return text

    for a in soup.find_all("a"):
        href = a.get("href", "")
        if any(s in href for s in ["/educators/", "/parents/"]):
            text = _tag_text(a)
            if text:
                return text
    return ""


def _extract_included_children(soup: BeautifulSoup) -> list[str]:
    explicit_names: list[str] = []
    seen: set[str] = set()
    for node in soup.find_all(class_="child-name-link"):
        text = _tag_text(node)
        if not text or text in seen:
            continue
        explicit_names.append(text)
        seen.add(text)
    if explicit_names:
        return explicit_names

    for node in soup.find_all(attrs={"title": True}):
        title = node["title"]
        if title.startswith("Individual Story for ") or title.startswith("Group Story for "):
            match = re.match(r"^(?:Individual|Group) Story for (.+)$", title)
            if match:
                names: list[str] = []
                for item in match.group(1).split(","):
                    name = _normalize_whitespace(item)
                    if name and name not in seen:
                        names.append(name)
                        seen.add(name)
                return names
    return []


def _extract_content_type(soup: BeautifulSoup) -> str:
    explicit = soup.find(attrs={"data-type": True})
    if explicit:
        return explicit["data-type"]
    badge = soup.find(class_="story-type")
    if badge:
        return _tag_text(badge) or "story"
    return "story"


def _extract_body_text(soup: BeautifulSoup, title: str, published_text: str, author: str) -> str:
    paragraphs: list[str] = []
    seen: set[str] = set()

    for p in soup.find_all("p"):
        if p.find_parent(class_="date-box"):
            continue
        text = _tag_text(p)
        if not text or text in {title, published_text, author} or text in seen:
            continue
        paragraphs.append(text)
        seen.add(text)

    if not paragraphs:
        for div in soup.find_all("div"):
            cls = _get_classes(div)
            if {"trix-content", "story-body"} & cls:
                text = _tag_text(div)
                if text and text not in seen:
                    paragraphs.append(text)
                    seen.add(text)

    return "\n\n".join(paragraphs)


def _extract_assets(soup: BeautifulSoup, base_url: str) -> list[StoryAsset]:
    preferred_images: list[StoryAsset] = []
    fallback_images: list[StoryAsset] = []
    videos: list[StoryAsset] = []
    seen_urls: set[str] = set()

    for img in soup.find_all("img"):
        src = img.get("src")
        if not src:
            continue
        cls = _get_classes(img)
        if any(token in cls for token in {"avatar", "icon", "logo"}):
            continue
        asset = _build_asset(src, base_url, seen_urls, kind="image")
        if asset is None:
            continue
        if img.get("data-action") == "zoom-media" or img.find_parent(class_="image-container"):
            preferred_images.append(asset)
        else:
            fallback_images.append(asset)

    for video in soup.find_all("video"):
        asset = _build_asset(video.get("src"), base_url, seen_urls, kind="video")
        if asset is not None:
            videos.append(asset)

    for source in soup.find_all("source"):
        stype = source.get("type", "").casefold()
        if stype.startswith("video/") or source.find_parent("video"):
            asset = _build_asset(source.get("src"), base_url, seen_urls, kind="video")
            if asset is not None:
                videos.append(asset)

    images = preferred_images or fallback_images
    return images + videos


def _build_asset(source: Optional[str], base_url: str, seen_urls: set[str], *, kind: str) -> Optional[StoryAsset]:
    if not source:
        return None
    absolute = urljoin(base_url, source)
    if absolute in seen_urls:
        return None
    seen_urls.add(absolute)
    return StoryAsset(source_url=absolute, kind=kind)


def _looks_like_story_date(value: str) -> bool:
    normalized = _normalize_story_date(value)
    if not normalized:
        return False
    return normalized != value or bool(re.search(r"\b\d{4}\b", value))


def _normalize_story_date(value: str) -> Optional[str]:
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

