"""Modbus TCP sensor platform for FranklinWH aGate.

Reads real-time system data locally via Modbus TCP (26-60ms) instead of
the cloud API (2-7s). Provides the same core sensors as the cloud-based
sensor platform plus additional data only available via Modbus (voltage,
frequency, temperatures, grid connection state).

Register mappings based on SunSpec 700-series DER models and FranklinWH
vendor extension registers (15500+), validated by the mtnears/FranklinWH-
Automation project against ~300k readings.

Configure alongside the cloud-based select/number platforms:
  - Modbus handles all READ operations (sensors)
  - Cloud API handles all WRITE operations (mode switching, export settings)
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import struct
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.components.sensor import (
    PLATFORM_SCHEMA as SENSOR_PLATFORM_SCHEMA,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_ID,
    CONF_PORT,
    PERCENTAGE,
    UnitOfElectricPotential,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
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

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 502
DEFAULT_UPDATE_INTERVAL = 30  # seconds
MODBUS_TIMEOUT = 5.0

# Sanity bounds — values near 0xFFFF are Modbus errors, not real data.
MAX_PLAUSIBLE_SOLAR_W = 25000
MAX_PLAUSIBLE_LOAD_W = 50000

# Operating mode map from extended register 15507.
_MODE_MAP = {
    0: "Standby",
    1: "Emergency Backup",
    2: "Self Consumption",
    3: "Time of Use",
}

PLATFORM_SCHEMA = SENSOR_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Optional(CONF_ID, default=""): cv.string,
        vol.Optional("prefix", default="FranklinWH"): cv.string,
        vol.Optional(
            "update_interval", default=DEFAULT_UPDATE_INTERVAL
        ): cv.time_period,
    }
)


# ── Modbus TCP reader ────────────────────────────────────────────────


async def _modbus_read(
    host: str, port: int, addr: int, count: int, unit: int = 1
) -> bytes | None:
    """Read holding registers via raw Modbus TCP socket.

    Uses asyncio streams instead of pymodbus to avoid adding a heavy
    dependency to the HA integration. Each read opens a fresh TCP
    connection (the aGate handles this fine at 30s intervals).
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=MODBUS_TIMEOUT
        )
    except (OSError, asyncio.TimeoutError) as err:
        _LOGGER.debug("Modbus connect failed: %s", err)
        return None

    try:
        # Modbus TCP: MBAP header + Read Holding Registers (func 0x03)
        request = struct.pack(
            ">HHHBBHH", 1, 0, 6, unit, 3, addr, count
        )
        writer.write(request)
        await writer.drain()

        response = await asyncio.wait_for(reader.read(2048), timeout=MODBUS_TIMEOUT)

        if len(response) < 9:
            return None
        func = response[7]
        if func == 3:
            byte_count = response[8]
            return response[9 : 9 + byte_count]
        if func == 0x83:
            _LOGGER.debug("Modbus exception at addr %d: code %d", addr, response[8])
        return None
    except (OSError, asyncio.TimeoutError) as err:
        _LOGGER.debug("Modbus read error at addr %d: %s", addr, err)
        return None
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack(">H", data[offset * 2 : (offset + 1) * 2])[0]


def _s16(data: bytes, offset: int) -> int:
    return struct.unpack(">h", data[offset * 2 : (offset + 1) * 2])[0]


