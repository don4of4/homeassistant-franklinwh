"""Switch platform for FranklinWH smart circuits."""

from __future__ import annotations

from datetime import timedelta
import logging

import voluptuous as vol

from homeassistant.components.switch import (
    PLATFORM_SCHEMA as SWITCH_PLATFORM_SCHEMA,
    SwitchEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ID,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_SWITCHES,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from . import api as franklinwh
from . import describe_exception

_LOGGER = logging.getLogger(__name__)

# Cloud polling for switch state. Matches select/number: the API is rate
# limited, and a toggle refreshes immediately anyway.
DEFAULT_UPDATE_INTERVAL = 300
CIRCUIT_COUNT = 3

PLATFORM_SCHEMA = SWITCH_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Required(CONF_ID): cv.string,
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_SWITCHES): cv.ensure_list(vol.In([1, 2, 3])),
        vol.Optional("use_sn", default=False): cv.boolean,
        vol.Optional("prefix", default=False): cv.string,
        vol.Optional(
            "update_interval", default=DEFAULT_UPDATE_INTERVAL
        ): cv.time_period,
    }
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up one grouped switch from YAML (legacy)."""
    # TODO(richo) why does it string the default value
    unique_id = config[CONF_ID] if config["use_sn"] and config["use_sn"] != "False" else None
    prefix = config["prefix"] if config["prefix"] and config["prefix"] != "False" else "FranklinWH"
    indices = [n - 1 for n in config[CONF_SWITCHES]]

    client, coordinator = await _async_build(
        hass, config[CONF_USERNAME], config[CONF_PASSWORD], config[CONF_ID],
        config["update_interval"],
    )
    async_add_entities(
        [SmartCircuitSwitch(coordinator, client, prefix, unique_id, config[CONF_NAME], indices)]
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one switch per smart circuit from a config entry.

    __init__.py forwards SWITCH only for connection_type "cloud" and "both".
    Circuits are named from the gateway (Sw1Name..Sw3Name). When the gateway
    reports circuits 1 and 2 as merged they act as one, so they get one entity;
    the client refuses to set them to different values.
    """
    client, coordinator = await _async_build(
        hass,
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data.get("gateway_id") or entry.data.get("serial", ""),
        timedelta(seconds=DEFAULT_UPDATE_INTERVAL),
    )
    unique_id = entry.data.get("serial") or entry.data.get("gateway_id") or None
    layout = await _async_read_circuit_layout(client)
    async_add_entities(
        [
            SmartCircuitSwitch(coordinator, client, "FranklinWH", unique_id, name, indices)
            for name, indices in layout
        ]
    )


async def _async_read_circuit_layout(client) -> list[tuple[str, list[int]]]:
    """Return (name, indices) per entity, using the gateway's circuit names."""
    try:
        status = await client._switch_status()
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Could not read circuit names (%s); using defaults", describe_exception(err))
        status = {}
    names = [
        status.get(f"Sw{n}Name") or f"Smart Circuit {n}" for n in range(1, CIRCUIT_COUNT + 1)
    ]
    if status.get("SwMerge") == 1:
        return [(f"{names[0]} + {names[1]}", [0, 1]), (names[2], [2])]
    return [(name, [i]) for i, name in enumerate(names)]


async def _async_build(hass, username, password, gateway, update_interval):
    """Shared client + coordinator for both setup paths."""
    from . import get_shared_client  # noqa: PLC0415

    client = await get_shared_client(hass, username, password, gateway)

    async def _update_data() -> franklinwh.SwitchState:
        try:
            return await client.get_smart_switch_state()
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(
                f"Error fetching FranklinWH switch state: {describe_exception(err)}"
            ) from err

    coordinator = DataUpdateCoordinator[franklinwh.SwitchState](
        hass,
        _LOGGER,
        name="franklinwh_switches",
        update_method=_update_data,
        update_interval=update_interval,
        always_update=False,
    )
    await coordinator.async_refresh()
    return client, coordinator


class SmartCircuitSwitch(
    CoordinatorEntity[DataUpdateCoordinator[franklinwh.SwitchState]], SwitchEntity
):
    """One or more smart circuits toggled together."""

    def __init__(self, coordinator, client, prefix, unique_id, name, indices) -> None:
        """Initializer."""
        super().__init__(coordinator)
        self._client = client
        self._indices = list(indices)
        self._attr_name = f"{prefix} {name}"
        if unique_id:
            self._attr_has_entity_name = True
            slug = "_".join(str(i + 1) for i in self._indices)
            self._attr_unique_id = f"{unique_id}_smart_circuit_{slug}"

    @property
    def available(self) -> bool:
        """Entity is available when the coordinator has data."""
        return self.coordinator.last_update_success and self.coordinator.data is not None

    @property
    def is_on(self) -> bool | None:
        """On when every grouped circuit is on; None when they disagree."""
        state = self.coordinator.data
        if state is None:
            return None
        values = [state[i] for i in self._indices]
        if all(values):
            return True
        if all(v is False for v in values):
            return False
        return None

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the circuit(s) on."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the circuit(s) off."""
        await self._async_set(False)

    async def _async_set(self, value: bool) -> None:
        # None means "leave unchanged", so only our circuits are touched.
        desired: list[bool | None] = [None] * CIRCUIT_COUNT
        for i in self._indices:
            desired[i] = value
        await self._client.set_smart_switch_state(franklinwh.SwitchState(desired))
        await self.coordinator.async_refresh()
