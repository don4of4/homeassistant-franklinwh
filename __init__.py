"""FranklinWH integration — shared client, coordinators, and helpers."""

from __future__ import annotations

import logging
from typing import Any

import franklinwh

from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

DOMAIN = "franklin_wh"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the FranklinWH integration."""
    hass.data.setdefault(DOMAIN, {})
    return True


def get_shared_client(
    hass: HomeAssistant,
    username: str,
    password: str,
    gateway: str,
) -> franklinwh.Client:
    """Return a shared FranklinWH Client for the given gateway.

    All platforms (sensor, switch, select, number) should call this instead
    of creating their own Client.  The Client and its TokenFetcher are
    cached in ``hass.data`` keyed by gateway serial so that a single auth
    session is reused across platforms.
    """
    hass.data.setdefault(DOMAIN, {})
    key = f"client_{gateway}"

    if key not in hass.data[DOMAIN]:
        _LOGGER.debug("Creating shared FranklinWH client for gateway %s", gateway)
        fetcher = franklinwh.TokenFetcher(username, password)
        client = franklinwh.Client(fetcher, gateway)
        hass.data[DOMAIN][key] = client

    return hass.data[DOMAIN][key]


def get_shared_coordinator(
    hass: HomeAssistant,
    gateway: str,
    name: str,
) -> DataUpdateCoordinator[dict[str, Any]] | None:
    """Retrieve a shared coordinator stored by another platform.

    Returns None if the coordinator hasn't been created yet.
    """
    return hass.data.get(DOMAIN, {}).get(f"coordinator_{gateway}_{name}")


def set_shared_coordinator(
    hass: HomeAssistant,
    gateway: str,
    name: str,
    coordinator: DataUpdateCoordinator[dict[str, Any]],
) -> None:
    """Store a coordinator for sharing across platforms."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][f"coordinator_{gateway}_{name}"] = coordinator
