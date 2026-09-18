"""Coordinator errors must name the exception type.

#82 surfaced "Error fetching FranklinWH mode/export status:" with nothing after
the colon, because franklinwh raises bare exceptions whose str() is empty.

This exercises the real _read_mode_and_export. An earlier version of this test
re-implemented the error handling inline, so it passed against the broken code
and proved nothing.
"""

import pytest
from conftest import run

from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.franklin_wh import select as select_mod


class Bare(Exception):
    """No message, like franklinwh's InvalidDataException()."""


class FakeClient:
    def __init__(self, exc):
        self._exc = exc

    async def _status(self):
        raise self._exc

    async def _switch_status(self):
        raise self._exc

    async def get_export_settings(self):
        raise self._exc


def test_empty_exception_still_names_its_type():
    with pytest.raises(UpdateFailed) as err:
        run(select_mod._read_mode_and_export(FakeClient(Bare())))
    msg = str(err.value)
    assert "Bare" in msg, "the type is the only clue when str() is empty"
    assert msg.endswith("Bare"), f"dangling colon or empty detail: {msg!r}"


def test_exception_with_a_message_keeps_it():
    class Detailed(Exception):
        pass

    with pytest.raises(UpdateFailed) as err:
        run(select_mod._read_mode_and_export(FakeClient(Detailed("gateway offline"))))
    assert str(err.value).endswith("Detailed: gateway offline")


def test_successful_read_returns_all_four_fields(monkeypatch):
    async def mode(client):
        return "self_consumption", 33

    async def export(client):
        return "solar_only", 5.0

    monkeypatch.setattr(select_mod, "_read_operating_mode", mode)
    monkeypatch.setattr(select_mod, "_read_export_settings", export)
    assert run(select_mod._read_mode_and_export(None)) == {
        "operating_mode": "self_consumption",
        "reserve_soc": 33,
        "export_mode": "solar_only",
        "export_limit_kw": 5.0,
    }
