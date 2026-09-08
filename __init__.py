"""FranklinWH integration — shared client, coordinators, and helpers."""

from __future__ import annotations

import logging
import time
from typing import Any

import franklinwh

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

DOMAIN = "franklin_wh"

PLATFORMS_MODBUS = [Platform.SENSOR]
PLATFORMS_CLOUD = [Platform.SENSOR, Platform.SELECT, Platform.NUMBER]

# Rate limit circuit breaker
_RATE_LIMIT_KEY = "rate_limit_until"
_RATE_LIMIT_BACKOFF_KEY = "rate_limit_backoff"
_INITIAL_BACKOFF_SECONDS = 120
_MAX_BACKOFF_SECONDS = 1800


def check_rate_limit(hass: HomeAssistant) -> None:
    """Raise UpdateFailed if we're in a rate-limit backoff period."""
    data = hass.data.get(DOMAIN, {})
    until = data.get(_RATE_LIMIT_KEY, 0)
    if time.time() < until:
        remaining = int(until - time.time())
        raise UpdateFailed(
            f"FranklinWH API rate limited — backing off for {remaining}s"
        )


def handle_rate_limit(hass: HomeAssistant) -> None:
    """Activate the rate-limit circuit breaker with exponential backoff."""
    hass.data.setdefault(DOMAIN, {})
    current_backoff = hass.data[DOMAIN].get(
        _RATE_LIMIT_BACKOFF_KEY, _INITIAL_BACKOFF_SECONDS
    )
    hass.data[DOMAIN][_RATE_LIMIT_KEY] = time.time() + current_backoff
    hass.data[DOMAIN][_RATE_LIMIT_BACKOFF_KEY] = min(
        current_backoff * 2, _MAX_BACKOFF_SECONDS
    )
    _LOGGER.warning(
        "FranklinWH API rate limited — pausing calls for %ss", current_backoff
    )


def clear_rate_limit(hass: HomeAssistant) -> None:
    """Reset the backoff after a successful API call."""
    data = hass.data.get(DOMAIN, {})
    if _RATE_LIMIT_KEY in data:
        _LOGGER.info("FranklinWH API recovered from rate limit")
        data.pop(_RATE_LIMIT_KEY, None)
        data[_RATE_LIMIT_BACKOFF_KEY] = _INITIAL_BACKOFF_SECONDS


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the FranklinWH integration (legacy YAML support)."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up FranklinWH from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    conn_type = entry.data.get("connection_type", "cloud")

    if conn_type == "modbus":
        await hass.config_entries.async_forward_entry_setups(
            entry, PLATFORMS_MODBUS
        )
    elif conn_type == "both":
        await hass.config_entries.async_forward_entry_setups(
            entry, [Platform.SENSOR, Platform.SELECT, Platform.NUMBER]
        )
    else:
        await hass.config_entries.async_forward_entry_setups(
            entry, PLATFORMS_CLOUD
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    conn_type = entry.data.get("connection_type", "cloud")
    if conn_type == "modbus":
        platforms = PLATFORMS_MODBUS
    elif conn_type == "both":
        platforms = [Platform.SENSOR, Platform.SELECT, Platform.NUMBER]
    else:
        platforms = PLATFORMS_CLOUD
    return await hass.config_entries.async_unload_platforms(entry, platforms)


# ── Shared helpers (used by YAML platforms and config entry platforms) ──


async def get_shared_client(
    hass: HomeAssistant,
    username: str,
    password: str,
    gateway: str,
) -> franklinwh.Client:
    """Return a shared FranklinWH Client for the given gateway.

    One client per gateway, reused by every cloud platform (sensor, select,
    number). Each client owns a TokenFetcher, and each TokenFetcher logs in
    independently - so a platform that builds its own client costs an extra
    login on every restart. This account locks after 3 bad attempts and has
    been disassociated once already, so logins are treated as a scarce
    resource.
    """
    hass.data.setdefault(DOMAIN, {})
    key = f"client_{gateway}"

    if key not in hass.data[DOMAIN]:
        _LOGGER.debug("Creating shared FranklinWH client for gateway %s", gateway)
        from .sensor import supports_http2  # noqa: PLC0415

        fetcher = franklinwh.TokenFetcher(username, password)
        if supports_http2():
            # pylint: disable=no-name-in-module,import-outside-toplevel
            from homeassistant.helpers.httpx_client import (  # noqa: PLC0415
                SSL_ALPN_HTTP11_HTTP2,  # type: ignore  # noqa: PGH003
                create_async_httpx_client,
            )
            # pylint: enable=no-name-in-module,import-outside-toplevel

            def get_client() -> Any:
                return create_async_httpx_client(
                    hass, alpn_protocols=SSL_ALPN_HTTP11_HTTP2
                )

            franklinwh.HttpClientFactory.set_client_factory(get_client)
            client = franklinwh.Client(fetcher, gateway)
        else:
            client = await hass.async_add_executor_job(
                franklinwh.Client, fetcher, gateway
            )
        hass.data[DOMAIN][key] = client

    return hass.data[DOMAIN][key]


def get_shared_coordinator(
    hass: HomeAssistant,
    gateway: str,
    name: str,
) -> DataUpdateCoordinator[dict[str, Any]] | None:
    """Retrieve a shared coordinator stored by another platform."""
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
