"""Circuit 3 sensors (richo/homeassistant-franklinwh#83).

The 353 usage reply has SW1*/SW2* fields and CarSW* fields, nothing named
SW3: FranklinWH reports circuit 3 as the car switch. The sensors expose it
under the circuit's own name so it sits next to switches 1 and 2.
"""

from custom_components.franklin_wh import sensor as sensor_mod
from custom_components.franklin_wh import api
from custom_components.franklin_wh.api import client as client_mod
from conftest import run


class FakeCoordinator:
    def __init__(self, data):
        self.data = data
        self.last_update_success = True

    def async_add_listener(self, *a, **k):
        return lambda: None


def _stats():
    c = client_mod.Client.__new__(client_mod.Client)
    c.gateway = "GW1"

    async def info():
        return {"runtimeData": {
            "p_sun": 1, "p_gen": 0, "genStat": 0, "p_fhp": 2, "p_uti": 3,
            "p_load": 4, "soc": 50, "kwh_fhp_chg": 1, "kwh_fhp_di": 2,
            "kwh_uti_in": 3, "kwh_uti_out": 4, "kwh_sun": 5, "kwh_gen": 0,
            "kwh_load": 6,
        }}

    async def usage():
        # Shape of a real 353 reply; the only circuit-3 fields are CarSW*.
        return {"SW1ExpPower": 10, "SW2ExpPower": 20, "CarSWPower": 30,
                "SW1ExpEnergy": 100, "SW2ExpEnergy": 200,
                "CarSWExpEnergy": 300, "CarSWImpEnergy": 40}

    c.get_composite_info = info
    c._switch_usage = usage
    return run(c.get_stats())


def test_switch_3_sensors_read_the_car_switch_fields():
    coord = FakeCoordinator(_stats())
    load = sensor_mod.Sw3LoadSensor(coord, "FranklinWH", "GW1")
    use = sensor_mod.Sw3UseSensor(coord, "FranklinWH", "GW1")
    assert load.native_value == 30
    assert use.native_value == 300
    assert load.unique_id == "GW1_switch_3_load"
    assert use.unique_id == "GW1_switch_3_lifetime_use"
    # Every FranklinSensor name carries a doubled space (prefix + " " + "_x"
    # titled); left as-is so existing friendly names do not change.
    assert " ".join(load.name.split()) == "FranklinWH Switch 3 Load"


def test_switch_3_sensors_are_created_for_cloud_entries():
    assert "Sw3LoadSensor" in sensor_mod._CLOUD_ONLY_SENSOR_CLASSES
    assert "Sw3UseSensor" in sensor_mod._CLOUD_ONLY_SENSOR_CLASSES
