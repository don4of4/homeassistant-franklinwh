"""Config flow for FranklinWH integration."""

from __future__ import annotations

import asyncio
import logging
import struct
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 502
MODBUS_TIMEOUT = 5.0


async def _test_modbus(host: str, port: int) -> dict[str, str] | None:
    """Test Modbus TCP connection and read device info.

    Returns dict with gateway serial and model, or None on failure.
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=MODBUS_TIMEOUT
        )
    except (OSError, asyncio.TimeoutError):
        return None

    try:
        # Read SunSpec 'SunS' marker at addr 0
        request = struct.pack(">HHHBBHH", 1, 0, 6, 1, 3, 0, 2)
        writer.write(request)
        await writer.drain()
        response = await asyncio.wait_for(reader.read(256), timeout=MODBUS_TIMEOUT)
        if len(response) < 13 or response[7] != 3:
            return None
        marker = response[9:13]
        if marker != b"SunS":
            return None

        writer.close()
        await writer.wait_closed()

        # Read Common Model (Model 1) for device identity
        reader2, writer2 = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=MODBUS_TIMEOUT
        )
        # Model 1 starts at addr 4, read 66 registers (full common model)
        request = struct.pack(">HHHBBHH", 2, 0, 6, 1, 3, 4, 66)
        writer2.write(request)
        await writer2.drain()
        response = await asyncio.wait_for(reader2.read(2048), timeout=MODBUS_TIMEOUT)
        writer2.close()
        await writer2.wait_closed()

        if len(response) < 9 + 132 or response[7] != 3:
            return None

        raw = response[9:]

        def read_str(offset: int, length: int) -> str:
            return (
                raw[offset * 2 : (offset + length) * 2]
                .decode("ascii", errors="replace")
                .strip()
                .strip("\x00")
            )

        manufacturer = read_str(2, 16)  # offset 2 in model data (after ID+len)
        model = read_str(18, 16)
        version = read_str(42, 8)
        serial = read_str(50, 16)

        return {
            "manufacturer": manufacturer,
            "model": model,
            "version": version,
            "serial": serial,
        }
    except (OSError, asyncio.TimeoutError):
        return None


async def _test_cloud(username: str, password: str, gateway: str) -> bool:
    """Test cloud API credentials."""
    try:
        import franklinwh  # noqa: PLC0415

        fetcher = franklinwh.TokenFetcher(username, password)
        client = franklinwh.Client(fetcher, gateway)
        await client.get_stats()
        return True
    except Exception:  # noqa: BLE001
        return False


class FranklinWHConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for FranklinWH."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._modbus_info: dict[str, str] | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """First step — choose connection type."""
        if user_input is not None:
            if user_input["connection_type"] == "modbus":
                return await self.async_step_modbus()
            return await self.async_step_cloud()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("connection_type", default="modbus"): vol.In(
                        {
                            "modbus": "Local (Modbus TCP) — recommended",
                            "cloud": "Cloud API — for mode switching",
                        }
                    ),
                }
            ),
            description_placeholders={
                "info": "Local Modbus reads data directly from your aGate "
                "(fast, no cloud dependency). Cloud API is needed only "
                "for mode switching."
            },
        )

    async def async_step_modbus(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Modbus setup — just the IP address."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input.get(CONF_PORT, DEFAULT_PORT)

            info = await _test_modbus(host, port)
            if info is None:
                errors["base"] = "cannot_connect"
            else:
                self._modbus_info = info
                serial = info["serial"]

                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"FranklinWH {info['model']} ({serial[-4:]})",
                    data={
                        "connection_type": "modbus",
                        CONF_HOST: host,
                        CONF_PORT: port,
                        "serial": serial,
                        "model": info["model"],
                        "firmware": info["version"],
                    },
                )

        return self.async_show_form(
            step_id="modbus",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
                }
            ),
            errors=errors,
            description_placeholders={
                "info": "Enter the IP address of your FranklinWH aGate. "
                "Modbus TCP must be enabled (Franklin app → SPAN toggle)."
            },
        )

    async def async_step_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Cloud API setup — credentials + gateway ID."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            gateway = user_input["gateway_id"]

            valid = await _test_cloud(username, password, gateway)
            if not valid:
                errors["base"] = "invalid_auth"
            else:
                await self.async_set_unique_id(gateway)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"FranklinWH Cloud ({gateway[-4:]})",
                    data={
                        "connection_type": "cloud",
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                        "gateway_id": gateway,
                    },
                )

        return self.async_show_form(
            step_id="cloud",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required("gateway_id"): str,
                }
            ),
            errors=errors,
        )
