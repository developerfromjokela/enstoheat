"""The Ensto integration models."""
from __future__ import annotations

from dataclasses import dataclass

from .helpers import EnstoThermostatLE

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

@dataclass(frozen=True)
class EnstoThermostatState:
    target_temp: float = 0
    room_temp: float = 0
    floor_temp: float = 0
    calibration_temp: float = 0
    boost_on: bool = False
    boost_offset: float = 0
    boost_left: float = 0

@dataclass
class ThermostatData:
    """Data for the led ble integration."""

    title: str
    device: EnstoThermostatLE
    coordinator: DataUpdateCoordinator
