"""Constants for Hidromotic devices."""

# Output types from device firmware (tipo & 0xF0)
OUTPUT_TYPE_MANGUERA = 0x10  # Hose
OUTPUT_TYPE_TANQUE = 0x20  # Tank
OUTPUT_TYPE_ZONA = 0x40  # Zone (flooding zone)
OUTPUT_TYPE_PISCINA = 0x50  # Pool
OUTPUT_TYPE_CICLON = 0x60  # Cyclon

# Output states (estado)
STATE_OFF = 0
STATE_ON = 1
STATE_PAUSED = 4
STATE_SHORT_CIRCUIT = 2
STATE_OPEN_CIRCUIT = 3
STATE_TIMEOUT = 5
STATE_WAITING = STATE_TIMEOUT  # Legacy alias; this means time exhausted.
STATE_TURNING_OFF = 10
STATE_TURNING_ON = 11
STATE_OPEN_CIRCUIT_ALT = 49

# Pump status has its own meanings for values 5 and 11.
PUMP_NO_WATER = 5
PUMP_RECOVERING = 11
STATE_DISABLED = 7  # Disconnected/disabled

# Tank levels (nivel)
TANK_FULL = 0
TANK_EMPTY = 1
TANK_SENSOR_FAIL = 2
TANK_LEVEL_FAIL = 3
TANK_MEDIUM = 4
