"""Retries are routine; only the final outcome warrants a warning."""

import logging

from conftest import run

from custom_components.franklin_wh import sensor as sensor_mod


class Flaky:
    def __init__(self, fail_times, exc=None):
        self.fail_times = fail_times
        self.calls = 0
        self.exc = exc or TimeoutError()  # empty message, like httpx.ReadTimeout

    async def get_stats(self):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        return "ok"


def warnings(caplog):
    return [r.message for r in caplog.records if r.levelno >= logging.WARNING]


def test_recovery_after_a_retry_logs_no_warning(caplog):
    with caplog.at_level(logging.DEBUG):
        out = run(sensor_mod._fetch_stats(Flaky(2), sensor_mod.StaleDataCache(),
                                          tolerate_stale_data=False, retries=3, delay=0))
    assert out == "ok"
    assert warnings(caplog) == []


def test_total_failure_warns_once_and_names_the_error(caplog):
    cache = sensor_mod.StaleDataCache(); cache.store("stale")
    with caplog.at_level(logging.DEBUG):
        out = run(sensor_mod._fetch_stats(Flaky(9), cache, tolerate_stale_data=True, retries=3, delay=0))
    assert out == "stale"
    w = warnings(caplog)
    assert len(w) == 1
    assert "TimeoutError" in w[0], "an empty-message exception must still be identifiable"
    assert not w[0].rstrip().endswith(":")
