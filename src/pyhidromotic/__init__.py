"""Python client for Hidromotic CHI Smart irrigation controllers."""

from .client import HidromoticClient, hex_to_int, int_to_hex
from .const import (
    OUTPUT_TYPE_CICLON,
    OUTPUT_TYPE_MANGUERA,
    OUTPUT_TYPE_PISCINA,
    OUTPUT_TYPE_TANQUE,
    OUTPUT_TYPE_ZONA,
    STATE_DISABLED,
    STATE_OFF,
    STATE_ON,
    STATE_PAUSED,
    STATE_WAITING,
    TANK_EMPTY,
    TANK_FULL,
    TANK_LEVEL_FAIL,
    TANK_MEDIUM,
    TANK_SENSOR_FAIL,
)

__all__ = [
    # Client
    "HidromoticClient",
    "hex_to_int",
    "int_to_hex",
    # Output types
    "OUTPUT_TYPE_CICLON",
    "OUTPUT_TYPE_MANGUERA",
    "OUTPUT_TYPE_PISCINA",
    "OUTPUT_TYPE_TANQUE",
    "OUTPUT_TYPE_ZONA",
    # States
    "STATE_DISABLED",
    "STATE_OFF",
    "STATE_ON",
    "STATE_PAUSED",
    "STATE_WAITING",
    # Tank levels
    "TANK_EMPTY",
    "TANK_FULL",
    "TANK_LEVEL_FAIL",
    "TANK_MEDIUM",
    "TANK_SENSOR_FAIL",
]

__version__ = "0.1.0"
