"""The vendored cloud client: the patches we carry on top of upstream main."""

import pathlib

import pytest
from conftest import run

from custom_components.franklin_wh import api
from custom_components.franklin_wh.api import client as client_mod


def bare_client(gateway="GW1"):
    c = client_mod.Client.__new__(client_mod.Client)
    c.gateway = gateway
    c.url_base = api.DEFAULT_URL_BASE
    return c


def test_get_stats_raises_invalid_data_not_typeerror_on_null_result(monkeypatch):
    """Upstream indexes info["runtimeData"] before checking info (#82)."""
    c = bare_client()

    async def none():
        return None

    async def sw():
        return {}

    monkeypatch.setattr(c, "get_composite_info", none)
    monkeypatch.setattr(c, "_switch_usage", sw)
    with pytest.raises(api.InvalidDataException):
        run(c.get_stats())


def test_set_mode_carries_storm_hedge_and_profile_through(monkeypatch):
    """Storm Hedge, the per-account profile id and oldIndex come from the gateway."""
    c = bare_client()
    sent = {}

    async def tou():
        return {"active_id": 501, "profiles": {2: {"id": 502, "oldIndex": 2, "soc": 33.0}}, "stromEn": 0}

    async def post_form(url, payload):
        sent.update(payload)

    monkeypatch.setattr(c, "get_tou_settings", tou)
    monkeypatch.setattr(c, "_post_form", post_form)
    run(c.set_mode(api.Mode.self_consumption(soc=33)))
    assert sent["stromEn"] == "0", "must not flip Storm Hedge on"
    assert sent["currendId"] == "502", "per-account profile id, not the 9323 constant"
    assert sent["oldIndex"] == "2"
    assert sent["soc"] == "33"


def test_get_mode_resolves_from_the_tou_list(monkeypatch):
    c = bare_client()

    async def tou():
        return {"active_id": 7, "profiles": {1: {"id": 6, "soc": 15.0}, 2: {"id": 7, "soc": 33.0}}, "stromEn": 1}

    monkeypatch.setattr(c, "get_tou_settings", tou)
    assert run(c.get_mode()) == ("self_consumption", 33.0)


def test_set_mode_reserve_validates_before_any_request():
    c = bare_client()
    with pytest.raises(ValueError):
        run(c.set_mode_reserve(9, 50))
    with pytest.raises(ValueError):
        run(c.set_mode_reserve(2, 101))


def test_installer_manage_namespace_is_gone():
    """Probing /manage/ got an account disassociated from its device once."""
    src = pathlib.Path(client_mod.__file__).read_text()
    assert "/manage/" not in src
