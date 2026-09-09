# Feature Specification: Rotating Daycare Photo Web Component for Kitchen Dashboard

**Feature Branch**: `004-kitchen-dashboard-card`
**Created**: 2026-09-09
**Status**: Draft
**Input**: "and have a rotating photo entity for my kitchen dashboard - maybe just as a web component is easiset for this?"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ambient Photo Rotation on Kitchen Display (Priority: P1)

As a parent standing in the kitchen, I want a wall-mounted tablet or smart display to cycle through recent daycare photos of Sam and Lyra with smooth transitions and subtle date/title captions, so our family can enjoy recent preschool moments effortlessly throughout the day.

**Why this priority**: Delivers the primary daily emotional value of the entire scraper system into the home living space.

**Independent Test**: Open the web component in a browser or Lovelace dashboard card, verify it fetches the latest 20 photos and smoothly rotates to the next photo every 30 seconds with child name and date displayed.

**Acceptance Scenarios**:
1. **Given** 20 recent photos available from the scraper API, **When** the dashboard card loads, **Then** the first photo is displayed with child name, story title, and date.
2. **Given** the configured rotation interval passes (e.g. 30 seconds), **When** time elapses, **Then** the display cross-fades smoothly to the subsequent photo.
3. **Given** the last photo is reached, **When** the interval elapses, **Then** the slideshow seamlessly loops back to the first photo.

---

### User Story 2 - Home Assistant Dashboard Embedding (Priority: P1)

As a Home Assistant user, I want to easily embed the rotating photo component into my Lovelace dashboard (either via Webpage card, iframe, or custom Lovelace card) without complex frontend build toolchains.

**Why this priority**: Direct user request ("maybe just as a web component is easiest for this"); zero-dependency embedding in Home Assistant.

**Independent Test**: Embed `<storypark-photo-frame src="http://.../api/recent-photos"></storypark-photo-frame>` or embed `/widget/photo-frame` into a Home Assistant Webpage card; verify it renders cleanly without layout overflow.

**Acceptance Scenarios**:
1. **Given** a Home Assistant Lovelace dashboard, **When** added as a Webpage card pointing to the photo frame widget, **Then** it auto-scales to the card container dimensions with responsive sizing.

---

### User Story 3 - Interactive Playback Controls & Child Filtering (Priority: P2)

As a family member viewing a photo, I want to tap on the photo to pause the slideshow or swipe/click next/previous to revisit a specific memory.

**Why this priority**: Allows families to stop and examine a fun activity or show a visiting grandparent a specific moment.

**Independent Test**: Tap the screen; verify rotation pauses and shows subtle play/pause and next/previous controls.

**Acceptance Scenarios**:
1. **Given** a playing slideshow, **When** the user taps the display, **Then** rotation pauses and navigation controls become visible.
2. **Given** a paused slideshow, **When** user taps play or does not interact for 60 seconds, **Then** automatic rotation resumes.

---

### User Story 4 - Background Auto-Refresh for New Stories (Priority: P2)

As a user, I want the photo frame to periodically query for newly scraped photos in the background without requiring a manual browser reload.

**Why this priority**: As the 3:00 AM job runs and downloads fresh stories, the morning kitchen display automatically features the new pictures.

**Independent Test**: Ingest a new story while the photo frame is active; verify that within the configured poll interval (e.g. 15 minutes), the new photo appears in the rotation queue.

**Acceptance Scenarios**:
1. **Given** the component is running on a kitchen wall tablet, **When** new photos are added by the scraper, **Then** the component silently updates its playlist without interrupting the currently displayed photo.

---

### Edge Cases

- **Mixed Aspect Ratios (Portrait vs Landscape)**: Kitchen displays are often fixed in landscape or portrait. Photos with differing aspect ratios must be presented cleanly (e.g. subtle blurred background letterboxing) rather than abruptly cropped or distorted.
- **Network Glitches / Offline Mode**: If the local network or webserver drops temporarily, the component MUST continue cycling through already-cached photos in browser memory rather than displaying an error or blank card.
- **Low-Power Wall Tablets**: Transitions must use hardware-accelerated CSS opacity transitions to ensure 60fps smoothness on budget Android/Fire tablets.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a self-contained custom web component (`<storypark-photo-frame>`) and a standalone URL (`/widget/photo-frame`) requiring no build tools or external CDN dependencies.
- **FR-002**: Component MUST fetch photo playlist from `GET /api/recent-photos` with configurable limit (default 20).
- **FR-003**: Component MUST support customizable attributes: `interval` (rotation duration in seconds), `transition-speed`, `child` (filter by child name), and `fit` (`contain` with blurred backdrop or `cover`).
- **FR-004**: Component MUST render clean, subtle typography displaying: Child Name, Story Title, and Published Date.
- **FR-005**: Component MUST support touch and click interactions: tap to toggle pause/play, forward/back navigation.
- **FR-006**: Component MUST periodically poll the feed API in the background (default every 15 minutes) and append newly scraped photos seamlessly.
- **FR-007**: Component MUST cache image assets in browser memory to survive transient network disconnections without disruption.
- **FR-008**: Component MUST support authentication tokens (`?token=...`) when connecting to secured scraper endpoints.

### Key Entities

- **PhotoFrameConfig**: Configuration attributes controlling display timing, transition effects, child filtering, and token authentication.
- **PlaylistItem**: Frontend image representation holding image source URL, caption, child tag, and date.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Transitions between photos animate smoothly at 60fps on typical wall-mounted tablets.
- **SC-002**: Fresh photos scraped during overnight runs automatically enter the kitchen display rotation within 15 minutes without manual refresh.
- **SC-003**: 100% functional in Home Assistant Lovelace Webpage / custom cards without external network or Internet dependencies.

## Assumptions

- Kitchen display device runs a browser supporting modern Web Components (HTML5 custom elements) and CSS transitions.
- The display has network connectivity to the Storypark scraper status webserver on the local network.
