"""Changing export mode must not clear the export cap.

set_export_settings treats limit_kw=None as *unlimited*, so writing without
reading the current limit silently removes a configured cap — the same class of
bug as the reserve-SOC reset in #82.
"""

import pytest
from conftest import run

from homeassistant.exceptions import HomeAssistantError

from custom_components.franklin_wh import select as select_mod


class FakeClient:
    def __init__(self, mode="solar_only", limit=5.0, raises=None):
        self._mode = mode
        self._limit = limit
        self._raises = raises
        self.writes = []

    async def get_export_settings(self):
        if self._raises:
            raise self._raises
        class S:
            pass
        s = S()
        s.mode = type("M", (), {"name": self._mode.upper()})()
        s.limit_kw = self._limit
        return s

    async def set_export_settings(self, mode, limit_kw=None):
        self.writes.append({"mode": mode, "limit_kw": limit_kw})


class FakeCoordinator:
    def __init__(self, data):
        self.data = data


def make_entity(client, current_mode):
    e = select_mod.ExportModeSelect.__new__(select_mod.ExportModeSelect)
    e.coordinator = FakeCoordinator({"export_mode": current_mode, "export_limit_kw": None})
    e._client = client
    e.async_write_ha_state = lambda: None
    return e


def test_existing_limit_is_preserved():
    """Coordinator data says None; the device says 5 kW. The device wins."""
    client = FakeClient(mode="solar_only", limit=5.0)
    run(make_entity(client, "no_export").async_select_option("solar_and_apower"))
    assert len(client.writes) == 1
    assert client.writes[0]["limit_kw"] == 5.0, "a configured cap must survive"


def test_refuses_when_the_limit_cannot_be_read():
    client = FakeClient(raises=RuntimeError("api down"))
    with pytest.raises(HomeAssistantError) as err:
        run(make_entity(client, "no_export").async_select_option("solar_only"))
    assert client.writes == [], "nothing may be written blind"
    assert "export cap" in str(err.value)


def test_zero_limit_from_no_export_is_not_carried_over():
    """no_export stores 0.0, outside the valid 0.1-10000 range."""
    client = FakeClient(mode="no_export", limit=0.0)
    run(make_entity(client, "no_export").async_select_option("solar_only"))
    assert client.writes[0]["limit_kw"] is None


def test_selecting_the_current_mode_writes_nothing():
    client = FakeClient()
    run(make_entity(client, "solar_only").async_select_option("solar_only"))
    assert client.writes == []


def test_unknown_mode_raises_and_writes_nothing():
    client = FakeClient()
    with pytest.raises(HomeAssistantError):
        run(make_entity(client, "solar_only").async_select_option("nonsense"))
    assert client.writes == []
