# Quickstart: Home Assistant MQTT Integration

## 1. Prerequisites
- An active MQTT broker (such as the official Mosquitto broker add-on in Home Assistant).
- Home Assistant MQTT integration enabled with "Enable discovery" checked (default).

## 2. Configuration (`config.yaml`)

Add the `mqtt` configuration section:

```yaml
mqtt:
  enabled: true
  host: "192.168.1.50"          # IP or hostname of your MQTT broker
  port: 1883
  username: "homeassistant"      # Or dedicated MQTT user
  password: "your_mqtt_password" # Or set in Windows Credential Manager / SP_MQTT_PASSWORD
  tls: false
  discovery_prefix: "homeassistant"
  topic_prefix: "storypark"
  retain: true
  timeout_seconds: 5
```

## 3. Testing Discovery & Telemetry

Test publishing discovery and current status from the CLI:

```bash
# Verify connectivity and publish discovery configs
python -m storypark_scraper mqtt-publish --config config.yaml
```

## 4. Verification in Home Assistant

1. In Home Assistant, open **Settings** -> **Devices & Services** -> **MQTT**.
2. Under "Devices", verify the **Storypark Scraper** device appears with:
   - Status sensor (`idle`, `running`, `success`, `failed`, `interrupted`)
   - Problem binary sensor (`Off` when healthy, `On` on failure)
   - Last run timestamp & duration
   - Total stories & photos
   - Per-child counters for Sam & Lyra

3. Example Automation for Failure Alerts:
```yaml
alias: "Storypark Scraper Failure Notification"
trigger:
  - platform: state
    entity_id: binary_sensor.storypark_problem
    to: "on"
action:
  - service: notify.mobile_app_parents
    data:
      title: "⚠️ Storypark Scraper Alert"
      message: "The daycare scraper failed during its last run! Check logs on main-pc."
```
