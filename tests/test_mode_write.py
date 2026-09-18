"""Writing an operating mode must never invent a reserve SOC.

richo/homeassistant-franklinwh#82: switching to Self Consumption reset the
user's reserve from 33% to 20%. _read_operating_mode returned None for their
unrecognised runingMode, async_select_option then passed no soc, and
franklinwh's Mode factories default to 20 (100 for emergency backup) — which
set_mode duly wrote to the battery.
"""

import pytest
from conftest import run

from homeassistant.exceptions import HomeAssistantError

from franklin_wh import select as select_mod


class FakeClient:
    """Records what would be sent to the device."""

    def __init__(self, switch_status):
        self._switch_status_data = switch_status
        self.modes_set = []

    async def _switch_status(self):
        return self._switch_status_data

    async def set_mode(self, mode):
        self.modes_set.append(mode)


class FakeCoordinator:
    def __init__(self, data):
        self.data = data

    def async_write_ha_state(self):
        pass


def make_entity(client, current_mode, reserve=None):
    entity = select_mod.OperatingModeSelect.__new__(select_mod.OperatingModeSelect)
    entity.coordinator = FakeCoordinator(
        {"operating_mode": current_mode, "reserve_soc": reserve}
    )
    entity._client = client
    entity.async_write_ha_state = lambda: None
    return entity


# The reserves the device reports, all three populated at once — exactly what
# #82's log showed alongside the unrecognised runingMode.
DEVICE = {"runingMode": 75616, "selfMinSoc": 33, "touMinSoc": 25, "backupMaxSoc": 100}


def test_switching_preserves_that_modes_own_reserve():
    client = FakeClient(DEVICE)
    run(make_entity(client, "time_of_use").async_select_option("self_consumption"))
    assert len(client.modes_set) == 1
    assert client.modes_set[0].soc == 33, "must send the device's own selfMinSoc"


def test_never_writes_the_library_default_when_mode_is_unknown():
    """The exact #82 scenario: current mode undetectable, reserve unknown."""
    client = FakeClient(DEVICE)
    entity = make_entity(client, None, reserve=None)
    run(entity.async_select_option("self_consumption"))
    assert client.modes_set[0].soc == 33
    assert client.modes_set[0].soc != 20, "20 is the library default that caused #82"


def test_each_mode_reads_its_own_reserve():
    for option, expected in (
        ("self_consumption", 33),
        ("time_of_use", 25),
        ("emergency_backup", 100),
    ):
        client = FakeClient(DEVICE)
        run(make_entity(client, "unknown_mode").async_select_option(option))
        assert client.modes_set[0].soc == expected, option


def test_refuses_to_write_when_the_reserve_is_unreadable():
    """Better to fail loudly than to overwrite the reserve with a guess."""
    client = FakeClient({"runingMode": 75616})  # no reserve fields at all
    entity = make_entity(client, "time_of_use")
    with pytest.raises(HomeAssistantError) as err:
        run(entity.async_select_option("self_consumption"))
    assert client.modes_set == [], "nothing may be written to the device"
    assert "selfMinSoc" in str(err.value)


def test_selecting_the_current_mode_writes_nothing():
    """Every set_mode also forces Storm Hedge on, so a no-op must not write."""
    client = FakeClient(DEVICE)
    run(make_entity(client, "self_consumption").async_select_option("self_consumption"))
    assert client.modes_set == []


def test_unknown_option_raises_and_writes_nothing():
    client = FakeClient(DEVICE)
    with pytest.raises(HomeAssistantError):
        run(make_entity(client, "time_of_use").async_select_option("nonsense"))
    assert client.modes_set == []


def test_mode_factories_produce_the_right_work_mode():
    """Guards against the factory map being wired to the wrong mode."""
    expected = {"self_consumption": 2, "time_of_use": 1, "emergency_backup": 3}
    for option, work_mode in expected.items():
        assert select_mod._MODE_FACTORIES[option](soc=50).workMode == work_mode


def test_payload_still_carries_the_soc_we_chose():
    """End-to-end through the library's payload builder."""
    client = FakeClient(DEVICE)
    run(make_entity(client, "time_of_use").async_select_option("self_consumption"))
    payload = client.modes_set[0].payload("GW1")
    assert payload["soc"] == "33"
    # Documents the upstream bug we warn about: Storm Hedge is forced on.
    assert payload["stromEn"] == "1"
