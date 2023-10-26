from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EnstoThermostatState:
    target_temp: float = 0
    room_temp: float = 0
    floor_temp: float = 0
    calibration_temp: float = 0
    boost_on: bool = False
    boost_offset: float = 0
    boost_left: float = 0
