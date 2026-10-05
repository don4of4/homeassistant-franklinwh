"""The operating-mode select prefers the local connection and falls back.

Local is preferred because it needs no cloud login (the part that breaks),
selects the gateway's own programme so a reserve cannot be overwritten, and is
confirmed by a read-back. The cloud path must still work unchanged when there
is no local connection or it fails.
"""

import pytest
from conftest import run

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.franklin_wh import select as select_mod
from custom_components.franklin_wh.local import LocalGatewayError

MODES = {
    "current_id": 70189,
    "list": [
        {"id": 135494, "name": "Emergency Backup", "reserved_soc": 100, "scheduling_type": 3},
        {"id": 70189, "name": "TOU", "reserved_soc": 35, "scheduling_type": 1},
    ],
}


class FakeLocal:
    def __init__(self, *, modes=MODES, read_error=None, set_error=None):
        self._modes, self._read_error, self._set_error = modes, read_error, set_error
        self.sets = []

    async def mode_list(self):
        if self._read_error:
            raise self._read_error
        return self._modes

    async def set_mode(self, mode):
        self.sets.append(mode)
        if self._set_error:
            raise self._set_error


class FakeCloud:
    def __init__(self, fail=None):
        self.fail = fail
        self.modes_set = []
        self.calls = []

    async def _switch_status(self):
        self.calls.append("_switch_status")
        if self.fail:
            raise self.fail
        return {"selfMinSoc": 33, "touMinSoc": 25, "backupMaxSoc": 100}

    async def get_tou_settings(self):
        self.calls.append("get_tou_settings")
        raise RuntimeError("no tou list in this fake")

    async def set_mode(self, mode):
        self.modes_set.append(mode)


class FakeCoordinator:
    def __init__(self, data):
        self.data = data
        self.refreshes = 0

    async def async_request_refresh(self):
        self.refreshes += 1


def make_entity(cloud, local, current="time_of_use"):
    entity = select_mod.OperatingModeSelect.__new__(select_mod.OperatingModeSelect)
    entity.coordinator = FakeCoordinator({"operating_mode": current, "mode_source": "local"})
    entity._client = cloud
    entity._local = local
    entity.async_write_ha_state = lambda: None
    return entity


# -- reads -------------------------------------------------------------------


def _patch_cloud_reads(monkeypatch, *, mode=("self_consumption", 20), export=("no_export", None),
                       mode_error=None, export_error=None):
    seen = {"mode": 0, "export": 0}

    async def read_mode(client):
        seen["mode"] += 1
        if mode_error:
            raise mode_error
        return mode

    async def read_export(client):
        seen["export"] += 1
        if export_error:
            raise export_error
        return export

    monkeypatch.setattr(select_mod, "_read_operating_mode", read_mode)
    monkeypatch.setattr(select_mod, "_read_export_settings", read_export)
    return seen


def test_mode_comes_from_the_gateway_when_local_is_available(monkeypatch):
    seen = _patch_cloud_reads(monkeypatch)
    data = run(select_mod._read_mode_and_export(None, FakeLocal()))
    assert (data["operating_mode"], data["reserve_soc"]) == ("time_of_use", 35)
    assert data["mode_source"] == "local"
    assert data["available_modes"] == ["emergency_backup", "time_of_use"]
    assert seen["mode"] == 0, "the cloud must not be asked for a mode we already have"
    assert data["export_mode"] == "no_export", "export settings still come from the cloud"


def test_cloud_outage_does_not_take_the_mode_select_down(monkeypatch):
    """The whole point: the cloud login failing must not hide a mode we can read."""
    _patch_cloud_reads(monkeypatch, export_error=TimeoutError("502"))
    previous = {"export_mode": "solar_only", "export_limit_kw": 5.0}
    data = run(select_mod._read_mode_and_export(None, FakeLocal(), previous))
    assert data["operating_mode"] == "time_of_use"
    assert (data["export_mode"], data["export_limit_kw"]) == ("solar_only", 5.0), (
        "last known export settings are kept, not blanked"
    )


def test_local_read_failure_falls_back_to_the_cloud(monkeypatch):
    seen = _patch_cloud_reads(monkeypatch)
    local = FakeLocal(read_error=LocalGatewayError("connect", "refused"))
    data = run(select_mod._read_mode_and_export(None, local))
    assert data["operating_mode"] == "self_consumption"
    assert data["mode_source"] == "cloud"
    assert seen["mode"] == 1


