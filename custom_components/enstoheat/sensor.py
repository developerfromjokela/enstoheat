"""Ensto integration sensor platform."""


from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
from .models import EnstoThermostatLE
from .models import ThermostatData

TARGET_TEMPERATURE = SensorEntityDescription(
    key="target_temp",
    translation_key="ensto_target_temperature",
    device_class=SensorDeviceClass.TEMPERATURE,
    entity_registry_enabled_default=True,
    entity_registry_visible_default=True,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    state_class=SensorStateClass.MEASUREMENT,
)

ROOM_TEMPERATURE = SensorEntityDescription(
    key="room_temp",
    translation_key="ensto_room_temperature",
    device_class=SensorDeviceClass.TEMPERATURE,
    entity_registry_enabled_default=True,
    entity_registry_visible_default=True,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    state_class=SensorStateClass.MEASUREMENT,
)

FLOOR_TEMPERATURE = SensorEntityDescription(
    key="floor_temp",
    translation_key="ensto_floor_temperature",
    device_class=SensorDeviceClass.TEMPERATURE,
    entity_registry_enabled_default=True,
    entity_registry_visible_default=True,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    state_class=SensorStateClass.MEASUREMENT,
)


SENSOR_DESCRIPTIONS = (
    [
        TARGET_TEMPERATURE,
        ROOM_TEMPERATURE,
        FLOOR_TEMPERATURE
    ]
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the platform for LD2410BLE."""
    data: ThermostatData = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        EnstoHeatBLESensor(
            data.coordinator,
            data.device,
            entry.title,
            description,
        )
        for description in SENSOR_DESCRIPTIONS
    )


class EnstoHeatBLESensor(CoordinatorEntity[DataUpdateCoordinator], SensorEntity):
    """Generic sensor for LD2410BLE."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        device: EnstoThermostatLE,
        name: str,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._coordinator = coordinator
        self._device = device
        self._key = description.key
        self.entity_description = description
        self._attr_unique_id = f"{device.address}_{self._key}"
        self._attr_device_info = DeviceInfo(
            name=name,
            connections={(dr.CONNECTION_BLUETOOTH, device.address)},
        )
        self._attr_native_value = getattr(self._device.state, self._key)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_native_value = getattr(self._device.state, self._key)
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Unavailable if coordinator isn't connected."""
        return self._coordinator.connected and super().available
