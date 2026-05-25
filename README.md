# FranklinWH Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-blue.svg?style=for-the-badge)](https://github.com/hacs/integration)

This is a custom integration for [Home Assistant](https://www.home-assistant.io/) that provides monitoring and control for FranklinWH home energy storage systems.

> ⚠️ This project is unofficial and not affiliated with FranklinWH.

---

## Features

### Local Modbus TCP (Recommended)
- **26-60ms response times** — reads data directly from the aGate on your LAN
- **No cloud dependency** — works during internet outages and API lockouts
- Auto-detects gateway model, serial, and firmware during setup
- 13 real-time sensor entities (see table below)

### Cloud API
- Live battery status (SoC, charging/discharging)
- Solar production, grid import/export, generator, home load, switch loads, V2L
- **Operating mode control** (Self Consumption / Time of Use / Emergency Backup)
- **Grid export mode control** (Solar Only / Solar + Battery / No Export)
- **Grid export power limit** (kW cap on grid feed)

---

## Installation

### Via HACS (Recommended)

1. In Home Assistant, go to **HACS → Integrations**.
2. Click the menu (⋮) → **Custom repositories**.
3. Add this repository URL: `https://github.com/don4of4/homeassistant-franklinwh`
4. Choose category **Integration** and click **Add**.
5. Install the **FranklinWH** integration from the list.
6. Restart Home Assistant.

### Manual Installation

1. Download this repository as a ZIP.
2. Extract to your Home Assistant `custom_components/franklin_wh/` directory.
3. Restart Home Assistant.

---

## Setup

### UI Setup (Config Flow)

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **FranklinWH**
3. Choose your connection type:

#### Local (Modbus TCP) — Recommended
- Enter the IP address of your aGate
- The integration auto-detects your gateway and creates sensors
- **Prerequisite:** Modbus TCP must be enabled on your aGate. In the FranklinWH app, enable the **SPAN panel toggle** (no SPAN hardware required — this just enables the Modbus TCP listener on port 502).

#### Cloud API
- Enter your FranklinWH account email, password, and Gateway ID
- Gateway ID is found in the FranklinWH app: **Settings → Device Info → SN**

### YAML Configuration (Legacy)

YAML configuration is still supported for backwards compatibility. See [YAML Configuration](#yaml-configuration-legacy-1) below.

---

## Modbus Sensor Entities

These sensors are available when using the Local (Modbus) connection:

| Entity | Description | Unit |
|--------|-------------|------|
| FranklinWH Battery SOC | Battery state of charge | % |
| FranklinWH Grid Power | Grid import (+) / export (-) | kW |
| FranklinWH Home Power | Home consumption | kW |
| FranklinWH Battery Power | Battery charge (-) / discharge (+) | kW |
| FranklinWH Solar Power | Solar production | kW |
| FranklinWH Grid Voltage | Grid voltage | V |
| FranklinWH Grid Frequency | Grid frequency | Hz |
| FranklinWH Ambient Temperature | Outdoor / ambient temp | °C |
| FranklinWH Cabinet Temperature | Internal cabinet temp | °C |
| FranklinWH Grid Status | Grid connection (Connected / Disconnected) | — |
| FranklinWH Operating Mode (Local) | Current mode (TOU / SC / EB) | — |
| FranklinWH Reserve SOC (Local) | Active reserve setting | % |
| FranklinWH Battery DC Voltage | Battery DC bus voltage | V |

### Modbus Register Sources

Register mappings are based on the SunSpec 700-series DER models and FranklinWH vendor extension registers, validated by the [mtnears/FranklinWH-Automation](https://github.com/mtnears/FranklinWH-Automation) project against ~300,000 readings.

| Source | Registers | Data |
|--------|-----------|------|
| Model 713 (addr 1035) | SOC, battery DC voltage | Battery status |
| Model 714 (addr 1048) | Battery charge/discharge watts | Battery power |
| Model 701 (addr 72) | Grid power, voltage, frequency, temps | AC measurements |
| Extended (addr 15500+) | Solar, home load, mode, reserve | Vendor-specific |

---

## Cloud API Entities

These entities are available when using the Cloud API connection:

| Entity | Description | Unit |
|--------|-------------|------|
| FranklinWH State of Charge | Battery state of charge | % |
| FranklinWH Battery Use | Battery charging/discharging rate | kW |
| FranklinWH Battery Charge | Total energy charged to battery | kWh |
| FranklinWH Battery Discharge | Total energy discharged from battery | kWh |
| FranklinWH Home Load | Instantaneous home power use | kW |
| FranklinWH Home Use | Total energy consumed by home | kWh |
| FranklinWH Grid Use | Net grid power usage | kW |
| FranklinWH Grid Import | Total energy imported from grid | kWh |
| FranklinWH Grid Export | Total energy exported to grid | kWh |
| FranklinWH Solar Production | Instantaneous solar power | kW |
| FranklinWH Solar Energy | Total solar energy produced | kWh |
| FranklinWH Generator Use | Generator power output | kW |
| FranklinWH Switch 1/2 Load | Power draw on smart relays | W |
| FranklinWH V2L Use/Import/Export | Vehicle-to-Load data | W/Wh |
| FranklinWH Operating Mode | Select operating mode | — |
| FranklinWH Export Mode | Select grid export mode | — |
| FranklinWH Export Limit | Grid export power cap | kW |

---

## YAML Configuration (Legacy)

> 💡 For security, store your password in `secrets.yaml`.

```yaml
# Cloud API sensors
sensor:
  - platform: franklin_wh
    username: "email@domain.com"
    password: !secret franklinwh_password
    id: "100xxxxxxxxxxxx"
    tolerate_stale_data: true

# OR: Local Modbus sensors (no cloud credentials needed)
sensor:
  - platform: franklin_wh
    host: "192.168.1.100"
    id: "100xxxxxxxxxxxx"

# Operating mode + export control (cloud API required)
select:
  - platform: franklin_wh
    username: "email@domain.com"
    password: !secret franklinwh_password
    id: "100xxxxxxxxxxxx"

number:
  - platform: franklin_wh
    username: "email@domain.com"
    password: !secret franklinwh_password
    id: "100xxxxxxxxxxxx"
    max_export_kw: 10.0
```

---

## Troubleshooting

- **Modbus: "Cannot connect"** — Verify the aGate IP and that Modbus is enabled (Franklin app → SPAN toggle).
- **Cloud: No entities appear** — Confirm username, password, and gateway ID. Check that FranklinWH cloud services are online.
- **Rate limited (code 181)** — The integration has automatic exponential backoff. Wait 2-30 minutes for it to recover.
- **Logs** — Check Settings → System → Logs for errors containing `franklin_wh`.

---

## Contributing

Contributions are welcome! Please fork and open a pull request.

- Upstream: [richo/homeassistant-franklinwh](https://github.com/richo/homeassistant-franklinwh)
- This fork: [don4of4/homeassistant-franklinwh](https://github.com/don4of4/homeassistant-franklinwh)

## License

Dual-licensed under the MIT License and the Apache License 2.0.