def test_unrecognised_active_programme_falls_back_to_the_cloud(monkeypatch):
    seen = _patch_cloud_reads(monkeypatch)
    odd = {"current_id": 1, "list": [{"id": 1, "name": "Custom", "scheduling_type": 9}]}
    data = run(select_mod._read_mode_and_export(None, FakeLocal(modes=odd)))
    assert data["mode_source"] == "cloud" and seen["mode"] == 1


def test_both_paths_down_is_still_an_update_failure(monkeypatch):
    _patch_cloud_reads(monkeypatch, mode_error=TimeoutError("502"))
    local = FakeLocal(read_error=LocalGatewayError("connect", "refused"))
    with pytest.raises(UpdateFailed):
        run(select_mod._read_mode_and_export(None, local))


# -- writes ------------------------------------------------------------------


def test_mode_change_goes_local_and_never_touches_the_cloud():
    cloud, local = FakeCloud(), FakeLocal()
    entity = make_entity(cloud, local)
    run(entity.async_select_option("emergency_backup"))
    assert local.sets == ["emergency_backup"]
    assert cloud.modes_set == [] and cloud.calls == [], "no cloud login, no reserve read"
    assert entity.coordinator.data["operating_mode"] == "emergency_backup"
    assert entity.coordinator.refreshes == 1


def test_local_failure_falls_back_to_the_cloud_with_the_modes_own_reserve():
    cloud = FakeCloud()
    local = FakeLocal(set_error=LocalGatewayError("unconfirmed", "still TOU"))
    run(make_entity(cloud, local).async_select_option("emergency_backup"))
    assert local.sets == ["emergency_backup"]
    assert len(cloud.modes_set) == 1 and cloud.modes_set[0].soc == 100


def test_missing_programme_is_a_clear_error_and_does_not_fall_back():
    """The cloud would send a made-up profile id for a programme that is not there."""
    cloud = FakeCloud()
    local = FakeLocal(set_error=LocalGatewayError("unsupported", "no self_consumption programme"))
    with pytest.raises(HomeAssistantError) as err:
        run(make_entity(cloud, local).async_select_option("self_consumption"))
    assert "no programme" in str(err.value)
    assert cloud.modes_set == []


def test_already_in_that_mode_writes_nothing_on_either_path():
    cloud, local = FakeCloud(), FakeLocal()
    run(make_entity(cloud, local, current="time_of_use").async_select_option("time_of_use"))
    assert local.sets == [] and cloud.modes_set == []


def test_without_a_local_connection_the_cloud_path_is_unchanged():
    cloud = FakeCloud()
    run(make_entity(cloud, None).async_select_option("self_consumption"))
    assert len(cloud.modes_set) == 1 and cloud.modes_set[0].soc == 33


def test_attributes_say_where_the_mode_came_from():
    entity = make_entity(FakeCloud(), FakeLocal())
    entity.coordinator.data.update(mode_source="local", available_modes=["time_of_use"])
    assert entity.extra_state_attributes == {
        "mode_source": "local", "available_modes": ["time_of_use"],
    }


def test_only_modes_the_gateway_has_are_offered():
    """Don's aGate has no Self Consumption programme; offering it is a dead end."""
    entity = make_entity(FakeCloud(), FakeLocal())
    entity.coordinator.data["available_modes"] = ["emergency_backup", "time_of_use"]
    assert entity.options == ["time_of_use", "emergency_backup"]


def test_the_active_mode_is_always_among_the_options():
    entity = make_entity(FakeCloud(), FakeLocal(), current="self_consumption")
    entity.coordinator.data["available_modes"] = ["time_of_use"]
    assert "self_consumption" in entity.options


def test_all_modes_are_offered_when_availability_is_unknown():
    entity = make_entity(FakeCloud(), None)
    assert entity.options == select_mod.OPERATING_MODES


def test_missing_programme_is_reported_as_a_validation_error():
    from homeassistant.exceptions import ServiceValidationError

    local = FakeLocal(set_error=LocalGatewayError("unsupported", "none"))
    with pytest.raises(ServiceValidationError):
        run(make_entity(FakeCloud(), local).async_select_option("self_consumption"))
