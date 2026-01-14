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

## License

GPL-3.0-or-later
