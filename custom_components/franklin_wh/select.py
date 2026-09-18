"""Select platform for FranklinWH operating mode and grid export control."""

from __future__ import annotations

from datetime import timedelta
import logging

from . import api as franklinwh
import voluptuous as vol

from homeassistant.components.select import (
    PLATFORM_SCHEMA as SELECT_PLATFORM_SCHEMA,
    SelectEntity,
)
from homeassistant.const import (
    CONF_ID,
    CONF_PASSWORD,
    CONF_USERNAME,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import describe_exception
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

_LOGGER = logging.getLogger(__name__)

# Poll mode/export status every 5 minutes — they rarely change
DEFAULT_UPDATE_INTERVAL = 300

_EXPORT_MODE_MAP = {
    "solar_only": franklinwh.ExportMode.SOLAR_ONLY,
    "solar_and_apower": franklinwh.ExportMode.SOLAR_AND_APOWER,
    "no_export": franklinwh.ExportMode.NO_EXPORT,
}

# Correct runingMode values for current firmware (the library's built-in
# MODE_MAP uses different keys and may not match all firmware versions)
_RUNNING_MODE_MAP = {
    7167: "self_consumption",
    7168: "emergency_backup",
    7169: "time_of_use",
}

# Per-mode reserve-SOC fields. The device reports all three regardless of which
# mode is active, so a mode change can read the target mode's own reserve back
# out and preserve it.
_MODE_FACTORIES = {
    "self_consumption": franklinwh.Mode.self_consumption,
    "time_of_use": franklinwh.Mode.time_of_use,
    "emergency_backup": franklinwh.Mode.emergency_backup,
}

# Operating-mode name -> workMode integer (1=TOU, 2=self consumption, 3=backup).
_WORK_MODE_BY_NAME = {name: wm for wm, name in franklinwh.WORK_MODE_TO_NAME.items()}

_MODE_SOC_KEYS = {
    "self_consumption": "selfMinSoc",
    "time_of_use": "touMinSoc",
    "emergency_backup": "backupMaxSoc",
}

# Unknown runingMode values already reported, so the warning fires once each.
_WARNED_RUNNING_MODES: set = set()

OPERATING_MODES = ["self_consumption", "time_of_use", "emergency_backup"]
EXPORT_MODES = ["solar_only", "solar_and_apower", "no_export"]

PLATFORM_SCHEMA = SELECT_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Required(CONF_ID): cv.string,
        vol.Optional("use_sn", default=False): cv.boolean,
        vol.Optional("prefix", default=False): cv.string,
        vol.Optional(
            "update_interval", default=DEFAULT_UPDATE_INTERVAL
        ): cv.time_period,
    }
)


async def _read_operating_mode(client) -> tuple[str | None, int | None]:
    """Read the current operating mode and reserve SOC from the device.

    Tries the high-level _status() endpoint (command 203) first, reading
    the human-readable 'name' field which is firmware-version independent.
    Falls back to _switch_status() (command 311) with numeric runingMode
    lookup if the name field is absent or unrecognised.

    Returns (mode_name, reserve_soc).
    """
    soc_key_map = _MODE_SOC_KEYS

    # Authoritative: the TOU profile list (getGatewayTouListV2). The active
    # profile's workMode is stable across firmware, unlike runingMode below.
    try:
        mode, reserve = await client.get_mode()
        return mode, (int(reserve) if reserve is not None else None)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("get_mode via tou list failed (%s) — trying _status", err)

    # Primary: human-readable name from high-level status
    try:
        status = await client._status()
        name = (status.get("name") or "").lower()
        if "self" in name and "consumption" in name:
            mode = "self_consumption"
        elif "emergency" in name or "backup" in name:
            mode = "emergency_backup"
        elif "tou" in name or "time" in name:
            mode = "time_of_use"
        else:
            mode = None

        if mode:
            sw = await client._switch_status()
            reserve = sw.get(soc_key_map[mode])
            return mode, reserve
        if name:
            _LOGGER.debug(
                "get_mode: _status name %r unrecognised — trying _switch_status",
                status.get("name"),
            )
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("get_mode: _status failed (%s) — trying _switch_status", err)

    # Fallback: numeric runingMode
    sw = await client._switch_status()
    running_mode = sw.get("runingMode")
    mode = _RUNNING_MODE_MAP.get(running_mode)
    if mode is None and running_mode not in _WARNED_RUNNING_MODES:
        # Firmware versions disagree on these IDs: this integration sees
        # 7167/7168/7169, the library ships 9322/9323/9324, and #82 reported
        # 75616 — a third set. The device populates ALL THREE reserve fields
        # whatever the mode, so they do not identify it (an earlier version of
        # this comment claimed they did). They are logged because they are the
        # values a mode change must preserve. Warn once per unknown value.
        _WARNED_RUNNING_MODES.add(running_mode)
        _LOGGER.warning(
            "get_mode: unrecognised runingMode %r. Known: %s. Reserve values "
            "seen: %s. Please report these at "
            "https://github.com/don4of4/homeassistant-franklinwh/issues",
            running_mode,
            sorted(_RUNNING_MODE_MAP),
            {k: sw.get(k) for k in soc_key_map.values() if k in sw},
        )
    reserve = sw.get(soc_key_map[mode]) if mode else None
    return mode, reserve


