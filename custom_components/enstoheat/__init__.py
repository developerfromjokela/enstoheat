"""The Ensto BLE integration."""
import asyncio
import logging
from datetime import timedelta

from bleak_retry_connector import BleakError, close_stale_connections, get_device, BLEAK_EXCEPTIONS

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.match import ADDRESS, BluetoothCallbackMatcher
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN, UPDATE_SECONDS, DEVICE_TIMEOUT
from .models import ThermostatData, EnstoThermostatLE
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up LD2410 BLE from a config entry."""
    address: str = entry.data[CONF_ADDRESS]
    ble_device = bluetooth.async_ble_device_from_address(
        hass, address.upper(), True
    ) or await get_device(address)
    if not ble_device:
        raise ConfigEntryNotReady(
            f"Could not find LD2410B device with address {address}"
        )

    await close_stale_connections(ble_device)

    ensto_therm = EnstoThermostatLE(ble_device)

    try:
        await ensto_therm.initialise()
    except BleakError as exc:
        raise ConfigEntryNotReady(
            f"Could not initialise Ensto device with address {address}"
        ) from exc

    @callback
    def _async_update_ble(
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Update from a ble callback."""
        ensto_therm.set_ble_device_and_advertisement_data(
            service_info.device, service_info.advertisement
        )

    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            _async_update_ble,
            BluetoothCallbackMatcher({ADDRESS: address}),
            bluetooth.BluetoothScanningMode.ACTIVE,
        )
    )

    async def _async_update():
        """Update the device state."""
        try:
            await ensto_therm.update()
        except BLEAK_EXCEPTIONS as ex:
            raise UpdateFailed(str(ex)) from ex


    startup_event = asyncio.Event()
    cancel_first_update = ensto_therm.register_callback(lambda *_: startup_event.set())
    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=ensto_therm.name,
        update_method=_async_update,
        update_interval=timedelta(seconds=UPDATE_SECONDS),
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        cancel_first_update()
        raise

    try:
        async with asyncio.timeout(DEVICE_TIMEOUT):
            await startup_event.wait()
    except asyncio.TimeoutError as ex:
        raise ConfigEntryNotReady(
            "Unable to communicate with the device; "
            f"Try moving the Bluetooth adapter closer to {ensto_therm.name}"
        ) from ex
    finally:
        cancel_first_update()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = ThermostatData(
        entry.title, ensto_therm, coordinator
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    async def _async_stop(event: Event) -> None:
        """Close the connection."""
        await ensto_therm.stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop)
    )
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    data: ThermostatData = hass.data[DOMAIN][entry.entry_id]
    if entry.title != data.title:
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        data: ThermostatData = hass.data[DOMAIN].pop(entry.entry_id)
        await data.device.stop()

    return unload_ok
