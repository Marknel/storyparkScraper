# Storypark Scraper — Remediation & Engineering TODO

This document tracks all defects, architectural flaws, and technical debt identified during the code audit, prioritized by operational necessity.

---

## Phase 1: Functional & Reliability Issues (Immediate Priority)

The immediate focus is stabilizing core scraping, media downloading, state persistence, and operational monitoring.

- [x] **Fix premature infinite-scroll termination in story collection**
  - **File**: `storypark_scraper/browser.py` (`collect_story_ids`)
  - **Issue**: `stable_rounds >= 1` breaks history scrolling after a single iteration if network latency exceeds `scroll_pause_ms`.
  - **Remedy**: Increase tolerance to 3-5 consecutive stable checks, check for DOM loading spinners/network idle, and ensure scrolling properly triggers dynamic pagination. *(Resolved: configurable `scroll_stable_rounds` defaulting to 3)*

- [x] **Fix stop-on-first-existing logic skipping new/backdated stories**
  - **File**: `storypark_scraper/app.py` (`run`)
  - **Issue**: Encountering a single existing story immediately aborts the child scan. Any backdated, pinned, or republished stories placed after an existing story are permanently stranded.
  - **Remedy**: Scan recent history with an existing-story threshold (e.g. 5-10 consecutive existing stories) or compare story dates before terminating the scan. *(Resolved: configurable `consecutive_existing_stories_threshold` defaulting to 5)*

- [x] **Prevent destructive media directory deletion before asset downloads**
  - **File**: `storypark_scraper/archive.py` (`export_story`, `_reset_media_dir`)
  - **Issue**: `_reset_media_dir` forcefully purges the story's `media/` folder before attempting any downloads. If any asset fails mid-download, previous assets are destroyed and the archive is corrupted.
  - **Remedy**: Download assets into a temporary staging directory (`.tmp_media`) and perform an atomic replacement only after all assets succeed. *(Resolved: staging directory + safe rollback)*

- [x] **Implement atomic writes for media assets and status file**
  - **Files**: `storypark_scraper/downloader.py` (`download`), `storypark_scraper/status.py` (`write_status_file`)
  - **Issue**: Streaming directly into destination files leaves incomplete/corrupt files on network drops or process interruptions. Writing `status.json` non-atomically causes `WinError 32` or read corruption for monitoring tools.
  - **Remedy**: Write to a `.tmp` file in the target directory and atomically replace (`os.replace`). *(Resolved)*

- [x] **Eliminate arbitrary `wait_for_timeout` hardcoded sleeps**
  - **File**: `storypark_scraper/browser.py`
  - **Issue**: Arbitrary 500ms, 1000ms, 1500ms, and 2000ms sleeps cause race conditions on slow network connections and waste time on fast ones.
  - **Remedy**: Replace with Playwright explicit locator assertions (`locator.wait_for(...)`, `expect(locator).to_be_visible()`, `page.wait_for_url(...)`). *(Resolved)*

- [x] **Fix SQLite WAL mode corruption risks on SMB / VM network shares**
  - **File**: `storypark_scraper/state.py` (`initialize`)
  - **Issue**: `PRAGMA journal_mode=WAL` is officially unsupported on network filesystems (SMB / CIFS / 9p) due to shared memory (`*-shm`) locking constraints, risking database corruption.
  - **Remedy**: Detect network mounts or configure appropriate rollback journal mode (`TRUNCATE` or `DELETE`) with increased busy timeout (`busy_timeout = 10000`). *(Resolved: busy_timeout=10000 + operational error fallback)*

- [x] **Fix Webserver fake HTTP 200 and schema DDL on every ping**
  - **File**: `storypark_scraper/webserver.py`
  - **Issue**: Webserver sends `HTTP 200 OK` header before querying SQLite, sending `{"error": ...}` with a 200 response when the DB is locked. Also runs `store.initialize()` (DDL migrations) on every HTTP request.
  - **Remedy**: Subclass `http.server.BaseHTTPRequestHandler`, query state read-only without running schema migrations, send proper HTTP 500/503 on failures, and use a multi-threaded server (`ThreadingHTTPServer`). *(Resolved)*

- [x] **Clean up zombie 'running' runs in state store**
  - **File**: `storypark_scraper/state.py`
  - **Issue**: Abrupt process termination or crashes leave previous runs permanently marked as `running` with `finished_at = NULL` (e.g. runs from May 3).
  - **Remedy**: Add a startup reconciliation step to mark stale orphaned runs as `aborted` or `failed`. *(Resolved: automatically sets to `interrupted`)*

- [x] **Fix Dockerfile browser installation and non-root execution**
  - **File**: `Dockerfile`
  - **Issue**: `playwright install` as `root` puts Chromium in `/root/.cache/ms-playwright` with 0700 permissions; switching to `appuser` causes Playwright launch failures.
  - **Remedy**: Set `ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`, install chromium into that shared path, and grant `appuser` read/execute permissions. *(Resolved)*

