from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.backends.service import BleakGATTCharacteristic, BleakGATTServiceCollection
from bleak.exc import BleakDBusError
from bleak_retry_connector import BLEAK_RETRY_EXCEPTIONS as BLEAK_EXCEPTIONS
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    BleakError,
    BleakNotFoundError,
    establish_connection,
    retry_bluetooth_connection_error,
)

from .const import READ_BOOST_CHARACTERISTIC_UUID
from .exceptions import CharacteristicMissingError
from .dataclasses import EnstoThermostatState

_LOGGER = logging.getLogger(__name__)

DISCONNECT_DELAY = 120
DEFAULT_ATTEMPTS = 3
BLEAK_BACKOFF_TIME = 0.25

class EnstoThermostatLE:
    def __init__(
        self, ble_device: BLEDevice, advertisement_data: AdvertisementData | None = None
    ) -> None:
        """Init the Thermostat."""
        self._ble_device = ble_device
        self._advertisement_data = advertisement_data
        self._operation_lock = asyncio.Lock()
        self._state = EnstoThermostatState()
        self._connect_lock: asyncio.Lock = asyncio.Lock()
        self._read_char: BleakGATTCharacteristic | None = None
        self._write_char: BleakGATTCharacteristic | None = None
        self._disconnect_timer: asyncio.TimerHandle | None = None
        self._client: BleakClientWithServiceCache | None = None
        self._expected_disconnect = False
        self.loop = asyncio.get_running_loop()
        self._callbacks: list[Callable[[EnstoThermostatState], None]] = []

    @property
    def address(self) -> str:
        """Return the address."""
        return self._ble_device.address

    @property
    def _address(self) -> str:
        """Return the address."""
        return self._ble_device.address

    @property
    def name(self) -> str:
        """Get the name of the device."""
        return self._ble_device.name or self._ble_device.address

    @property
    def rssi(self) -> int | None:
        """Get the rssi of the device."""
        if self._advertisement_data:
            return self._advertisement_data.rssi
        return None

    @property
    def state(self) -> EnstoThermostatState:
        """Return the state."""
        return self._state


    async def stop(self) -> None:
        """Stop the EnstoThermostat."""
        _LOGGER.debug("%s: Stop", self.name)
        await self._execute_disconnect()


    def _fire_callbacks(self) -> None:
        """Fire the callbacks."""
        for callback in self._callbacks:
            callback(self._state)

    async def update(self) -> None:
        """Update the EnstoThermostat."""
        await self._ensure_connected()
        _LOGGER.debug("%s: Updating", self.name)
        data = await self._read_data()
        self._data_notification_handler(0, data)

    def register_callback(
        self, callback: Callable[[EnstoThermostatState], None]
    ) -> Callable[[], None]:
        """Register a callback to be called when the state changes."""

        def unregister_callback() -> None:
            self._callbacks.remove(callback)

        self._callbacks.append(callback)
        return unregister_callback

    async def _ensure_connected(self) -> None:
        """Ensure connection to device is established."""
        if self._connect_lock.locked():
            _LOGGER.debug(
                "%s: Connection already in progress, waiting for it to complete; RSSI: %s",
                self.name,
                self.rssi,
            )
        if self._client and self._client.is_connected:
            self._reset_disconnect_timer()
            return
        async with self._connect_lock:
            # Check again while holding the lock
            if self._client and self._client.is_connected:
                self._reset_disconnect_timer()
                return
            _LOGGER.debug("%s: Connecting; RSSI: %s", self.name, self.rssi)
            client = await establish_connection(
                BleakClientWithServiceCache,
                self._ble_device,
                self.name,
                self._disconnected,
                use_services_cache=True,
                ble_device_callback=lambda: self._ble_device,
            )
            _LOGGER.debug("%s: Connected; RSSI: %s", self.name, self.rssi)
            resolved = self._resolve_characteristics(client.services)
            if not resolved:
                # Try to handle services failing to load
                resolved = self._resolve_characteristics(await client.get_services())

            self._client = client
            self._reset_disconnect_timer()

            _LOGGER.debug(
                "%s: Subscribe to notifications; RSSI: %s", self.name, self.rssi
            )
            await client.start_notify(self._read_char, self._data_notification_handler)




    def _data_notification_handler(self, _sender: int, data: bytearray) -> None:
        """Handle notification responses."""
        _LOGGER.debug("%s: Notification received: %s", self.name, data.hex())

        if len(data) < 20:
            return

        target_temp = (data[1]+256*data[2]) / 10.0
        room_temp = (data[4]+256*data[5]) / 10.0
        floor_temp = (data[4]+256*data[5]) / 10.0

        self._state = EnstoThermostatState(
            target_temp=target_temp,
            room_temp=room_temp,
            floor_temp=floor_temp
        )

        _LOGGER.debug(
            "%s: Notification received; RSSI: %s: %s %s",
            self.name,
            self.rssi,
            data.hex(),
            self._state,
        )

        self._fire_callbacks()

    def _reset_disconnect_timer(self) -> None:
        """Reset disconnect timer."""
        if self._disconnect_timer:
            self._disconnect_timer.cancel()
        self._expected_disconnect = False
        self._disconnect_timer = self.loop.call_later(
            DISCONNECT_DELAY, self._disconnect
        )

    def _disconnected(self, client: BleakClientWithServiceCache) -> None:
        """Disconnected callback."""
        if self._expected_disconnect:
            _LOGGER.debug(
                "%s: Disconnected from device; RSSI: %s", self.name, self.rssi
            )
            return
        _LOGGER.warning(
            "%s: Device unexpectedly disconnected; RSSI: %s",
            self.name,
            self.rssi,
        )

    def _disconnect(self) -> None:
        """Disconnect from device."""
        self._disconnect_timer = None
        asyncio.create_task(self._execute_timed_disconnect())

    async def _execute_timed_disconnect(self) -> None:
        """Execute timed disconnection."""
        _LOGGER.debug(
            "%s: Disconnecting after timeout of %s",
            self.name,
            DISCONNECT_DELAY,
        )
        await self._execute_disconnect()

    async def _execute_disconnect(self) -> None:
        """Execute disconnection."""
        async with self._connect_lock:
            read_char = self._read_char
            client = self._client
            self._expected_disconnect = True
            self._client = None
            self._read_char = None
            self._write_char = None
            if client and client.is_connected:
                if read_char:
                    try:
                        await client.stop_notify(read_char)
                    except BleakError:
                        _LOGGER.debug(
                            "%s: Failed to stop notifications", self.name, exc_info=True
                        )
                await client.disconnect()

    @retry_bluetooth_connection_error(DEFAULT_ATTEMPTS)
    async def _send_command_locked(self, commands: list[bytes]) -> None:
        """Send command to device and read response."""
        try:
            await self._execute_command_locked(commands)
        except BleakDBusError as ex:
            # Disconnect so we can reset state and try again
            await asyncio.sleep(BLEAK_BACKOFF_TIME)
            _LOGGER.debug(
                "%s: RSSI: %s; Backing off %ss; Disconnecting due to error: %s",
                self.name,
                self.rssi,
                BLEAK_BACKOFF_TIME,
                ex,
            )
            await self._execute_disconnect()
            raise
        except BleakError as ex:
            # Disconnect so we can reset state and try again
            _LOGGER.debug(
                "%s: RSSI: %s; Disconnecting due to error: %s", self.name, self.rssi, ex
            )
            await self._execute_disconnect()
            raise

    async def _execute_command_locked(self, commands: list[bytes]) -> None:
        """Execute command and read response."""
        assert self._client is not None  # nosec
        if not self._read_char:
            raise CharacteristicMissingError("Read characteristic missing")
        if not self._write_char:
            raise CharacteristicMissingError("Write characteristic missing")
        for command in commands:
            await self._client.write_gatt_char(self._write_char, command, False)

    async def _read_data_locked(self) -> bytearray:
        assert self._client is not None  # nosec
        if not self._read_char:
            raise CharacteristicMissingError("Read characteristic missing")
        return await self._client.read_gatt_char(self._read_char)

    async def _send_command(
        self, commands: list[bytes] | bytes, retry: int | None = None
    ) -> None:
        """Send command to device and read response."""
        await self._ensure_connected()
        if not isinstance(commands, list):
            commands = [commands]
        await self._send_command_while_connected(commands, retry)


    async def _read_data(self) -> bytearray:
        """Send command to device and read response."""
        await self._ensure_connected()
        return await self._read_data_while_connected()


    async def _read_data_while_connected(self) -> bytearray:
        """Send command to device and read response."""
        _LOGGER.debug(
            "%s: Reading data,",
            self.name
       )
        if self._operation_lock.locked():
            _LOGGER.debug(
                "%s: Operation already in progress, waiting for it to complete; RSSI: %s",
                self.name,
                self.rssi,
            )
        async with self._operation_lock:
            try:
                return await self._read_data_locked()
            except BleakNotFoundError:
                _LOGGER.error(
                    "%s: device not found, no longer in range, or poor RSSI: %s",
                    self.name,
                    self.rssi,
                    exc_info=True,
                )
                raise
            except CharacteristicMissingError as ex:
                _LOGGER.debug(
                    "%s: characteristic missing: %s; RSSI: %s",
                    self.name,
                    ex,
                    self.rssi,
                    exc_info=True,
                )
                raise
            except BLEAK_EXCEPTIONS:
                _LOGGER.debug("%s: communication failed", self.name, exc_info=True)
                raise

        raise RuntimeError("Unreachable")

    async def _send_command_while_connected(
        self, commands: list[bytes], retry: int | None = None
    ) -> None:
        """Send command to device and read response."""
        _LOGGER.debug(
            "%s: Sending commands %s",
            self.name,
            [command.hex() for command in commands],
        )
        if self._operation_lock.locked():
            _LOGGER.debug(
                "%s: Operation already in progress, waiting for it to complete; RSSI: %s",
                self.name,
                self.rssi,
            )
        async with self._operation_lock:
            try:
                await self._send_command_locked(commands)
                return
            except BleakNotFoundError:
                _LOGGER.error(
                    "%s: device not found, no longer in range, or poor RSSI: %s",
                    self.name,
                    self.rssi,
                    exc_info=True,
                )
                raise
            except CharacteristicMissingError as ex:
                _LOGGER.debug(
                    "%s: characteristic missing: %s; RSSI: %s",
                    self.name,
                    ex,
                    self.rssi,
                    exc_info=True,
                )
                raise
            except BLEAK_EXCEPTIONS:
                _LOGGER.debug("%s: communication failed", self.name, exc_info=True)
                raise

        raise RuntimeError("Unreachable")


    def _resolve_characteristics(self, services: BleakGATTServiceCollection) -> bool:
        """Resolve characteristics."""
        if char := services.get_characteristic(READ_BOOST_CHARACTERISTIC_UUID):
            self._read_char = char
        return bool(self._read_char)

