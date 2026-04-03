# Storypark Scraper

This repository now archives Storypark story text and photos into local Markdown folders, tracks state in SQLite, and writes a Home Assistant friendly status JSON file.

## What it does

- Logs into Storypark with a persistent Playwright browser profile.
- Discovers all children available to the authenticated account.
- Downloads newly discovered story posts into `archive/<child>/<year>/<story_id>/`.
- Stores story text in `story.md` with YAML front matter.
- Applies denylist filtering for menu, newsletter, and reminder-style posts.
- Tracks idempotent state in `state/state.sqlite`.
- Writes run status to `status/status.json`.
- Supports a per-run story cap and a delay between processed stories for safer first-pass testing.

## Local Windows setup

1. Run `pwsh -File .\scripts\bootstrap.ps1`.
2. Store credentials with `.\.venv\Scripts\python.exe -m storypark_scraper --config .\config.yaml store-credentials --username you@example.com`.
3. Edit `config.yaml` to tune filters, paths, throttling, and child allowlists.
4. Run a first sync with `.\.venv\Scripts\python.exe -m storypark_scraper --config .\config.yaml run --full-scan --max-stories 3 --story-delay-seconds 2`.
5. If the first pass looks right, remove the cap and run a broader sync.
6. Register the daily scheduled task with `pwsh -File .\scripts\install_windows_task.ps1 -RunAt 03:00 -User YOURMACHINE\storypark-svc`.

## Development fallback

You can still use `.env` for local development. Copy `example.env` to `.env` and set `SP_USER` and `SP_PASS`.

## Runtime outputs

- Archive root: `archive/`
- SQLite state: `state/state.sqlite`
- Browser profile: `state/browser-profile`
- Status JSON: `status/status.json`

## Home Assistant

The first release writes a stable JSON payload with these fields:

- `state`
- `last_run_started_at`
- `last_run_finished_at`
- `last_success_at`
- `last_story_acquired_at`
- `stories_seen`
- `stories_downloaded`
- `stories_skipped`
- `last_error`

## Docker

Docker remains secondary. If you use it, mount `config.yaml`, `archive/`, `state/`, and `status/`, and provide credentials through `.env`.
