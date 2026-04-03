from __future__ import annotations

from pathlib import Path
import re
import shutil

from storypark_scraper.models import ExportResult, ExportedAsset, FilterDecision, StoryContent
from storypark_scraper.yaml_utils import dump_yaml


class ArchiveWriter:
    def __init__(self, archive_root: Path, downloader):
        self._archive_root = archive_root
        self._downloader = downloader

    def export_story(self, story: StoryContent, decision: FilterDecision, scraped_at: str) -> ExportResult:
        story_dir = self._archive_root / _sanitize_path(story.child_name) / _story_year(story.published_at) / story.story_id
        media_dir = story_dir / "media"
        story_dir.mkdir(parents=True, exist_ok=True)
        _reset_media_dir(media_dir)

        exported_assets: list[ExportedAsset] = []
        for index, asset in enumerate(story.assets, start=1):
            local_path = self._downloader.download(asset, media_dir, index, story.source_url)
            exported_assets.append(ExportedAsset(source_url=asset.source_url, local_path=local_path, kind=asset.kind))

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
            "semantic_labels": story.semantic_labels,
        }
        markdown_path = story_dir / "story.md"
        markdown_path.write_text(_render_markdown(front_matter, story, exported_assets), encoding="utf-8")
        return ExportResult(story_dir=story_dir, story_markdown_path=markdown_path, assets=exported_assets)


def _render_markdown(front_matter: dict[str, object], story: StoryContent, assets: list[ExportedAsset]) -> str:
    title = story.title.strip() or f"Storypark Story {story.story_id}"
    body_parts = ["---", dump_yaml(front_matter).strip(), "---", "", f"# {title}", ""]

    if story.author:
        body_parts.extend([f"Author: {story.author}", ""])
    if story.published_text:
        body_parts.extend([f"Published: {story.published_text}", ""])
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


def _reset_media_dir(media_dir: Path) -> None:
    if media_dir.exists():
        for entry in media_dir.iterdir():
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
    media_dir.mkdir(parents=True, exist_ok=True)


def _story_year(published_at: str | None) -> str:
    if published_at and re.match(r"^\d{4}", published_at):
        return published_at[:4]
    return "unknown"


def _sanitize_path(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "-", value).strip().strip(".")
    return cleaned or "unknown-child"
