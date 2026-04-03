from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import sqlite3
import uuid

from storypark_scraper.models import ExportedAsset, FilterDecision, RunStats, StoryContent


STORIES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS stories (
    story_id TEXT NOT NULL,
    child_name TEXT NOT NULL,
    title TEXT,
    published_at TEXT,
    source_url TEXT NOT NULL,
    author TEXT,
    content_type TEXT NOT NULL,
    filter_decision TEXT NOT NULL,
    filter_reason TEXT NOT NULL,
    archive_path TEXT,
    asset_count INTEGER NOT NULL DEFAULT 0,
    body_hash TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_run_id TEXT NOT NULL,
    last_downloaded_at TEXT,
    semantic_labels TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (story_id, child_name)
);
CREATE INDEX IF NOT EXISTS idx_stories_story_id ON stories(story_id);
"""

ASSETS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS assets (
    story_id TEXT NOT NULL,
    child_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    local_path TEXT,
    kind TEXT NOT NULL,
    downloaded_at TEXT,
    PRIMARY KEY (story_id, child_name, source_url)
);
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    def __init__(self, path: Path):
        self._path = path
        self._connection: sqlite3.Connection | None = None

    def initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self._path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                state TEXT NOT NULL,
                last_error TEXT,
                stories_seen INTEGER NOT NULL DEFAULT 0,
                stories_downloaded INTEGER NOT NULL DEFAULT 0,
                stories_skipped INTEGER NOT NULL DEFAULT 0,
                last_story_acquired_at TEXT
            );
            """
        )
        self._migrate_schema_if_needed()
        self.connection.executescript(STORIES_TABLE_SQL)
        self.connection.executescript(ASSETS_TABLE_SQL)
        self.connection.commit()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def start_run(self) -> str:
        run_id = uuid.uuid4().hex
        started_at = utc_now_iso()
        self.connection.execute(
            """
            INSERT INTO runs(run_id, started_at, state, last_error, stories_seen, stories_downloaded, stories_skipped)
            VALUES (?, ?, 'running', NULL, 0, 0, 0)
            """,
            (run_id, started_at),
        )
        self.connection.commit()
        return run_id

    def finish_run(
        self,
        run_id: str,
        state: str,
        stats: RunStats,
        last_story_acquired_at: str | None,
        last_error: str | None,
    ) -> None:
        self.connection.execute(
            """
            UPDATE runs
            SET finished_at = ?, state = ?, last_error = ?, stories_seen = ?, stories_downloaded = ?,
                stories_skipped = ?, last_story_acquired_at = ?
            WHERE run_id = ?
            """,
            (
                utc_now_iso(),
                state,
                last_error,
                stats.stories_seen,
                stats.stories_downloaded,
                stats.stories_skipped,
                last_story_acquired_at,
                run_id,
            ),
        )
        self.connection.commit()

    def has_any_successful_run(self) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM runs WHERE state = 'success' LIMIT 1"
        ).fetchone()
        return row is not None

    def story_exists(self, story_id: str, child_name: str | None = None) -> bool:
        if child_name is None:
            row = self.connection.execute(
                "SELECT 1 FROM stories WHERE story_id = ? LIMIT 1",
                (story_id,),
            ).fetchone()
        else:
            row = self.connection.execute(
                "SELECT 1 FROM stories WHERE story_id = ? AND child_name = ? LIMIT 1",
                (story_id, child_name),
            ).fetchone()
        return row is not None

    def upsert_story(
        self,
        story: StoryContent,
        run_id: str,
        decision: FilterDecision,
        archive_path: str | None,
        assets: list[ExportedAsset],
        downloaded_at: str | None,
    ) -> None:
        now = utc_now_iso()
        body_hash = hashlib.sha256(story.searchable_text.encode("utf-8")).hexdigest()
        labels = ",".join(story.semantic_labels)
        self.connection.execute(
            """
            INSERT INTO stories(
                story_id, child_name, title, published_at, source_url, author, content_type,
                filter_decision, filter_reason, archive_path, asset_count, body_hash,
                first_seen_at, last_seen_at, last_run_id, last_downloaded_at, semantic_labels
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(story_id, child_name) DO UPDATE SET
                title = excluded.title,
                published_at = excluded.published_at,
                source_url = excluded.source_url,
                author = excluded.author,
                content_type = excluded.content_type,
                filter_decision = excluded.filter_decision,
                filter_reason = excluded.filter_reason,
                archive_path = excluded.archive_path,
                asset_count = excluded.asset_count,
                body_hash = excluded.body_hash,
                last_seen_at = excluded.last_seen_at,
                last_run_id = excluded.last_run_id,
                last_downloaded_at = COALESCE(excluded.last_downloaded_at, stories.last_downloaded_at),
                semantic_labels = excluded.semantic_labels
            """,
            (
                story.story_id,
                story.child_name,
                story.title,
                story.published_at,
                story.source_url,
                story.author,
                story.content_type,
                decision.label,
                decision.reason,
                archive_path,
                len(assets),
                body_hash,
                now,
                now,
                run_id,
                downloaded_at,
                labels,
            ),
        )
        for asset in assets:
            self.connection.execute(
                """
                INSERT INTO assets(story_id, child_name, source_url, local_path, kind, downloaded_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(story_id, child_name, source_url) DO UPDATE SET
                    local_path = excluded.local_path,
                    kind = excluded.kind,
                    downloaded_at = excluded.downloaded_at
                """,
                (
                    story.story_id,
                    story.child_name,
                    asset.source_url,
                    str(asset.local_path),
                    asset.kind,
                    downloaded_at,
                ),
            )
        self.connection.commit()

    def get_latest_run(self):
        return self.connection.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()

    def get_last_success_at(self) -> str | None:
        row = self.connection.execute(
            "SELECT MAX(finished_at) AS value FROM runs WHERE state = 'success'"
        ).fetchone()
        return row["value"] if row else None

    def get_last_story_acquired_at(self) -> str | None:
        row = self.connection.execute(
            "SELECT MAX(last_downloaded_at) AS value FROM stories"
        ).fetchone()
        return row["value"] if row else None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("StateStore.initialize() must be called before use")
        return self._connection

    def _migrate_schema_if_needed(self) -> None:
        self._migrate_stories_table_if_needed()
        self._migrate_assets_table_if_needed()

    def _migrate_stories_table_if_needed(self) -> None:
        if not self._table_exists("stories"):
            return
        pk_columns = self._primary_key_columns("stories")
        if pk_columns == ["story_id", "child_name"]:
            return
        self.connection.executescript(
            """
            CREATE TABLE stories_v2 (
                story_id TEXT NOT NULL,
                child_name TEXT NOT NULL,
                title TEXT,
                published_at TEXT,
                source_url TEXT NOT NULL,
                author TEXT,
                content_type TEXT NOT NULL,
                filter_decision TEXT NOT NULL,
                filter_reason TEXT NOT NULL,
                archive_path TEXT,
                asset_count INTEGER NOT NULL DEFAULT 0,
                body_hash TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                last_run_id TEXT NOT NULL,
                last_downloaded_at TEXT,
                semantic_labels TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (story_id, child_name)
            );
            INSERT INTO stories_v2(
                story_id, child_name, title, published_at, source_url, author, content_type,
                filter_decision, filter_reason, archive_path, asset_count, body_hash,
                first_seen_at, last_seen_at, last_run_id, last_downloaded_at, semantic_labels
            )
            SELECT
                story_id, child_name, title, published_at, source_url, author, content_type,
                filter_decision, filter_reason, archive_path, asset_count, body_hash,
                first_seen_at, last_seen_at, last_run_id, last_downloaded_at, semantic_labels
            FROM stories;
            DROP TABLE stories;
            ALTER TABLE stories_v2 RENAME TO stories;
            CREATE INDEX IF NOT EXISTS idx_stories_story_id ON stories(story_id);
            """
        )
        self.connection.commit()

    def _migrate_assets_table_if_needed(self) -> None:
        if not self._table_exists("assets"):
            return
        columns = self._table_columns("assets")
        pk_columns = self._primary_key_columns("assets")
        if "child_name" in columns and pk_columns == ["story_id", "child_name", "source_url"]:
            return
        self.connection.executescript(
            """
            CREATE TABLE assets_v2 (
                story_id TEXT NOT NULL,
                child_name TEXT NOT NULL,
                source_url TEXT NOT NULL,
                local_path TEXT,
                kind TEXT NOT NULL,
                downloaded_at TEXT,
                PRIMARY KEY (story_id, child_name, source_url)
            );
            INSERT INTO assets_v2(story_id, child_name, source_url, local_path, kind, downloaded_at)
            SELECT
                a.story_id,
                COALESCE(s.child_name, ''),
                a.source_url,
                a.local_path,
                a.kind,
                a.downloaded_at
            FROM assets a
            LEFT JOIN stories s ON s.story_id = a.story_id;
            DROP TABLE assets;
            ALTER TABLE assets_v2 RENAME TO assets;
            """
        )
        self.connection.commit()

    def _table_exists(self, name: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        ).fetchone()
        return row is not None

    def _table_columns(self, name: str) -> list[str]:
        rows = self.connection.execute(f"PRAGMA table_info({name})").fetchall()
        return [row[1] for row in rows]

    def _primary_key_columns(self, name: str) -> list[str]:
        rows = self.connection.execute(f"PRAGMA table_info({name})").fetchall()
        ordered = sorted((row[5], row[1]) for row in rows if row[5])
        return [name for _, name in ordered]
