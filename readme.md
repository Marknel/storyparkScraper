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

## Storypark CAPTCHA and MFA

Storypark may require the account owner to complete a CAPTCHA and MFA. The scraper
reuses its persistent browser profile after that sign-in. When a scheduled run
needs verification, it stops with an actionable error instead of waiting for a
person during the night.

On the scraper machine, use the same Windows account and configuration as the
scheduled task:

```powershell
.\.venv\Scripts\python.exe -m storypark_scraper --config .\config.yaml login
```

Complete the verification in the visible browser. The command only succeeds after
it sees the authenticated Storypark menu. Closing the browser or timing out does
not count as a successful login. The browser closes cleanly to save the profile.

For a headless host, use the temporary login-assist page:

```powershell
python -m storypark_scraper --config config.yaml login --headless
```

Open the complete private link printed by that command, then use its screenshot
view to click and enter your code. Each session has a fresh access token; the
page shuts down after verified sign-in or timeout. It binds to `127.0.0.1:8000`
by default. Use an SSH tunnel for another machine. An explicit `--host` and
`--port` can be supplied when arranging forwarding; no public listener is started
by ordinary syncs or by the status webserver.

Login does not change `runtime.enabled`. If the installation is paused, leave it
paused while renewing the profile, then enable it for a capped acceptance run:

```powershell
.\.venv\Scripts\python.exe -m storypark_scraper --config .\config.yaml run --max-stories 3 --max-stories-per-child 1 --story-delay-seconds 2
```

Verify the resulting status, story Markdown and media, then repeat to check session
reuse and deduplication before relying on the overnight schedule. Browser profiles,
credentials, verification codes and private login-assist links must stay out of
Git and CI artifacts. Never run login concurrently with sync: both use the same
profile lock.

Optional settings under `runtime` are `auth_timeout_seconds` (600),
`auth_assist_host` (`127.0.0.1`) and `auth_assist_port` (8000). With no `user_agent`
override, the scraper uses the installed Chromium version consistently for login,
sync and authenticated media downloads.

The approach is adapted from [upstream release 1.1](https://github.com/AustralianSimon/storyparkScraper/commit/b27237ef8c64d2bd99c51173f2dcc689fc9e3104).
See [the upstream review](docs/upstream-auth-review.md) and
[home deployment instructions](docs/home-deployment.md) for the integration and rollout details.

## Development fallback

You can still use `.env` for local development. Copy `example.env` to `.env` and set `SP_USER` and `SP_PASS`.

## Runtime outputs

- Archive root: `archive/`
- SQLite state: `state/state.sqlite`
- Browser profile: `state/browser-profile`
- Status JSON: `status/status.json`

## Home Assistant Integration

### 1. Native MQTT Discovery (Recommended)
Configure the `mqtt` block in `config.yaml`:
```yaml
mqtt:
  enabled: true
  host: "192.168.1.50"          # IP or hostname of your MQTT broker
  port: 1883
  username: "homeassistant"      # Or dedicated MQTT user
  password: "your_mqtt_password" # Or store via store-credentials / SP_MQTT_PASSWORD
  discovery_prefix: "homeassistant"
  topic_prefix: "storypark"
```

The scraper automatically registers a **Storypark Scraper** device in Home Assistant via MQTT Discovery with:
- `sensor.storypark_status` (`idle`, `running`, `success`, `failed`, `interrupted`)
- `binary_sensor.storypark_problem` (alert sensor: turns **ON** when an error or failure occurs)
- `sensor.storypark_last_run` (timestamp) and `sensor.storypark_last_run_duration` (seconds)
- `sensor.storypark_stories_total` and `sensor.storypark_photos_total`
- Per-child counters: `sensor.storypark_<child>_stories` and `sensor.storypark_<child>_photos`

Test discovery and publish current status at any time:
```bash
python -m storypark_scraper mqtt-publish
```

#### Example Failure Alert Automation in Home Assistant:
```yaml
alias: "Storypark Scraper Failure Notification"
trigger:
  - platform: state
    entity_id: binary_sensor.storypark_problem
    to: "on"
action:
  - service: notify.notify
    data:
      title: "⚠️ Storypark Scraper Alert"
      message: "The daycare scraper encountered an error during its run!"
```

### 2. HTTP Status JSON Endpoint
The status webserver serves `status.json` on port 8082 (`python -m storypark_scraper serve --auth-token <token>`).

## Docker

Docker remains secondary. If you use it, mount `config.yaml`, `archive/`, `state/`, and `status/`, and provide credentials through `.env`.
