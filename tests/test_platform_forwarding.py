"""__init__.async_setup_entry must forward the right platforms per connection type."""

from conftest import FakeEntry, run

from homeassistant.const import Platform

from custom_components.franklin_wh import async_setup_entry, async_unload_entry


class FakeHass:
    def __init__(self):
        self.data = {}
        self.forwarded = []
        self.unloaded = []
        outer = self

        class Entries:
            async def async_forward_entry_setups(self, entry, platforms):
                outer.forwarded.append(list(platforms))

            async def async_unload_platforms(self, entry, platforms):
                outer.unloaded.append(list(platforms))
                return True

        self.config_entries = Entries()


def forwarded(conn_type):
    hass = FakeHass()
    run(async_setup_entry(hass, FakeEntry({"connection_type": conn_type}), ))
    return hass.forwarded[0]


def test_modbus_only_forwards_sensor():
    assert forwarded("modbus") == [Platform.SENSOR]


def test_cloud_forwards_switch_too():
    assert Platform.SWITCH in forwarded("cloud")


def test_both_forwards_switch_too():
    assert Platform.SWITCH in forwarded("both")


def test_unload_matches_setup():
    for conn in ("modbus", "cloud", "both"):
        hass = FakeHass()
        entry = FakeEntry({"connection_type": conn})
        run(async_setup_entry(hass, entry))
        run(async_unload_entry(hass, entry))
        assert hass.unloaded[0] == hass.forwarded[0], conn
