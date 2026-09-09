# Phase 0 Research: Home Assistant MQTT Discovery & Alerting Integration

## 1. Protocol & Ecosystem Standards

### Home Assistant MQTT Discovery
Home Assistant implements an automated MQTT discovery convention where devices announce their capabilities by publishing JSON schemas to predefined topics under a discovery prefix (default `homeassistant/`).

* **Entity Types**:
  - `sensor`: For continuous or discrete text/numerical values (e.g. Scraper Status, Last Run Timestamp, Story Count, Photo Count).
  - `binary_sensor`: For boolean states, specifically utilizing `device_class: problem` for error and failure alerts.

* **Unified Device Schema**:
  Every sensor discovery message will include a common `device` dictionary:
  ```json
  "device": {
    "identifiers": ["storypark_scraper"],
    "name": "Storypark Scraper",
    "manufacturer": "Marknel",
    "model": "Storypark Scraper",
    "sw_version": "1.0.0"
  }
  ```
  This groups all entities under a single device card in Home Assistant's UI.

* **Atomic Telemetry via JSON State Topic**:
  Rather than publishing to 10 distinct state topics on every run, the scraper will publish a single retained JSON state payload to `storypark/state`:
  ```json
  {
    "status": "success",
    "problem": "OFF",
    "last_run": "2026-09-09T17:00:44Z",
    "duration_seconds": 44.2,
    "stories_total": 434,
    "photos_total": 931,
    "last_run_stories": 10,
    "last_run_photos": 0,
    "sam_stories": 369,
    "sam_photos": 589,
    "lyra_stories": 65,
    "lyra_photos": 342,
    "error": null
  }
  ```
  Each discovered sensor specifies a `value_template` (e.g. `{{ value_json.status }}` or `{{ value_json.problem }}`), ensuring that Home Assistant updates all sensors synchronously with zero topic fragmentation.

### Availability & Last Will and Testament (LWT)
- **Topic**: `storypark/availability`
- **Online Payload**: `online` (published upon connection and run start)
- **Offline Payload**: `offline` (set as MQTT Last Will and Testament)

---

## 2. Library Selection & Compatibility

* **Selected Library**: `paho-mqtt>=2.0.0`
* **Rationale**:
  - Standard, battle-tested Python MQTT client.
  - Supports Protocol v3.1.1 and v5.0.
  - Native support for TLS/SSL, username/password, and automatic reconnection.
  - Single-call helper `paho.mqtt.publish.single` / `multiple` allows lightweight one-shot publishing for scheduled tasks without maintaining a permanent blocking connection.
* **Environment Impact**:
  - Pure Python (minimal footprint).
  - Works identically on Windows, Linux, and inside Docker.

---

## 3. Security & Resilience

* **Credential Storage**:
  - MQTT passwords can be configured directly in `config.yaml`, loaded from `.env` (`SP_MQTT_PASSWORD`), or retrieved securely from Windows Credential Manager / DPAPI target `storypark-scraper-mqtt`.
* **Zero Disruption Guarantee**:
  - All MQTT network calls are wrapped in defensive try/except blocks with timeout limits (default 5s).
  - If the MQTT broker is unreachable or drops mid-scrape, the scraper logs a warning and completes the scrape normally. The primary job of saving children memories is never blocked by a home automation network issue.

---

## 4. Configuration Schema Design

```yaml
mqtt:
  enabled: false
  host: "192.168.1.100"
  port: 1883
  username: ""
  password: ""
  tls: false
  discovery_prefix: "homeassistant"
  topic_prefix: "storypark"
  retain: true
  timeout_seconds: 5
```
