"""The Ensto integration models."""
from __future__ import annotations

from dataclasses import dataclass

from .helpers import EnstoThermostatLE

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

@dataclass
class ThermostatData:
    """Data for the led ble integration."""
    title: str
    device: EnstoThermostatLE
    coordinator: DataUpdateCoordinator
