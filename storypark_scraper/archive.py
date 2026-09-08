from __future__ import annotations

from pathlib import Path
import os
import re
import shutil
import uuid

from storypark_scraper.models import ExportResult, ExportedAsset, FilterDecision, StoryContent
from storypark_scraper.yaml_utils import dump_yaml


class ArchiveWriter:
    def __init__(self, archive_root: Path, downloader):
        self._archive_root = archive_root
        self._downloader = downloader

    def export_story(self, story: StoryContent, decision: FilterDecision, scraped_at: str) -> ExportResult:
        clean_story_id = _sanitize_path(story.story_id)
        story_dir = (
            self._archive_root
            / _sanitize_path(story.child_name)
            / _story_year(story.published_at)
            / clean_story_id
        )
        media_dir = story_dir / "media"
        story_dir.mkdir(parents=True, exist_ok=True)

        staging_media_dir = story_dir / f".tmp_media_{uuid.uuid4().hex[:8]}"
        staging_media_dir.mkdir(parents=True, exist_ok=True)

        try:
            downloaded_paths: list[tuple[object, str]] = []
            for index, asset in enumerate(story.assets, start=1):
                staging_path = self._downloader.download(asset, staging_media_dir, index, story.source_url)
                downloaded_paths.append((asset, staging_path.name))

            _safe_replace_dir(staging_media_dir, media_dir)
        except Exception:
            if staging_media_dir.exists():
                shutil.rmtree(staging_media_dir, ignore_errors=True)
            raise

        exported_assets: list[ExportedAsset] = [
            ExportedAsset(
                source_url=asset.source_url,
                local_path=media_dir / filename,
                kind=asset.kind,
            )
            for asset, filename in downloaded_paths
        ]

        front_matter = {
            "storypark_id": story.story_id,
            "child_name": story.child_name,
            "published_at": story.published_at,
            "scraped_at": scraped_at,
            "source_url": story.source_url,
            "filter_decision": decision.label,
            "asset_count": len(exported_assets),
            "author": story.author,
            "content_type": story.content_type,
            "included_children": story.included_children,
            "google_photos_candidate": story.google_photos_candidate,
            "semantic_labels": story.semantic_labels,
        }
        markdown_path = story_dir / "story.md"
        temp_markdown_path = story_dir / f".story.md.{uuid.uuid4().hex[:8]}.tmp"
        try:
            temp_markdown_path.write_text(_render_markdown(front_matter, story, exported_assets), encoding="utf-8")
            os.replace(temp_markdown_path, markdown_path)
        except Exception:
            if temp_markdown_path.exists():
                try:
                    temp_markdown_path.unlink()
                except OSError:
                    pass
            raise
        return ExportResult(story_dir=story_dir, story_markdown_path=markdown_path, assets=exported_assets)


def _render_markdown(front_matter: dict[str, object], story: StoryContent, assets: list[ExportedAsset]) -> str:
    title = story.title.strip() or f"Storypark Story {story.story_id}"
    body_parts = ["---", dump_yaml(front_matter).strip(), "---", "", f"# {title}", ""]

    if story.author:
        body_parts.extend([f"Author: {story.author}", ""])
    if story.published_text:
        body_parts.extend([f"Published: {story.published_text}", ""])
    if story.included_children:
        body_parts.extend([f"Included Children: {', '.join(story.included_children)}", ""])
    body_parts.extend([f"Google Photos Candidate: {'yes' if story.google_photos_candidate else 'no'}", ""])
    if story.body:
        body_parts.extend([story.body.strip(), ""])

    image_assets = [asset for asset in assets if asset.kind == "image"]
    video_assets = [asset for asset in assets if asset.kind == "video"]
    other_assets = [asset for asset in assets if asset.kind not in {"image", "video"}]

    if image_assets:
        body_parts.append("## Photos")
        body_parts.append("")
        for asset in image_assets:
            relative_path = Path("media") / asset.local_path.name
            body_parts.append(f"![{asset.local_path.name}]({relative_path.as_posix()})")
        body_parts.append("")

    if video_assets:
        body_parts.append("## Videos")
        body_parts.append("")
        for asset in video_assets:
            relative_path = Path("media") / asset.local_path.name
            body_parts.append(f"- [{asset.local_path.name}]({relative_path.as_posix()})")
        body_parts.append("")

    if other_assets:
        body_parts.append("## Attachments")
        body_parts.append("")
        for asset in other_assets:
            relative_path = Path("media") / asset.local_path.name
            body_parts.append(f"- [{asset.local_path.name}]({relative_path.as_posix()})")
        body_parts.append("")

    return "\n".join(body_parts).rstrip() + "\n"


def _safe_replace_dir(src: Path, dst: Path) -> None:
    if not dst.exists():
        src.rename(dst)
        return

    backup = dst.with_name(f".backup_{dst.name}_{uuid.uuid4().hex[:8]}")
    dst.rename(backup)
    try:
        src.rename(dst)
        shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        if not dst.exists() and backup.exists():
            backup.rename(dst)
        raise


def _story_year(published_at: str | None) -> str:
    if published_at and re.match(r"^\d{4}", published_at):
        return published_at[:4]
    return "unknown"


def _sanitize_path(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "-", value).strip().strip(".")
    return cleaned or "unknown-child"
