# Feature Specification: Home Assistant MQTT Discovery & Alerting Integration

**Feature Branch**: `002-home-assistant-mqtt`
**Created**: 2026-09-09
**Status**: Draft
**Input**: "pushing a Home Assistant integration (or whatever is the right fit - MQTT?) ... plus an alert if something breaks."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Home Assistant Automatic Entity Discovery (Priority: P1)

As a Home Assistant user, I want the Storypark scraper to automatically register a "Storypark Scraper" device with sensors in Home Assistant via standard MQTT Discovery so that I do not need to manually configure YAML entities.

**Why this priority**: Core value of zero-configuration Home Assistant integration.

**Independent Test**: Connect scraper to an MQTT broker, trigger an initial status publish, and verify that Home Assistant's MQTT integration automatically creates the "Storypark Scraper" device with sensors.

**Acceptance Scenarios**:
1. **Given** an active MQTT broker configured in `config.yaml`, **When** the scraper or status daemon starts, **Then** MQTT Discovery payloads are published under `homeassistant/sensor/storypark/...` with device metadata.
2. **Given** Home Assistant listens on the broker, **When** discovery payloads are received, **Then** entities `sensor.storypark_scraper_status`, `sensor.storypark_last_run`, and child story count sensors appear in Home Assistant.

---

### User Story 2 - Failure & Problem Alerting (Priority: P1)

As a parent and home automation operator, I want an alert binary sensor in Home Assistant that turns ON whenever a scrape fails, a run crashes, or an album is missing, so that I receive an immediate phone notification rather than discovering days later that scraping stopped.

**Why this priority**: Direct user request ("plus an alert if something breaks"); provides peace of mind that daycare memories are reliably collected.

**Independent Test**: Simulate a failed scrape run (e.g. bad credentials or network disconnect) and verify `binary_sensor.storypark_problem` changes from `OFF` to `ON` with the error description in the entity attributes.

**Acceptance Scenarios**:
1. **Given** a scraper run fails due to an error, **When** the run completes with status `failed` or `interrupted`, **Then** the MQTT topic for `binary_sensor.storypark_problem` publishes `ON` with diagnostic attributes.
2. **Given** a subsequent run succeeds, **When** the run finishes, **Then** `binary_sensor.storypark_problem` resets to `OFF`.

---

### User Story 3 - Per-Child Story and Photo Tallies (Priority: P2)

As a parent, I want sensors tracking the latest story title, total stories, and total photos downloaded for Sam and Lyra individually so that I can show child-specific counters on dashboard views.

**Why this priority**: Enables fine-grained dashboard cards for each child.

**Independent Test**: Run a scrape that discovers 1 new story for Lyra, verify `sensor.storypark_lyra_photos` increments and `sensor.storypark_lyra_latest_story` updates with the title.

**Acceptance Scenarios**:
1. **Given** new stories are ingested for a child, **When** the run finishes, **Then** MQTT sensors for that child publish updated story and photo counts and the latest story title and timestamp.

---

### User Story 4 - Availability and Offline Reporting (Priority: P3)

As a system administrator, I want Home Assistant to show the scraper integration as "unavailable" if the daemon or host is offline.

**Why this priority**: Prevents stale sensor readings if the VM goes down.

**Independent Test**: Terminate the daemon process and check that the broker publishes the LWT offline message, causing Home Assistant entities to display "unavailable".

**Acceptance Scenarios**:
1. **Given** the scraper service is running, **When** it shuts down or disconnects, **Then** MQTT availability topic transitions from `online` to `offline`.

---

### Edge Cases

- **MQTT Broker Offline**: If the broker is unreachable, the scraper MUST log a warning and continue normal scraping and archiving without crashing.
- **Network Flaps**: Automatic reconnection with exponential retry backoff.
- **Retained State**: State topics must use `retain: true` so Home Assistant retains current metrics across Home Assistant restarts.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST support configuration for MQTT connection (`host`, `port`, `username`, `password`, `tls`, `discovery_prefix`, `topic_prefix`).
- **FR-002**: System MUST publish Home Assistant MQTT Discovery configuration messages under the configured discovery prefix (default `homeassistant/`).
- **FR-003**: System MUST group all entities under a unified Home Assistant Device named "Storypark Scraper" with manufacturer, model, and software version attributes.
- **FR-004**: System MUST expose `sensor.storypark_status` with states `idle`, `running`, `success`, `failed`, `interrupted`.
- **FR-005**: System MUST expose `binary_sensor.storypark_problem` with `device_class: problem`, turning `ON` upon failure, run crash, or critical warning, and `OFF` upon successful completion.
- **FR-006**: System MUST expose per-child sensors: story count, photo count, and latest story title.
- **FR-007**: System MUST support publishing at the conclusion of each scraping run and optionally periodically via the status webserver/daemon.
- **FR-008**: System MUST support MQTT Last Will and Testament (LWT) for the availability topic (`online`/`offline`).
- **FR-009**: System MUST securely read MQTT credentials from Windows Credential Manager / DPAPI or environment variables.

### Key Entities

- **MQTTSensorConfig**: Home Assistant discovery schema object defining unique ID, name, state topic, device class, and device linkage.
- **ScraperTelemetry**: Telemetry payload containing run status, timestamps, child statistics, and error diagnostic logs.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Home Assistant automatically discovers all sensors and devices within 10 seconds of initial MQTT connection without manual Home Assistant YAML.
- **SC-002**: Failure alert binary sensor transitions to `ON` within 2 seconds of a failed run completion.
- **SC-003**: Unavailability of the MQTT broker causes 0 disruptions to core Storypark scraping, downloading, or local archiving.

## Assumptions

- The user operates an MQTT broker (e.g. Mosquitto, EMQX, or Home Assistant Mosquitto add-on) accessible on the local network.
- Home Assistant MQTT integration is enabled with MQTT discovery turned on (default HA behavior).
