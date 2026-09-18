# Changelog

## 2026.9.0
- Standard HACS layout (`custom_components/franklin_wh/`). Fixes the top-level `select.py` shadowing Python's `select` module.
- The cloud client is now vendored (`franklin_wh/api/`) from richo/franklinwh-python main, so no package is installed at startup and fixes ship with the integration.
- Mode changes preserve Storm Hedge, and use the gateway's own profile ids. Closes #1.
- Mode detection reads the TOU profile list; `runingMode` guessing is only a fallback. Closes #2.
- `get_stats()` raises `InvalidDataException` instead of `TypeError` on a null API result. Closes #3.
- Manifest and README point at this repository.

## 2026.8.0
- Mode changes read the target mode's own reserve SOC instead of writing a library default. Reported in richo/homeassistant-franklinwh#82.
- Export mode changes read the current limit from the device instead of cached data.
- Errors name the exception type.
- 42 tests, covering every path that writes to the device.

## 2026.7.3
- Transient cloud failures are retried and fall back to the last good value.
- Unknown `runingMode` warns once, with the reserve values alongside.
- Test suite added.

## 2026.7.2
- Cloud-only entities get unique IDs.
- A modbus entry without a host reports a clear error instead of `KeyError: 'host'`.

## 2026.7.0
- Battery power int16 overflow resolved jointly with grid power.

## 2026.6.1
- Grid power int16 overflow corrected against the energy balance.

## 2026.6.0
- Cloud sensors load in cloud-only and hybrid mode; the config entry previously loaded Modbus only.

## 2026.5.0
- Hybrid mode: config entries for select and number platforms.

## 2026.4.1
- Modbus entity names match the cloud sensors.