async def _read_reserve_for_mode(client, mode: str) -> int | None:
    """Read the reserve SOC the device already holds for `mode`.

    Mode changes must send a reserve SOC, and franklinwh defaults it to 20 (100
    for emergency backup) when the caller omits it. Passing the *current* mode's
    reserve is wrong too, since each mode keeps its own. Reading the target
    mode's own value back out preserves it, and works even when runingMode is
    unrecognised — the reserve fields are readable regardless.
    """
    try:
        settings = await client.get_tou_settings()
        profile = settings["profiles"].get(_WORK_MODE_BY_NAME[mode]) or {}
        if profile.get("soc") is not None:
            return int(profile["soc"])
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("get_tou_settings failed (%s) — trying _switch_status", err)

    key = _MODE_SOC_KEYS[mode]
    sw = await client._switch_status()
    value = sw.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        _LOGGER.warning("Reserve SOC %s has a non-numeric value %r", key, value)
        return None


async def _read_export_settings(client) -> tuple[str, float | None]:
    """Read the current grid export mode and limit from the API.

    Returns (export_mode, limit_kw) where limit_kw is None for unlimited.
    """
    settings = await client.get_export_settings()
    mode = settings.mode.name.lower()
    return mode, settings.limit_kw


async def _write_export_settings(
    client, mode: str, limit_kw: float | None
) -> None:
    """Write the grid export mode and optional power limit to the API."""
    await client.set_export_settings(_EXPORT_MODE_MAP[mode], limit_kw)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the select platform."""
    username: str = config[CONF_USERNAME]
    password: str = config[CONF_PASSWORD]
    gateway: str = config[CONF_ID]
    update_interval: timedelta = config["update_interval"]

    # TODO(richo) why does it string the default value
    if config["use_sn"] and config["use_sn"] != "False":
        unique_id = gateway
    else:
        unique_id = None

    # TODO(richo) why does it string the default value
    if config["prefix"] and config["prefix"] != "False":
        prefix = config["prefix"]
    else:
        prefix = "FranklinWH"

    await _async_add_selects(
        hass, async_add_entities, username, password, gateway,
        prefix, unique_id, update_interval,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up cloud-backed selects from a config entry.

    __init__.py forwards SELECT only for connection_type "cloud" and "both",
    so reaching here always means cloud credentials are present. In "both"
    mode telemetry comes from Modbus and only these writable controls use the
    cloud, which is the intended hybrid split.
    """
    await _async_add_selects(
        hass,
        async_add_entities,
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data.get("gateway_id") or entry.data.get("serial", ""),
        "FranklinWH",
        # Cloud-only entries carry gateway_id but no serial, so falling back
        # keeps entities registry-tracked (renameable, assignable to an area)
        # instead of silently unique_id-less.
        entry.data.get("serial") or entry.data.get("gateway_id") or None,
        timedelta(seconds=DEFAULT_UPDATE_INTERVAL),
    )


async def _read_mode_and_export(client) -> dict:
    """Read mode, reserve SOC and export settings for the coordinator.

    Module level so the error handling is testable; it used to be a closure.
    """
    try:
        operating_mode, reserve_soc = await _read_operating_mode(client)
        export_mode, export_limit_kw = await _read_export_settings(client)
    except Exception as err:
        # Name the type: franklinwh raises bare exceptions such as
        # InvalidDataException() whose str() is empty, which produced the
        # contentless "Error fetching FranklinWH mode/export status:" in #82.
        raise UpdateFailed(
            f"Error fetching FranklinWH mode/export status: {describe_exception(err)}"
        ) from err
    return {
        "operating_mode": operating_mode,
        "reserve_soc": reserve_soc,
        "export_mode": export_mode,
        "export_limit_kw": export_limit_kw,
    }


async def _async_add_selects(
    hass: HomeAssistant,
    async_add_entities: AddEntitiesCallback,
    username: str,
    password: str,
    gateway: str,
    prefix: str,
    unique_id: str | None,
    update_interval: timedelta,
) -> None:
    """Build the cloud client, coordinator and select entities.

    Shared by the YAML platform and the config entry so the two cannot drift.
    """
    from . import get_shared_client  # noqa: PLC0415

    client = await get_shared_client(hass, username, password, gateway)

    async def _update_data() -> dict:
        return await _read_mode_and_export(client)

    coordinator = DataUpdateCoordinator[dict](
        hass,
        _LOGGER,
        name="franklinwh_mode",
        update_method=_update_data,
        update_interval=update_interval,
        always_update=False,
    )

    await coordinator.async_refresh()

    async_add_entities(
        [
            OperatingModeSelect(coordinator, prefix, unique_id, client),
            ExportModeSelect(coordinator, prefix, unique_id, client),
        ]
    )


