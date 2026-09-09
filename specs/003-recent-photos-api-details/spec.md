# Feature Specification: Recent Photos Feed API & Status Details Reporting Web Page

**Feature Branch**: `003-recent-photos-api-details`
**Created**: 2026-09-09
**Status**: Draft
**Input**: "We could expose the latest 20 photos ... We could have some reporting show up on a details page"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Recent Photos API Feed (Priority: P1)

As a frontend developer or home automation dashboard, I want an HTTP API endpoint that returns metadata for the latest 20 downloaded daycare photos so that downstream widgets and dashboard cards can display fresh memories.

**Why this priority**: Foundational data feed for kitchen dashboards, Lovelace cards, and web views.

**Independent Test**: Send `GET /api/recent-photos?limit=20` to the webserver and verify it returns a JSON list of up to 20 media items ordered by published date descending, with child names, story titles, dates, and media URLs.

**Acceptance Scenarios**:
1. **Given** 50 photos exist in the database across stories, **When** requesting `GET /api/recent-photos?limit=20`, **Then** the endpoint returns the 20 most recent photos with `story_id`, `child_name`, `story_title`, `published_at`, and `media_url`.
2. **Given** the child query parameter `?child=Samuel+Cracknell`, **When** requested, **Then** only photos featuring Samuel Cracknell are returned.

---

### User Story 2 - Secure Media Asset Streaming Endpoint (Priority: P1)

As a browser client, I want to fetch the actual photo and video files via HTTP URLs so that dashboard cards and web browsers can display the photos.

**Why this priority**: Required for browsers and dashboards to display images without accessing raw SMB/local filesystem paths.

**Independent Test**: Request `GET /media/<story_id>/<filename>` for a known downloaded image and verify the image binary is returned with `Content-Type: image/jpeg` and caching headers.

**Acceptance Scenarios**:
1. **Given** an archived photo on disk, **When** requesting its `/media/...` endpoint, **Then** HTTP 200 with appropriate MIME type and file stream is returned.
2. **Given** an invalid path attempting path traversal (e.g. `../../config.yaml`), **When** requested, **Then** the server rejects the request with HTTP 403 or 404.

---

### User Story 3 - Visual Status Details Reporting Web Page (Priority: P2)

As a family member or system operator, I want to open `/details` in a web browser to view a visual status page showing recent scraper runs, per-child photo counts, and a gallery of the latest photos.

**Why this priority**: Direct user request ("We could have some reporting show up on a details page"); provides an intuitive, non-technical overview of scraper health and recent daycare memories.

**Independent Test**: Navigate to `http://<host>:8080/details` in a web browser and verify status cards, child tallies, run history table, and photo gallery render cleanly.

**Acceptance Scenarios**:
1. **Given** the webserver is running, **When** a user navigates to `/details`, **Then** an HTML page renders with:
   - System status badge (Idle / Running / Success / Error)
   - Child summary cards for Sam and Lyra with photo and story counts
   - Recent runs table showing last 10 executions
   - Gallery grid displaying the latest 20 photos with captions

---

### Edge Cases

- **Path Traversal Attacks**: The media serving handler MUST enforce path containment within the configured archive root.
- **Missing Media on Disk**: Return HTTP 404 cleanly without crashing the webserver thread.
- **Empty Archive**: When no photos are present yet, the API returns `[]` and the details page shows a clean empty state.
- **Authentication**: When `--auth-token` is enabled, the API and media endpoints must support token verification (via header or query parameter `?token=...`).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Webserver MUST expose `GET /api/recent-photos` supporting `limit` (default 20, max 100) and optional `child` filter query parameters.
- **FR-002**: System MUST query the SQLite state store for candidate photos ordered by `stories.published_at DESC`, returning child name, story ID, story title, publish date, local relative path, and accessible media URL.
- **FR-003**: Webserver MUST expose `GET /media/<path>` to stream image and video files from the configured archive directory.
- **FR-004**: System MUST validate that all requested media paths resolve strictly within the archive directory, blocking directory traversal attempts.
- **FR-005**: Webserver MUST serve a responsive, human-readable HTML reporting page at `GET /details` (or `GET /report`).
- **FR-006**: The details page MUST display current status, recent run history, child statistics, and a responsive image gallery.
- **FR-007**: Media streaming MUST include appropriate HTTP headers (`Content-Type`, `Content-Length`, `Cache-Control`).
- **FR-008**: System MUST respect webserver authentication settings (`--auth-token`) across all endpoints.

### Key Entities

- **PhotoFeedItem**: Representation of a recent photo with `media_id`, `child_name`, `story_id`, `story_title`, `published_at`, `media_url`, and `thumbnail_url`.
- **SystemReport**: Comprehensive report object combining system state, run history, child stats, and recent photos.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `GET /api/recent-photos` responds in under 50ms for libraries with over 1,000 photos.
- **SC-002**: Details reporting page renders and displays all 20 photos and charts in under 200ms.
- **SC-003**: 100% of directory traversal attempts are blocked.

## Assumptions

- The status webserver runs in a trusted local network environment or behind an authentication token.
- Modern web browsers accessing `/details` support standard CSS grid and HTML5 media elements.
