# FranklinWH for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-blue.svg?style=for-the-badge)](https://github.com/hacs/integration)

Monitoring and control for FranklinWH home batteries: local Modbus TCP for telemetry, the cloud API for control. Unofficial; not affiliated with FranklinWH.

This is the maintained continuation of [richo/homeassistant-franklinwh](https://github.com/richo/homeassistant-franklinwh), with the [cloud client](custom_components/franklin_wh/api/) vendored from [richo/franklinwh-python](https://github.com/richo/franklinwh-python). Both use the same `franklin_wh` domain, so **uninstall the richo version before installing this one** — entity IDs and history carry over.

## Install

HACS → Integrations → ⋮ → Custom repositories → add `https://github.com/don4of4/homeassistant-franklinwh` as an Integration. Install, restart Home Assistant, then Settings → Devices & Services → Add Integration → FranklinWH.

## Connection types

| | Local (Modbus) | Cloud | Local + Cloud |
|---|---|---|---|
| Live telemetry | 30 s, ~60 ms latency, no internet needed | 5 min, rate limited | Modbus |
| Energy totals (Energy dashboard) | — | yes | Cloud |
| Mode / export control | — | yes | Cloud |
| Needs | aGate IP, Modbus enabled | account + gateway ID | both |

**Modbus:** in the FranklinWH app enable the **SPAN panel** toggle — it turns on the Modbus TCP listener on port 502; no SPAN hardware needed. If the toggle isn't offered on your firmware, use Cloud.

**Gateway ID:** FranklinWH app → Settings → Device Info → SN.

## Entities

Local (Modbus): battery SOC, grid / home / battery / solar power, grid voltage and frequency, ambient and cabinet temperature, grid status, operating mode, reserve SOC, battery DC voltage.

Cloud: the energy totals (grid import/export, home use, solar, battery charge/discharge, switch and V2L totals), generator and switch power, plus the controls — operating mode, export mode, export limit. In Local + Cloud, the live power sensors come from Modbus only.

Grid and battery power are signed 16-bit registers on the aGate, so anything above 32.767 kW wraps; the integration resolves that against the energy balance.

## Troubleshooting

- **Modbus can't connect** — check the IP and the SPAN toggle.
- **Cloud: mode shows unknown** — please open an issue with the `franklin_wh` log lines; your firmware may report profile IDs we haven't seen.
- **Code 181 / rate limited** — backs off automatically, 2–30 minutes.
- Logs: Settings → System → Logs, filter `franklin_wh`.

## Development

Tests run inside a Home Assistant container, which already has every dependency:

```sh
tools/run-tests.sh            # FWH_CONTAINER=<name> to pick a container; CI runs the same suite
```

The client under `api/` is synced by diffing against the upstream commit noted in its `__init__.py`. Nothing in this integration calls the installer-only `/manage/` endpoints; please keep it that way — probing them got an account disassociated from its device.

## Credits and license

Original integration and client library by [@richo](https://github.com/richo); Modbus register map validated against [mtnears/FranklinWH-Automation](https://github.com/mtnears/FranklinWH-Automation); TOU profile work by [@cd34](https://github.com/cd34) and [@npdsomerhayes](https://github.com/npdsomerhayes). Dual-licensed MIT / Apache-2.0; the vendored client is MIT.
