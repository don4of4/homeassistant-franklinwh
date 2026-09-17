"""_fetch_stats must treat a broken library reply as transient.

#82 follow-up: franklinwh's get_stats() does `data = info["runtimeData"]` and
only afterwards checks `if data is None`, so an API reply of `result: null`
raises TypeError one line before its own guard.
"""

import pytest
from conftest import run

from homeassistant.helpers.update_coordinator import UpdateFailed

from franklin_wh import sensor as sensor_mod


class FakeClient:
    def __init__(self, *, raises=None, returns=None):
        self._raises = raises
        self._returns = returns
        self.calls = 0

    async def get_stats(self):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._returns


def _fetch(client, cache, tolerate=False):
    return run(
        sensor_mod._fetch_stats(
            client, cache, tolerate_stale_data=tolerate, retries=3, delay=0
        )
    )


def test_library_typeerror_becomes_update_failed():
    client = FakeClient(raises=TypeError("'NoneType' object is not subscriptable"))
    cache = sensor_mod.StaleDataCache()
    with pytest.raises(UpdateFailed):
        _fetch(client, cache)
    assert client.calls == 3, "a transient failure should use the retry budget"


def test_stale_cache_is_served_when_tolerated():
    cache = sensor_mod.StaleDataCache()
    cache.store("last-good")
    client = FakeClient(raises=TypeError("boom"))
    assert _fetch(client, cache, tolerate=True) == "last-good"


def test_success_populates_cache_without_retrying():
    client = FakeClient(returns="fresh")
    cache = sensor_mod.StaleDataCache()
    assert _fetch(client, cache) == "fresh"
    assert client.calls == 1
    assert cache.is_populated()


def test_recovers_on_a_later_attempt():
    class Flaky(FakeClient):
        async def get_stats(self):
            self.calls += 1
            if self.calls < 3:
                raise TypeError("transient")
            return "recovered"

    client = Flaky()
    assert _fetch(client, sensor_mod.StaleDataCache()) == "recovered"
