"""Smart-circuit switches: what we send to the device, and platform wiring."""

import pytest
from conftest import run

from custom_components.franklin_wh import api
from custom_components.franklin_wh import switch as switch_mod


class FakeClient:
    def __init__(self, state=(False, False, False), status=None):
        self._state = api.SwitchState(list(state))
        self._status = status if status is not None else {
            "Sw1Name": "Well Pump", "Sw2Name": "Circuits 2", "Sw3Name": "EV", "SwMerge": 0,
        }
        self.writes = []

    async def get_smart_switch_state(self):
        return self._state

    async def _switch_status(self):
        return self._status

    async def set_smart_switch_state(self, state):
        self.writes.append(tuple(state))


class FakeCoordinator:
    def __init__(self, data):
        self.data = data
        self.last_update_success = True
        self.refreshed = 0

    async def async_refresh(self):
        self.refreshed += 1


def make(client, indices, data=(False, False, False)):
    e = switch_mod.SmartCircuitSwitch.__new__(switch_mod.SmartCircuitSwitch)
    e._client = client
    e._indices = list(indices)
    e.coordinator = FakeCoordinator(api.SwitchState(list(data)))
    return e


def test_turning_on_one_circuit_leaves_the_others_untouched():
    client = FakeClient()
    run(make(client, [1]).async_turn_on())
    assert client.writes == [(None, True, None)], "None means unchanged"


def test_turning_off_sends_false_only_for_that_circuit():
    client = FakeClient()
    run(make(client, [0]).async_turn_off())
    assert client.writes == [(False, None, None)]


def test_merged_pair_is_written_together():
    client = FakeClient()
    run(make(client, [0, 1]).async_turn_on())
    assert client.writes == [(True, True, None)]


def test_state_is_refreshed_after_a_write():
    client = FakeClient()
    e = make(client, [2])
    run(e.async_turn_on())
    assert e.coordinator.refreshed == 1


def test_is_on_reflects_only_our_circuits():
    e = make(FakeClient(), [1], data=(True, False, True))
    assert e.is_on is False
    e = make(FakeClient(), [1], data=(False, True, False))
    assert e.is_on is True


def test_disagreeing_grouped_circuits_report_unknown():
    e = make(FakeClient(), [0, 1], data=(True, False, False))
    assert e.is_on is None


def test_layout_uses_gateway_names():
    layout = run(switch_mod._async_read_circuit_layout(FakeClient()))
    assert layout == [("Well Pump", [0]), ("Circuits 2", [1]), ("EV", [2])]


def test_layout_groups_merged_circuits():
    client = FakeClient(status={"Sw1Name": "A", "Sw2Name": "B", "Sw3Name": "C", "SwMerge": 1})
    layout = run(switch_mod._async_read_circuit_layout(client))
    assert layout == [("A + B", [0, 1]), ("C", [2])]


def test_layout_falls_back_when_names_are_unreadable():
    class Broken(FakeClient):
        async def _switch_status(self):
            raise RuntimeError("offline")

    layout = run(switch_mod._async_read_circuit_layout(Broken()))
    assert [n for n, _ in layout] == ["Smart Circuit 1", "Smart Circuit 2", "Smart Circuit 3"]


def test_unique_ids_are_stable_per_circuit():
    class Coord:
        data = None
        last_update_success = True

    e = switch_mod.SmartCircuitSwitch(Coord(), FakeClient(), "FranklinWH", "SN1", "EV", [2])
    assert e.unique_id == "SN1_smart_circuit_3"
    e = switch_mod.SmartCircuitSwitch(Coord(), FakeClient(), "FranklinWH", "SN1", "A + B", [0, 1])
    assert e.unique_id == "SN1_smart_circuit_1_2"