async def _read_all_registers(host: str, port: int) -> dict[str, Any]:
    """Read all register blocks and parse into a data dict."""
    data: dict[str, Any] = {}

    # Model 713: SOC & battery DC voltage (addr 1035, len 7)
    m713 = await _modbus_read(host, port, 1035, 7)
    if m713 and len(m713) >= 8:
        data["soc"] = _u16(m713, 2) / 10.0
        data["battery_dc_voltage"] = _u16(m713, 3) / 10.0
    else:
        raise UpdateFailed("Modbus: failed to read SOC (model 713)")

    # Model 714: Battery DC power (addr 1048, len 1)
    m714 = await _modbus_read(host, port, 1048, 1)
    if m714 and len(m714) >= 2:
        data["battery_power"] = _s16(m714, 0) / 1000.0  # W → kW

    # Model 701: AC measurements (addr 72, len 50)
    m701 = await _modbus_read(host, port, 72, 50)
    if m701 and len(m701) >= 70:
        data["grid_connected"] = _u16(m701, 3) == 1
        data["der_connect_status"] = _u16(m701, 7)

        grid_w = _s16(m701, 8)
        data["grid_power"] = grid_w / 1000.0  # W → kW

        freq = _u16(m701, 16)
        if freq > 0:
            data["grid_frequency"] = freq / 1000.0

        v_ll = _u16(m701, 13)
        if 0 < v_ll < 0xFFFF:
            data["grid_voltage"] = v_ll / 10.0

        v_ln = _u16(m701, 14)
        if 0 < v_ln < 0xFFFF:
            data["grid_voltage_ln"] = v_ln / 10.0

        ambient = _s16(m701, 33)
        if ambient not in (0, -32768, 32767):
            data["ambient_temp"] = ambient / 10.0

        cabinet = _s16(m701, 34)
        if cabinet not in (0, -32768, 32767):
            data["cabinet_temp"] = cabinet / 10.0

    # Extended registers (addr 15500, len 15)
    ext = await _modbus_read(host, port, 15500, 15)
    if ext and len(ext) >= 20:
        solar_w = _u16(ext, 2)
        if solar_w != 0xFFFF and solar_w < MAX_PLAUSIBLE_SOLAR_W:
            data["solar_power"] = solar_w / 1000.0
        else:
            data["solar_power"] = 0.0

        home_w = _u16(ext, 6)
        if home_w != 0xFFFF and home_w < MAX_PLAUSIBLE_LOAD_W:
            data["home_load"] = home_w / 1000.0

        mode_raw = _u16(ext, 7)
        data["operating_mode"] = _MODE_MAP.get(mode_raw, f"Unknown ({mode_raw})")
        data["operating_mode_raw"] = mode_raw

        data["reserve_soc"] = _u16(ext, 8)
        data["reserve_soc_2"] = _u16(ext, 9)

    return data


# ── Platform setup ───────────────────────────────────────────────────


def _create_coordinator(
    hass: HomeAssistant,
    host: str,
    port: int,
    update_interval: timedelta,
) -> DataUpdateCoordinator[dict[str, Any]]:
    """Create a Modbus data update coordinator."""

    async def _update_data() -> dict[str, Any]:
        try:
            return await _read_all_registers(host, port)
        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Modbus read failed: {err}") from err

    return DataUpdateCoordinator[dict[str, Any]](
        hass,
        _LOGGER,
        name="franklinwh_modbus",
        update_method=_update_data,
        update_interval=update_interval,
        always_update=False,
    )


def _create_entities(
    coordinator: DataUpdateCoordinator[dict[str, Any]],
    prefix: str,
    uid: str | None,
) -> list[ModbusSensor]:
    """Create all Modbus sensor entities."""
    return [
        ModbusBatterySocSensor(coordinator, prefix, uid),
        ModbusGridPowerSensor(coordinator, prefix, uid),
        ModbusHomePowerSensor(coordinator, prefix, uid),
        ModbusBatteryPowerSensor(coordinator, prefix, uid),
        ModbusSolarPowerSensor(coordinator, prefix, uid),
        ModbusGridVoltageSensor(coordinator, prefix, uid),
        ModbusGridFrequencySensor(coordinator, prefix, uid),
        ModbusAmbientTempSensor(coordinator, prefix, uid),
        ModbusCabinetTempSensor(coordinator, prefix, uid),
        ModbusGridConnectedSensor(coordinator, prefix, uid),
        ModbusOperatingModeSensor(coordinator, prefix, uid),
        ModbusReserveSocSensor(coordinator, prefix, uid),
        ModbusBatteryDcVoltageSensor(coordinator, prefix, uid),
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Modbus sensors from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)
    serial = entry.data.get("serial", "")
    model = entry.data.get("model", "")

    prefix = "FranklinWH"
    update_interval = timedelta(seconds=DEFAULT_UPDATE_INTERVAL)

    coordinator = _create_coordinator(hass, host, port, update_interval)
    await coordinator.async_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][f"modbus_coordinator_{serial}"] = coordinator

    async_add_entities(_create_entities(coordinator, prefix, serial or None))


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up Modbus sensors from YAML config (legacy)."""
    host: str = config[CONF_HOST]
    port: int = config[CONF_PORT]
    gateway: str = config.get(CONF_ID, "")
    prefix: str = config["prefix"]
    update_interval: timedelta = config["update_interval"]

    coordinator = _create_coordinator(hass, host, port, update_interval)
    await coordinator.async_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][f"modbus_coordinator_{gateway}"] = coordinator

    async_add_entities(_create_entities(coordinator, prefix, gateway or None))


# ── Base class ───────────────────────────────────────────────────────


class ModbusSensor(
    CoordinatorEntity[DataUpdateCoordinator[dict[str, Any]]], SensorEntity
):
    """Base class for FranklinWH Modbus sensors."""

    _data_key: str = ""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[dict[str, Any]],
        prefix: str,
        unique_id: str | None,
        suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_name = f"{prefix} {suffix}"
        if unique_id:
            self._attr_unique_id = f"{unique_id}_modbus_{suffix.lower().replace(' ', '_')}"
            self._attr_has_entity_name = True

    @property
    def available(self) -> bool:
        return (
            self.coordinator.last_update_success
            and self.coordinator.data is not None
        )

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self._data_key)


# ── Sensor entities ──────────────────────────────────────────────────


class ModbusBatterySocSensor(ModbusSensor):
    """Battery state of charge (%)."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery"
    _data_key = "soc"

    def __init__(self, coordinator, prefix, uid) -> None:
        super().__init__(coordinator, prefix, uid, "Battery SOC")


