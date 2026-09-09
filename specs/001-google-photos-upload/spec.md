# Feature Specification: Google Photos Automatic Upload to Existing Per-Child Albums

**Feature Branch**: `001-google-photos-upload`
**Created**: 2026-09-09
**Status**: Draft
**Input**: "Rather than automatically making an album, I'd prefer an error if one doesn't exist. I can manually make the album so I retain control there. Upload directly into 'Daycare - Sam' and 'Daycare - Lyra' albums."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Automatic Upload to Existing Per-Child Album (Priority: P1)

As a parent, I want media from new Storypark stories featuring my child to be automatically uploaded to that child's dedicated Google Photos album ('Daycare - Sam' or 'Daycare - Lyra') so that daycare photos appear in my family photo library without manual exporting.

**Why this priority**: Core value proposition of the integration; eliminates manual downloading and uploading of photos.

**Independent Test**: Can be tested with an existing test album in Google Photos by triggering a scraper run with 1 new candidate story containing photos and verifying the photo appears in the album.

**Acceptance Scenarios**:
1. **Given** child "Samuel Cracknell" is mapped to album "Daycare - Sam" and the album exists in Google Photos, **When** a story for Sam with 2 photos is scraped, **Then** both photos are uploaded to Google Photos and added to "Daycare - Sam", and the upload is recorded in local state.
2. **Given** child "Lyra Cracknell" is mapped to album "Daycare - Lyra" and the album exists in Google Photos, **When** a story for Lyra with 1 photo is scraped, **Then** the photo is uploaded to Google Photos and added to "Daycare - Lyra".

---

### User Story 2 - Album Existence Verification & Error Enforcement (Priority: P1)

As a parent, I want the system to throw an explicit, clear error if a configured album does not exist in Google Photos rather than automatically creating an album, so that I retain full control over album organization and naming.

**Why this priority**: Explicit user requirement: "Rather than automatically making an album, I'd prefer an error if one doesn't exist. I can manually make the album so I retain control there."

**Independent Test**: Configure an album name that does not exist in Google Photos (e.g. "Daycare - Nonexistent"), run the upload check, and verify the system reports an `AlbumNotFoundError` and halts upload for that child without creating any new album.

**Acceptance Scenarios**:
1. **Given** a child is mapped to an album name that does not exist in Google Photos, **When** the uploader attempts to sync photos for that child, **Then** the system logs an error indicating the album was not found and advises the user to create the album manually, without attempting album creation.
2. **Given** album lookup fails, **When** the error is caught, **Then** local downloaded archive files remain intact and the scraper run finishes with appropriate warning/failure status.

---

### User Story 3 - Multi-Child Group Story Album Distribution (Priority: P2)

As a parent with multiple children at daycare, I want group stories featuring both Sam and Lyra to have their photos uploaded to both "Daycare - Sam" and "Daycare - Lyra" albums.

**Why this priority**: Group activities (e.g. joint playground time, celebrations) frequently involve both siblings, and parents want memories accessible in each child's individual timeline.

**Independent Test**: Ingest a story with `included_children: ["Samuel Cracknell", "Lyra Cracknell"]` and verify the uploaded photos appear in both albums in Google Photos.

**Acceptance Scenarios**:
1. **Given** a story lists both "Samuel Cracknell" and "Lyra Cracknell", **When** the upload executes, **Then** media items are added to both "Daycare - Sam" and "Daycare - Lyra".

---

### User Story 4 - Idempotent Uploads and Deduplication (Priority: P2)

As a system operator, I want the uploader to record uploaded asset IDs in SQLite so that subsequent scheduled runs or retries never upload duplicate photos to Google Photos.

**Why this priority**: Prevents duplicate photos cluttering albums and conserves API quota and bandwidth.

**Independent Test**: Run the uploader twice against the same story; verify that Google Photos API media upload is called on run 1 and skipped with "already uploaded" on run 2.

