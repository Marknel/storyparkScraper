from __future__ import annotations

from pathlib import Path
import mimetypes
import os
from urllib.parse import urlparse

from storypark_scraper.models import StoryAsset


_CONTENT_TYPE_EXTENSIONS = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/ogg": ".ogv",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-m4v": ".m4v",
    "video/x-msvideo": ".avi",
}


class AssetDownloader:
    def __init__(self, timeout_seconds: int, user_agent: str):
        self._timeout_seconds = timeout_seconds
        self._user_agent = user_agent
        self._session = None

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    def set_cookies(self, cookies: list[dict]) -> None:
        session = self._get_session()
        session.cookies.clear()
        for cookie in cookies:
            name = cookie.get("name")
            value = cookie.get("value")
            domain = cookie.get("domain")
            path = cookie.get("path", "/")
            if name and value is not None:
                options = {"path": path, "secure": bool(cookie.get("secure", False))}
                if domain:
                    options["domain"] = domain
                expires = cookie.get("expires", -1)
                if expires is not None and expires >= 0:
                    options["expires"] = int(expires)
                session.cookies.set(name, value, **options)

    def set_user_agent(self, user_agent: str) -> None:
        self._user_agent = user_agent
        if self._session is not None:
            self._session.headers.update({"User-Agent": user_agent})

    def download(self, asset: StoryAsset, target_dir: Path, index: int, story_source_url: str) -> Path:
        session = self._get_session()
        target_dir.mkdir(parents=True, exist_ok=True)

        response = session.get(asset.source_url, timeout=self._timeout_seconds, stream=True)
        response.raise_for_status()

        filename = asset.suggested_filename or _derive_filename(
            asset.source_url,
            index,
            response.headers.get("Content-Type"),
        )
        destination = target_dir / filename
        if destination.exists():
            stem = destination.stem
            suffix = destination.suffix
            destination = target_dir / f"{stem}-{index:02d}{suffix}"

        temp_destination = destination.with_name(f"{destination.name}.{index}.tmp")
        try:
            with temp_destination.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        handle.write(chunk)
            os.replace(temp_destination, destination)
        except Exception:
            if temp_destination.exists():
                try:
                    temp_destination.unlink()
                except OSError:
                    pass
            raise
        return destination

    def _get_session(self):
        if self._session is None:
            self._session = _build_session(self._user_agent)
        return self._session


def _build_session(user_agent: str):
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError(
            "requests is required at runtime. Install dependencies with pip install -r requirements.txt."
        ) from exc

    session = requests.Session()
    session.trust_env = False
    session.headers.update({"User-Agent": user_agent})
    return session


def _derive_filename(source_url: str, index: int, content_type: str | None) -> str:
    parsed = urlparse(source_url)
    basename = os.path.basename(parsed.path.rstrip("/"))
    extension = _extension_for_content_type(content_type)

    if basename:
        stem, current_extension = os.path.splitext(basename)
        if current_extension:
            return basename
        if extension:
            return f"{basename}{extension}"
        return basename

    return f"asset-{index:02d}{extension or '.bin'}"


def _extension_for_content_type(content_type: str | None) -> str:
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    if not normalized:
        return ""
    if normalized in _CONTENT_TYPE_EXTENSIONS:
        return _CONTENT_TYPE_EXTENSIONS[normalized]
    return mimetypes.guess_extension(normalized) or ""
