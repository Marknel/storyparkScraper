# Data Model: Home Assistant MQTT Discovery & Alerting Integration

## 1. Internal Domain Entities

### `MQTTConfig` (Configuration Dataclass)
```python
@dataclass(slots=True)
class MQTTConfig:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 1883
    username: str = ""
    password: str = ""
    tls: bool = False
    discovery_prefix: str = "homeassistant"
    topic_prefix: str = "storypark"
    retain: bool = True
    timeout_seconds: int = 5
```

### `MQTTTelemetryPayload` (Status Telemetry Payload)
```python
@dataclass(slots=True)
class MQTTTelemetryPayload:
    status: str                         # "idle" | "running" | "success" | "failed" | "interrupted"
    problem: str                        # "ON" | "OFF"
    last_run: str                       # ISO 8601 UTC timestamp
    duration_seconds: float             # Execution duration
    stories_total: int                  # Total stories recorded in DB
    photos_total: int                   # Total photos recorded in DB
    last_run_stories: int               # Stories processed during last run
    last_run_photos: int                # Photos downloaded during last run
    error: str | None = None            # Diagnostic error string if failed
    children: dict[str, dict[str, Any]] = field(default_factory=dict)
```

---

## 2. Home Assistant MQTT Entity Model

| Entity ID | Component | Device Class | State / Values | Description |
|---|---|---|---|---|
| `sensor.storypark_status` | `sensor` | `enum` | `idle`, `running`, `success`, `failed`, `interrupted` | Scraper operational state |
| `binary_sensor.storypark_problem` | `binary_sensor` | `problem` | `ON` (error) / `OFF` (healthy) | Urgent failure alert for HA automations |
| `sensor.storypark_last_run` | `sensor` | `timestamp` | ISO 8601 UTC string | Timestamp of last run |
| `sensor.storypark_last_run_duration` | `sensor` | `duration` | Floating point seconds | Run duration |
| `sensor.storypark_stories_total` | `sensor` | None | Integer counter | Cumulative stories archived |
| `sensor.storypark_photos_total` | `sensor` | None | Integer counter | Cumulative photos archived |
| `sensor.storypark_last_run_stories` | `sensor` | None | Integer | Stories seen in the last run |
| `sensor.storypark_last_run_photos` | `sensor` | None | Integer | Photos downloaded in the last run |
| `sensor.storypark_<child>_stories` | `sensor` | None | Integer | Per-child story count (e.g. Sam, Lyra) |
| `sensor.storypark_<child>_photos` | `sensor` | None | Integer | Per-child photo count (e.g. Sam, Lyra) |

---

## 3. MQTT Topic Hierarchy

```text
<topic_prefix>/
├── availability               # "online" | "offline"
├── state                      # Complete JSON telemetry payload
└── error                      # Detailed stack trace or error message if failed

<discovery_prefix>/
├── sensor/
│   ├── storypark_status/config
│   ├── storypark_last_run/config
│   ├── storypark_last_run_duration/config
│   ├── storypark_stories_total/config
│   ├── storypark_photos_total/config
│   ├── storypark_sam_stories/config
│   ├── storypark_sam_photos/config
│   ├── storypark_lyra_stories/config
│   └── storypark_lyra_photos/config
└── binary_sensor/
    └── storypark_problem/config
```
