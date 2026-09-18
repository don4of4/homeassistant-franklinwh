"""Mode detection must degrade instead of raising on unknown firmware IDs."""

import pytest
from conftest import run

from custom_components.franklin_wh import select as select_mod


class FakeClient:
    def __init__(self, status=None, status_error=None, switch=None):
        self._status_data = status
        self._status_error = status_error
        self._switch_data = switch or {}

    async def _status(self):
        if self._status_error:
            raise self._status_error
        return self._status_data

    async def _switch_status(self):
        return self._switch_data


@pytest.fixture(autouse=True)
def _clear_warned():
    select_mod._WARNED_RUNNING_MODES.clear()
    yield
    select_mod._WARNED_RUNNING_MODES.clear()


def test_tou_list_is_authoritative_when_available():
    class TouClient(FakeClient):
        async def get_mode(self):
            return "time_of_use", 25.0

    client = TouClient(status={"name": "Self Consumption"}, switch={"selfMinSoc": 20})
    assert run(select_mod._read_operating_mode(client)) == ("time_of_use", 25)


def test_name_from_status_is_preferred():
    client = FakeClient(
        status={"name": "Self Consumption"}, switch={"selfMinSoc": 20}
    )
    assert run(select_mod._read_operating_mode(client)) == ("self_consumption", 20)


def test_falls_back_to_numeric_running_mode():
    client = FakeClient(
        status_error=RuntimeError("no status"),
        switch={"runingMode": 7169, "touMinSoc": 15},
    )
    assert run(select_mod._read_operating_mode(client)) == ("time_of_use", 15)


def test_unknown_running_mode_returns_none_without_raising():
    """#82 reported runingMode 75616 — a third ID set we have no mapping for."""
    client = FakeClient(
        status_error=RuntimeError("no status"),
        switch={"runingMode": 75616, "touMinSoc": 10},
    )
    assert run(select_mod._read_operating_mode(client)) == (None, None)


def test_unknown_running_mode_warns_only_once(caplog):
    client = FakeClient(
        status_error=RuntimeError("no status"), switch={"runingMode": 75616}
    )
    with caplog.at_level("WARNING"):
        for _ in range(5):
            run(select_mod._read_operating_mode(client))
    warnings = [r for r in caplog.records if "unrecognised runingMode" in r.message]
    assert len(warnings) == 1, "it logged on every poll before this"
