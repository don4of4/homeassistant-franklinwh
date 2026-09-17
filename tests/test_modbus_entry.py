"""A modbus/both entry without a host must fail with a readable error."""

import pytest
from conftest import FakeEntry, run

from homeassistant.exceptions import ConfigEntryError

from franklin_wh import modbus as modbus_mod


def test_missing_host_raises_config_entry_error():
    entry = FakeEntry({"connection_type": "both", "username": "u", "password": "p"})
    with pytest.raises(ConfigEntryError) as err:
        run(modbus_mod.async_setup_entry(None, entry, None))
    # It used to be a bare KeyError: 'host', which said nothing useful.
    assert "host" in str(err.value).lower()
