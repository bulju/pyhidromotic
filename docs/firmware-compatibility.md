# Firmware compatibility

The initial outage was a renewed IP address, not demonstrated firmware breakage.

The supplied `manufacurer/` JavaScript describes a gateway app that communicates
via `/ws` and receives decoded JSON snapshots and patches. The board continues
to expose local binary communication through `/rpc`.

Later inspection found a second interface served **by the board itself**:
`/?v=806` loads `/h.js?v=806`, including the binary decoder. The Python parser has
been updated from that local decoder and checked against configuration received
from the PIC v704 board. See [logic-comparison.md](logic-comparison.md) for
protocol evidence, fixes, tests, and remaining hardware-validation limits.

The Python client continues to use local `/rpc` communication. Gateway JSON
snapshots/patches, authentication, and device routing are a different transport
and are not automatically interchangeable with the local connection.
