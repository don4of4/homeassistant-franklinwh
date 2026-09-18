"""The export-limit control must preserve the export mode.

Third and last write path to the device. It uses read-modify-write, so these
tests pin that the mode survives and that the unlimited sentinel is deliberate
rather than accidental.
"""

from conftest import run

from franklin_wh import number as number_mod


class FakeMode:
    def __init__(self, name):
        self.name = name


class FakeSettings:
    def __init__(self, mode, limit):
        self.mode = mode
        self.limit_kw = limit


class FakeClient:
    def __init__(self, mode="SOLAR_ONLY", limit=5.0):
        self._settings = FakeSettings(FakeMode(mode), limit)
        self.writes = []

    async def get_export_settings(self):
        return self._settings

    async def set_export_settings(self, mode, limit_kw=None):
        self.writes.append({"mode": mode, "limit_kw": limit_kw})


class FakeCoordinator:
    def __init__(self):
        self.refreshed = 0

    async def async_refresh(self):
        self.refreshed += 1


def make_entity(client, max_kw=10.0):
    e = number_mod.ExportLimitNumber.__new__(number_mod.ExportLimitNumber)
    e._client = client
    e._attr_native_max_value = max_kw
    e.coordinator = FakeCoordinator()
    return e


def test_setting_a_limit_keeps_the_current_mode():
    client = FakeClient(mode="SOLAR_AND_APOWER")
    run(make_entity(client).async_set_native_value(3.5))
    assert len(client.writes) == 1
    assert client.writes[0]["limit_kw"] == 3.5
    assert client.writes[0]["mode"].name == "SOLAR_AND_APOWER", "mode must survive"


def test_max_value_means_unlimited():
    client = FakeClient()
    run(make_entity(client, max_kw=10.0).async_set_native_value(10.0))
    assert client.writes[0]["limit_kw"] is None


def test_above_max_also_means_unlimited():
    client = FakeClient()
    run(make_entity(client, max_kw=10.0).async_set_native_value(99.0))
    assert client.writes[0]["limit_kw"] is None


def test_just_below_max_is_a_real_cap():
    client = FakeClient()
    run(make_entity(client, max_kw=10.0).async_set_native_value(9.9))
    assert client.writes[0]["limit_kw"] == 9.9


def test_state_is_refreshed_after_writing():
    client = FakeClient()
    entity = make_entity(client)
    run(entity.async_set_native_value(2.0))
    assert entity.coordinator.refreshed == 1
