# pyhidromotic

Python client for Hidromotic CHI Smart irrigation controllers.

## Installation

```bash
pip install pyhidromotic
```

## Usage

```python
import asyncio
from pyhidromotic import HidromoticClient

async def main():
    # Create client
    client = HidromoticClient("192.168.1.100")

    # Connect to device
    if await client.connect():
        # Wait for initial data
        await asyncio.sleep(2)

        # Get zones
        zones = client.get_zones()
        print(f"Found {len(zones)} zones")

        # Turn on zone 0
        await client.set_zone_state(0, True)

        # Check auto irrigation status
        if client.is_auto_riego_on():
            print("Auto irrigation is enabled")

        # Disconnect
        await client.disconnect()

asyncio.run(main())
```

## Features

- WebSocket-based communication with Hidromotic devices
- Support for CHI Smart and CHI Smart Mini models
- Control irrigation zones and tanks
- Enable/disable automatic irrigation
- Real-time status updates via callbacks
- Automatic reconnection on connection loss

## Supported devices

- Hidromotic CHI Smart (12 outputs)
- Hidromotic CHI Smart Mini (6 outputs)

## Device state and commands

Configuration and running updates are decoded from the board's local binary
protocol. Physical output slots, module IDs, labels, and tank values come from
the device. Tank data includes `modo`, `nivel`, and `tipo_flotante`; `config_raw`
retains the corresponding configuration bytes for diagnostics.

`is_auto_riego_on()` returns `None` before an irrigation mode is available.
A running update's rain or active-schedule fields do not change this mode.
`get_tank_level()` returns `"unknown"` if no level is available.

`set_auto_riego(on, persist=True)` saves to flash, matching the manufacturer UI.
Use `persist=False` for a temporary change. The client requests fresh
configuration and waits for reported mode rather than changing local state
optimistically. Disconnected command sends raise `ConnectionError`; returning
from a command means it was sent, not that a physical action has completed.

Additional methods include `get_outputs()`, `get_schedules()`, `get_tasks()`,
`stop_pump()`, `set_schedule_state(schedule_id, on)`, and
`set_output_state(slot_id, on)`. Output diagnostics include empty/disabled slots;
zone and tank views omit disabled outputs. Full automatic tanks reject ignored
start requests, and hose starts respect external pause.

The decoder is verified against the board's local ESP 806 JavaScript and PIC 704
configuration captures. Unknown sections and incomplete frames are rejected
without publishing partial state. See [the protocol review](docs/logic-comparison.md)
for validation details and firmware limitations.

## License

GPL-3.0-or-later
