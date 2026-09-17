"""Test bootstrap.

The repo root *is* the integration package (hacs.json content_in_root), so it
has to be importable as `franklin_wh` for its relative imports to resolve.
Symlink it under a temp dir and put that on sys.path.

Run with tools/run-tests.sh, which uses the Home Assistant image — it already
provides homeassistant, voluptuous, franklinwh and pytest. There is no
pytest-asyncio there, so coroutines are driven with the `run` helper below
rather than an async plugin.
"""

import asyncio
import os
import pathlib
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_PARENT = pathlib.Path(tempfile.mkdtemp(prefix="franklin_wh_pkg_"))
if not (_PARENT / "franklin_wh").exists():
    os.symlink(ROOT, _PARENT / "franklin_wh")
sys.path.insert(0, str(_PARENT))


def run(coro):
    """Run a coroutine to completion (stands in for pytest-asyncio)."""
    return asyncio.run(coro)


class FakeEntry:
    """Minimal stand-in for ConfigEntry; the setup paths only read .data."""

    def __init__(self, data):
        self.data = data
        self.entry_id = "test-entry"
        self.title = "FranklinWH Test"


@pytest.fixture
def cloud_entry():
    """Exactly what async_step_cloud writes: gateway_id, and no serial/host."""
    return FakeEntry(
        {
            "connection_type": "cloud",
            "username": "user@example.com",
            "password": "secret",
            "gateway_id": "GW123456789",
        }
    )


@pytest.fixture
def modbus_entry():
    return FakeEntry(
        {
            "connection_type": "modbus",
            "host": "10.0.0.5",
            "port": 502,
            "serial": "SN987654321",
            "model": "aGate X",
        }
    )


@pytest.fixture
def both_entry():
    return FakeEntry(
        {
            "connection_type": "both",
            "host": "10.0.0.5",
            "port": 502,
            "username": "user@example.com",
            "password": "secret",
            "gateway_id": "GW123456789",
            "serial": "SN987654321",
        }
    )