**Acceptance Scenarios**:
1. **Given** an asset was previously uploaded and recorded in the database, **When** the scraper runs again, **Then** the asset is skipped and no redundant upload request is sent.

---

### User Story 5 - Secure Google Photos Credentials & Backfill CLI (Priority: P3)

As a system operator, I want a secure CLI command to authenticate with Google Photos OAuth 2.0 and an optional sync command to backfill historical archive photos.

**Why this priority**: Keeps OAuth tokens secure without plaintext file exposure and allows onboarding existing historical archives.

**Independent Test**: Execute `python -m storypark_scraper store-google-credentials` and `python -m storypark_scraper sync-google-photos --dry-run`.

**Acceptance Scenarios**:
1. **Given** Google OAuth client ID/secret, **When** running `store-google-credentials`, **Then** the refresh token is encrypted via Windows Credential Manager / DPAPI.
2. **Given** existing archived stories in local storage, **When** running `sync-google-photos`, **Then** historical un-uploaded photos are synced to their respective albums.

---

### Edge Cases

- **Google API Quota Limit (HTTP 429)**: The uploader must implement exponential backoff with jitter and resume on next run without failing permanently.
- **Token Expiry**: Expired OAuth access tokens must automatically refresh using the stored refresh token without user intervention.
- **Large Video Uploads**: Video files must be streamed in chunks or checked against maximum upload payload limits.
- **Non-Media Attachments**: PDF reports, learning frameworks, or plain text notes must be excluded from Google Photos upload.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST support configuration mapping each child target to a specific Google Photos album name (e.g., `albums: { "Samuel Cracknell": "Daycare - Sam", "Lyra Cracknell": "Daycare - Lyra" }`).
- **FR-002**: System MUST verify the existence of the configured album in the user's Google Photos library before attempting any upload.
- **FR-003**: System MUST NOT automatically create an album if the target album is not found; it MUST log an explicit error specifying the missing album name and instructions to create it manually.
- **FR-004**: System MUST securely store OAuth 2.0 client credentials and refresh tokens using Windows Credential Manager (with DPAPI fallback) and NEVER store tokens in plaintext configuration files.
- **FR-005**: System MUST track uploaded media items in the SQLite database (`google_photos_uploads` table) with asset source URL/hash, Google Photos media ID, album name, and upload timestamp.
- **FR-006**: System MUST check the database prior to upload and skip any asset that has already been successfully uploaded to the target album.
- **FR-007**: System MUST distribute media from group stories that include multiple configured children into each child's respective album.
- **FR-008**: System MUST provide a CLI command `store-google-credentials` for initial OAuth authorization and `sync-google-photos` for historical archive backfills.
- **FR-009**: System MUST preserve local archive files independently of Google Photos upload outcomes (upload failures must never delete local media).

### Key Entities

- **GooglePhotosAlbum**: Represents a destination album in Google Photos with attributes `id`, `title`, and `productUrl`.
- **GooglePhotosUploadRecord**: Database entity tracking `(asset_hash, child_name, album_name, google_media_id, uploaded_at, status)`.
- **GoogleOAuthCredential**: Represents stored OAuth client ID, client secret, and refresh token.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of candidate photos and videos for configured children are uploaded to the correct existing album during daily runs.
- **SC-002**: 0 duplicate photos uploaded across repeated scheduled runs or manual sync executions.
- **SC-003**: In the event of a missing album, system logs a clear descriptive error within 5 seconds and does not create an unintended album.
- **SC-004**: OAuth token refresh happens seamlessly in the background with zero manual intervention required after initial setup.

## Assumptions

- The user has a Google Cloud Project with the Google Photos Library API enabled, an OAuth 2.0 Client ID, and Client Secret.
- The user has manually created the albums `Daycare - Sam` and `Daycare - Lyra` in Google Photos before enabling upload.
- The scraping process runs on a machine with network access to Google APIs (`https://photoslibrary.googleapis.com`).
