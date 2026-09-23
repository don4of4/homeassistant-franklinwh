"""get_shared_client: one client per gateway, one TokenFetcher per account."""

from conftest import run

from custom_components.franklin_wh import DOMAIN, get_shared_client
from custom_components.franklin_wh import api as franklinwh
from custom_components.franklin_wh import sensor as sensor_mod


class FakeHass:
    def __init__(self):
        self.data = {}

    async def async_add_executor_job(self, func, *args):
        return func(*args)


class FakeClient:
    def __init__(self, fetcher, gateway):
        self.fetcher = fetcher
        self.gateway = gateway


def test_two_gateways_one_account_share_a_fetcher(monkeypatch):
    monkeypatch.setattr(sensor_mod, "supports_http2", lambda: False)
    monkeypatch.setattr(franklinwh, "Client", FakeClient)
    hass = FakeHass()
    a = run(get_shared_client(hass, "user@example.com", "secret", "GW-A"))
    b = run(get_shared_client(hass, "user@example.com", "secret", "GW-B"))
    assert a is not b, "each gateway needs its own client"
    assert a.fetcher is b.fetcher, "but the account login must be shared"
    assert isinstance(a.fetcher, franklinwh.TokenFetcher)


def test_same_gateway_returns_the_same_client(monkeypatch):
    monkeypatch.setattr(sensor_mod, "supports_http2", lambda: False)
    monkeypatch.setattr(franklinwh, "Client", FakeClient)
    hass = FakeHass()
    a = run(get_shared_client(hass, "user@example.com", "secret", "GW-A"))
    assert run(get_shared_client(hass, "user@example.com", "secret", "GW-A")) is a


def test_different_accounts_do_not_share_a_fetcher(monkeypatch):
    monkeypatch.setattr(sensor_mod, "supports_http2", lambda: False)
    monkeypatch.setattr(franklinwh, "Client", FakeClient)
    hass = FakeHass()
    a = run(get_shared_client(hass, "one@example.com", "s", "GW-A"))
    b = run(get_shared_client(hass, "two@example.com", "s", "GW-B"))
    assert a.fetcher is not b.fetcher
    assert set(k for k in hass.data[DOMAIN] if k.startswith("fetcher_")) == {
        "fetcher_one@example.com", "fetcher_two@example.com"
    }