class ModbusGridPowerSensor(ModbusSensor):
    """Grid active power (kW). Positive=importing, negative=exporting."""

    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _data_key = "grid_power"

    def __init__(self, coordinator, prefix, uid) -> None:
        super().__init__(coordinator, prefix, uid, "Grid Power")


class ModbusHomePowerSensor(ModbusSensor):
    """Home consumption (kW)."""

    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:home-lightning-bolt"
    _data_key = "home_load"

    def __init__(self, coordinator, prefix, uid) -> None:
        super().__init__(coordinator, prefix, uid, "Home Power")


class ModbusBatteryPowerSensor(ModbusSensor):
    """Battery DC power (kW). Negative=charging, positive=discharging."""

    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery-charging"
    _data_key = "battery_power"

    def __init__(self, coordinator, prefix, uid) -> None:
        super().__init__(coordinator, prefix, uid, "Battery Power")


class ModbusSolarPowerSensor(ModbusSensor):
    """Solar production (kW)."""

    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:solar-power"
    _data_key = "solar_power"

    def __init__(self, coordinator, prefix, uid) -> None:
        super().__init__(coordinator, prefix, uid, "Solar Power")


class ModbusGridVoltageSensor(ModbusSensor):
    """Grid voltage (V)."""

    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _data_key = "grid_voltage"

    def __init__(self, coordinator, prefix, uid) -> None:
        super().__init__(coordinator, prefix, uid, "Grid Voltage")


class ModbusGridFrequencySensor(ModbusSensor):
    """Grid frequency (Hz)."""

    _attr_native_unit_of_measurement = UnitOfFrequency.HERTZ
    _attr_device_class = SensorDeviceClass.FREQUENCY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _data_key = "grid_frequency"

    def __init__(self, coordinator, prefix, uid) -> None:
        super().__init__(coordinator, prefix, uid, "Grid Frequency")


class ModbusAmbientTempSensor(ModbusSensor):
    """Ambient temperature (°C)."""

    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _data_key = "ambient_temp"

    def __init__(self, coordinator, prefix, uid) -> None:
        super().__init__(coordinator, prefix, uid, "Ambient Temperature")


class ModbusCabinetTempSensor(ModbusSensor):
    """Cabinet temperature (°C)."""

    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _data_key = "cabinet_temp"

    def __init__(self, coordinator, prefix, uid) -> None:
        super().__init__(coordinator, prefix, uid, "Cabinet Temperature")


class ModbusGridConnectedSensor(ModbusSensor):
    """Grid connection status."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["Connected", "Disconnected"]
    _data_key = "grid_connected"

    def __init__(self, coordinator, prefix, uid) -> None:
        super().__init__(coordinator, prefix, uid, "Grid Status")

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        connected = self.coordinator.data.get("grid_connected")
        if connected is None:
            return None
        return "Connected" if connected else "Disconnected"


class ModbusOperatingModeSensor(ModbusSensor):
    """Current operating mode from Modbus register 15507."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(_MODE_MAP.values())
    _attr_icon = "mdi:battery-sync"
    _data_key = "operating_mode"

    def __init__(self, coordinator, prefix, uid) -> None:
        super().__init__(coordinator, prefix, uid, "Operating Mode (Local)")


class ModbusReserveSocSensor(ModbusSensor):
    """Active reserve SOC setting (%) from Modbus register 15508."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:battery-alert-variant-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _data_key = "reserve_soc"

    def __init__(self, coordinator, prefix, uid) -> None:
        super().__init__(coordinator, prefix, uid, "Reserve SOC (Local)")


class ModbusBatteryDcVoltageSensor(ModbusSensor):
    """Battery DC bus voltage (V)."""

    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _data_key = "battery_dc_voltage"

    def __init__(self, coordinator, prefix, uid) -> None:
        super().__init__(coordinator, prefix, uid, "Battery DC Voltage")