class FranklinSelectBase(
    CoordinatorEntity[DataUpdateCoordinator[dict]], SelectEntity
):
    """Base class for FranklinWH select entities."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[dict],
        prefix: str,
        unique_id: str | None,
        client,
        name_suffix: str,
        unique_id_suffix: str,
    ) -> None:
        """Initializer."""
        super().__init__(coordinator)
        self._attr_name = f"{prefix} {name_suffix}"
        self._client = client
        if unique_id:
            self._attr_has_entity_name = True
            self._attr_unique_id = unique_id + unique_id_suffix

    @property
    def available(self) -> bool:
        """Entity is available when the coordinator has data."""
        return (
            self.coordinator.last_update_success
            and self.coordinator.data is not None
        )


class OperatingModeSelect(FranklinSelectBase):
    """Select entity for the FranklinWH operating mode.

    Allows switching between Self Consumption, Time of Use, and Emergency
    Backup directly from Home Assistant. The current reserve SOC is read
    from the device and preserved when changing modes.
    """

    _attr_options = OPERATING_MODES

    def __init__(self, coordinator, prefix, unique_id, client) -> None:
        """Initializer."""
        super().__init__(
            coordinator,
            prefix,
            unique_id,
            client,
            "Operating Mode",
            "_operating_mode",
        )

    @property
    def current_option(self) -> str | None:
        """Return the currently active operating mode."""
        if self.coordinator.data:
            return self.coordinator.data.get("operating_mode")
        return None

    async def async_select_option(self, option: str) -> None:
        """Change the operating mode, preserving that mode's reserve SOC.

        Never call franklinwh's Mode factories without an explicit soc. They
        default to 20 (100 for emergency backup), and set_mode writes whatever
        it is given — which silently replaced a user's 33% reserve with 20%
        (richo/homeassistant-franklinwh#82).
        """
        factory = _MODE_FACTORIES.get(option)
        if factory is None:
            raise HomeAssistantError(f"Unknown operating mode: {option}")

        if option == self.current_option:
            _LOGGER.debug("Operating mode already %s — not writing", option)
            return

        soc = await _read_reserve_for_mode(self._client, option)
        if soc is None:
            # Refuse rather than let the library write its default over a
            # reserve we could not read.
            raise HomeAssistantError(
                f"Cannot switch to {option}: the device did not report its "
                f"{_MODE_SOC_KEYS[option]} reserve, and changing mode without "
                "it would overwrite your reserve SOC with a default."
            )

        # set_mode reads the gateway's TOU settings and carries the profile id,
        # oldIndex and Storm Hedge (stromEn) through, so nothing else changes.
        _LOGGER.info("Changing operating mode to %s (reserve SOC %s%%)", option, soc)
        mode = factory(soc=soc)
        await self._client.set_mode(mode)
        # Optimistically update local state — the device takes a moment to
        # apply the change so an immediate API read-back may not reflect it yet.
        if self.coordinator.data is not None:
            self.coordinator.data["operating_mode"] = option
        self.async_write_ha_state()


class ExportModeSelect(FranklinSelectBase):
    """Select entity for the FranklinWH grid export mode.

    Controls whether solar only, solar and battery, or no power is exported
    to the grid. The current export power limit is preserved when changing
    modes.
    """

    _attr_options = EXPORT_MODES

    def __init__(self, coordinator, prefix, unique_id, client) -> None:
        """Initializer."""
        super().__init__(
            coordinator,
            prefix,
            unique_id,
            client,
            "Export Mode",
            "_export_mode",
        )

    @property
    def current_option(self) -> str | None:
        """Return the current grid export mode."""
        if self.coordinator.data:
            return self.coordinator.data.get("export_mode")
        return None

    async def async_select_option(self, option: str) -> None:
        """Change the export mode, preserving the device's power limit.

        The limit is read back from the device rather than taken from
        coordinator data. set_export_settings treats limit_kw=None as
        *unlimited*, so a stale or failed refresh would have silently removed a
        configured export cap — the same way a missing reserve SOC silently
        reset the battery reserve in #82.
        """
        if option not in _EXPORT_MODE_MAP:
            raise HomeAssistantError(f"Unknown export mode: {option}")

        if option == self.current_option:
            _LOGGER.debug("Export mode already %s — not writing", option)
            return

        try:
            current_mode, limit_kw = await _read_export_settings(self._client)
        except Exception as err:
            raise HomeAssistantError(
                f"Cannot switch export mode to {option}: the current export "
                f"limit could not be read ({describe_exception(err)}), and "
                "writing without it would clear your export cap."
            ) from err

        if limit_kw is not None and limit_kw <= 0:
            # no_export stores gridFeedMax 0.0, which is outside the valid
            # 0.1-10000 kW range and is an artifact of that mode rather than a
            # cap the user chose. Carrying it into solar export would cap at
            # 0 kW and look like the mode change did nothing.
            _LOGGER.info(
                "Export limit reads %s kW under %s; treating as no cap. Set one "
                "with the Export Limit control if you want one.",
                limit_kw,
                current_mode,
            )
            limit_kw = None

        await _write_export_settings(self._client, option, limit_kw)
        # Optimistically update local state
        if self.coordinator.data is not None:
            self.coordinator.data["export_mode"] = option
        self.async_write_ha_state()