- [x] **Guard against UnicodeEncodeError on Windows stdout / logging**
  - **Files**: CLI entrypoints and logging
  - **Issue**: Story titles containing emojis (e.g. kangaroo `🦘`) crash Windows consoles using `cp1252`.
  - **Remedy**: Ensure stream handlers configure UTF-8 encoding or safe error replacement. *(Resolved)*

- [x] **Transfer Playwright session cookies to AssetDownloader**
  - **Files**: `storypark_scraper/app.py`, `storypark_scraper/downloader.py`
  - **Issue**: `AssetDownloader` uses a bare `requests.Session` without cookies. If Storypark requires authenticated sessions for media, downloads will fail.
  - **Remedy**: Pass authenticated cookies from Playwright's browser context to `requests.Session`, or download via Playwright APIRequestContext. *(Resolved)*

---

## Phase 2: Security & Credential Hardening

- [x] **Stop passing plaintext passwords via `cmdkey.exe` command-line arguments**
  - **File**: `storypark_scraper/credentials.py` (`store_windows_credentials`)
  - **Issue**: Plaintext password visible in process tables, audit logs, and task managers.
  - **Remedy**: Use `Advapi32.dll` `CredWriteW` via `ctypes` directly without spawning any external processes. *(Resolved)*

- [x] **Fix insecure DPAPI scope**
  - **File**: `storypark_scraper/credentials.py` (`_store_dpapi_file`)
  - **Issue**: Uses `CRYPTPROTECT_LOCAL_MACHINE` (allows any process/user on machine to decrypt credentials).
  - **Remedy**: Switch to user-scoped DPAPI (`local_machine=False`, `scope="current_user"`) with backwards-compatible decryption for existing machine-scoped stores. *(Resolved)*

- [x] **Remove plaintext password extraction in scheduled task setup**
  - **File**: `scripts/install_windows_task.ps1`
  - **Issue**: Marshals SecureString to plaintext BSTR in PowerShell memory.
  - **Remedy**: Support modern logon types (`Interactive` / `S4U`) via `New-ScheduledTaskPrincipal` and eliminate unmanaged BSTR marshaling. *(Resolved)*

- [x] **Sanitize `story_id` in archive paths**
  - **File**: `storypark_scraper/archive.py` (`export_story`)
  - **Issue**: `child_name` is sanitized but `story.story_id` is raw, allowing potential path traversal.
  - **Remedy**: Apply `_sanitize_path` to `story_id`. *(Resolved in Phase 1)*

- [x] **Prevent asset filename collision on identical basenames**
  - **File**: `storypark_scraper/downloader.py` (`_derive_filename`)
  - **Issue**: Two media items in the same story with identical URL filenames overwrite each other.
  - **Remedy**: Suffix subsequent matching filenames with asset collision counter `-02`, `-03`, etc. *(Resolved in Phase 1)*

- [x] **Secure status webserver binding and access control**
  - **File**: `storypark_scraper/webserver.py`, `storypark_scraper/cli.py`
  - **Issue**: Listened on all interfaces `0.0.0.0` with no authentication, exposing child names and statistics to local network scanners.
  - **Remedy**: Default host binding to `127.0.0.1` and support optional `--auth-token` (or `SP_WEB_TOKEN`) with `401 Unauthorized` enforcement. *(Resolved)*

- [x] **Warn on plaintext credentials loaded from `.env`**
  - **File**: `storypark_scraper/credentials.py`
  - **Issue**: Plaintext `.env` credentials pose credential exposure risks on shared hosts.
  - **Remedy**: Issue security `UserWarning` advising migration to Windows Credential Manager via `store-credentials`. *(Resolved)*

---

## Phase 3: Architecture & Technical Debt (NIH Syndrome & Dead Code)

- [ ] **Replace homebrew YAML parser with standard library / PyYAML**
  - **File**: `storypark_scraper/yaml_utils.py`
  - **Issue**: 202 lines of brittle parser that fails on 4-space indentation, multiline strings, and mixed sequence/mappings.
  - **Remedy**: Adopt standard `pyyaml` or Python 3.11+ stdlib `tomllib`.

- [ ] **Replace homebrew HTML tree builder with BeautifulSoup / Selectolax**
  - **File**: `storypark_scraper/parsing.py`
  - **Issue**: Custom 508-line DOM tree builder pops the entire tag stack on mismatched or unclosed HTML5 tags.
  - **Remedy**: Use `beautifulsoup4` or `selectolax` for resilient parsing.

- [ ] **Replace naive `.env` parser**
  - **File**: `storypark_scraper/credentials.py` (`_load_dotenv`)
  - **Remedy**: Adopt `python-dotenv` or robust regex parser.

- [ ] **Prune dead speculative code (Google Photos & Semantic Classifier)**
  - **Files**: `storypark_scraper/google_photos.py`, `storypark_scraper/classification.py`, `config.yaml`, `state.py`
  - **Issue**: Features raise `NotImplementedError` yet clutter database schema, child statistics queries, models, and config.
  - **Remedy**: Remove dead feature plumbing until real implementations are planned.
