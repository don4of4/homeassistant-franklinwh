"""Grid and battery power are int16 watts, so they wrap above 32.767 kW."""

import pytest

from custom_components.franklin_wh import modbus as modbus_mod

SPAN = 65536


@pytest.fixture(autouse=True)
def _clear_continuity():
    modbus_mod._LAST_RESOLVED.clear()
    yield
    modbus_mod._LAST_RESOLVED.clear()


def resolve(raw_grid, raw_batt, home_kw, solar_kw=0.0, prev=None):
    if prev:
        modbus_mod._LAST_RESOLVED["host"] = prev
    data = {
        "_grid_raw_w": raw_grid,
        "_battery_raw_w": raw_batt,
        "home_load": home_kw,
        "solar_power": solar_kw,
    }
    modbus_mod._resolve_power_wraparound(data, "host")
    return data["grid_power"], data["battery_power"]


def test_ordinary_readings_are_untouched():
    assert resolve(2374, -100, 2.3) == pytest.approx((2.374, -0.1))


def test_grid_wraps_while_battery_is_fine():
    # 35.4 kW import: batteries at 19.5 kW plus a ~15.9 kW house load.
    grid, batt = resolve(35400 - SPAN, -19500, 15.9)
    assert grid == pytest.approx(35.4)
    assert batt == pytest.approx(-19.5)


def test_battery_wraps_while_grid_is_fine():
    grid, batt = resolve(-29000, 34000 - SPAN, 5.0)
    assert grid == pytest.approx(-29.0)
    assert batt == pytest.approx(34.0)


def test_both_wrapped_resolved_by_continuity():
    """The energy balance alone is degenerate here.

    Its residual depends only on the sum of the two offsets, so shifting grid up
    a span and battery down a span is indistinguishable. The previous reading
    breaks the tie: real power cannot move 65.5 kW in 30 seconds.
    """
    grid, batt = resolve(40000 - SPAN, -34000 + SPAN, 6.0, prev=(39000.0, -33000.0))
    assert grid == pytest.approx(40.0)
    assert batt == pytest.approx(-34.0)


def test_genuine_export_near_the_rail_is_not_corrected():
    grid, batt = resolve(-31000, 31000, 0.0)
    assert grid == pytest.approx(-31.0)
    assert batt == pytest.approx(31.0)


def test_passthrough_when_the_balance_is_unavailable():
    grid, batt = resolve(-30084, -19500, None)
    assert grid == pytest.approx(-30.084)
    assert batt == pytest.approx(-19.5)
