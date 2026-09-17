"""sensor.async_setup_entry must dispatch on connection_type.

Regression test for richo/homeassistant-franklinwh#82: it delegated to the
Modbus setup unconditionally, so a cloud-only entry raised KeyError: 'host'
and produced no entities at all.
"""

import pytest
from conftest import run

from franklin_wh import modbus as modbus_mod
from franklin_wh import sensor as sensor_mod


@pytest.fixture
def calls(monkeypatch):
    """Record which setup paths run, without touching network or HA."""
    seen = {"modbus": [], "cloud": []}

    async def fake_modbus(hass, entry, add):
        seen["modbus"].append(entry)

    async def fake_cloud(hass, add, username, password, gateway, prefix, unique_id,
                         interval, *, tolerate_stale_data, skip_modbus_duplicates):
        seen["cloud"].append(
            {"gateway": gateway, "unique_id": unique_id, "username": username,
             "skip_modbus_duplicates": skip_modbus_duplicates}
        )

    monkeypatch.setattr(modbus_mod, "async_setup_entry", fake_modbus)
    monkeypatch.setattr(sensor_mod, "_async_add_cloud_sensors", fake_cloud)
    return seen


def test_cloud_only_never_touches_modbus(calls, cloud_entry):
    run(sensor_mod.async_setup_entry(None, cloud_entry, None))
    assert calls["modbus"] == [], "cloud-only must not run the Modbus setup"
    assert len(calls["cloud"]) == 1


def test_modbus_only_never_touches_cloud(calls, modbus_entry):
    run(sensor_mod.async_setup_entry(None, modbus_entry, None))
    assert len(calls["modbus"]) == 1
    assert calls["cloud"] == [], "modbus-only must not call the cloud API"


def test_both_runs_both_and_skips_duplicates(calls, both_entry):
    run(sensor_mod.async_setup_entry(None, both_entry, None))
    assert len(calls["modbus"]) == 1
    assert len(calls["cloud"]) == 1
    # Modbus already serves the live power sensors in hybrid mode.
    assert calls["cloud"][0]["skip_modbus_duplicates"] is True


def test_missing_connection_type_defaults_to_cloud(calls):
    from conftest import FakeEntry

    entry = FakeEntry({"username": "u", "password": "p", "gateway_id": "GW1"})
    run(sensor_mod.async_setup_entry(None, entry, None))
    assert calls["modbus"] == []
    assert len(calls["cloud"]) == 1


def test_cloud_only_entities_get_a_unique_id(calls, cloud_entry):
    """The cloud flow stores gateway_id but never serial.

    unique_id used to come from serial alone, so every cloud-only entity was
    created without one and never reached the entity registry.
    """
    run(sensor_mod.async_setup_entry(None, cloud_entry, None))
    assert calls["cloud"][0]["unique_id"] == "GW123456789"


def test_hybrid_prefers_serial_for_unique_id(calls, both_entry):
    run(sensor_mod.async_setup_entry(None, both_entry, None))
    assert calls["cloud"][0]["unique_id"] == "SN987654321"
