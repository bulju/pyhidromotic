# Decoder review and fixes

The original connection failure was an IP change. The review subsequently found
independent parser and state-reporting defects, now addressed in the client.

## Protocol evidence

The supplied files in `manufacurer/` are a gateway frontend. They receive decoded
JSON and reference backend Go files. However, the board also serves a **local**
interface: `/` redirects to `/?v=806`, whose script is `/h.js?v=806`. That script
contains a complete binary decoder in `process()` (C and D branches). We retrieved
it read-only through the SSH tunnel. Its SHA-256 is
`c2329284383f45774781b935694f8338f3b564043c266a0b5c26dfc552461260`.

`src/pyhidromotic/protocol.py` implements bounded reads based on that source,
without copying its UI implementation or using installation-specific offsets.
The source was inspected locally rather than committed wholesale. The checked-in
v704 fixture contains only binary controller configuration; the JSON handshake
contains Wi-Fi credentials and is deliberately excluded.

## Corrected behavior

| Original defect | Current behavior |
| --- | --- |
| Scanned arbitrary bytes for B/R/S markers | Walks sections and consumes their complete fields, labels, and module extensions |
| Missed R after schedules | Decodes R wherever it appears at a valid section boundary |
| Assumed running R carried automatic mode | Running R updates active schedule and rain only; configuration R supplies mode |
| Skipped unrecognized output bytes and lost physical slot mapping | Reads all slots, including hoses, pools, cyclones, tanks, zones, empty and disabled slots |
| Rejected fault/transition states above 7 | Preserves reported states, including 10, 11, 49 |
| Called output state 5 waiting | Exports STATE_TIMEOUT; STATE_WAITING remains a legacy alias; pump meanings are separate |
| Ignored tank metadata | Reads mode, level, and float type after the label; retains config_raw for diagnostics |
| Consumed tank extensions even when disabled | Disabled records have no module extension, matching local h.js |
| Output dictionary keys were off by one | Keys and embedded slot_id both use zero-based physical slots |
| Skipped an exact six-byte final output | Accepts it when it completes the model's output count |
| Pump D updates advanced six bytes | Consumes all seven bytes: marker, state, pause, uint32 running minutes |
| Tank and cyclone D extensions could be read as markers | Consumes level, or conditional cyclone countdown, structurally |
| Short packets crashed the listener | Bounds errors become ProtocolError; malformed frames are logged and ignored |
| Parsing partially cleared or mutated public state | Publishes a validated replacement atomically; rejected updates leave previous data intact |
| Updates before configuration raised KeyError | Requests configuration; D frames require matching model, firmware, and configuration counter |
| Silent disconnected sends and optimistic auto mode | Disconnected sends raise ConnectionError; mode changes only when reported by C |
| Automatic mode not saved like manufacturer's UI | set_auto_riego defaults to persist=True and appends #@+;; persist=False skips flash save |
| Generic tank start on v114 | Uses #@T<index>M1; on v114; stops use physical slot commands |
| Start requests for full automatic idle tanks silently ignored | Client raises ValueError before sending the known ignored request |
| Reconnection could duplicate resources or remain disabled after disconnect | Serialized/idempotent connect, single retry task, explicit reconnect restores retries, owned resources cleaned up |
| Callback removal could skip another callback | Iterates a subscription snapshot; unregister is idempotent |
| JSON debug logging exposed Wi-Fi credentials | Does not log handshake contents; RPC errors and PIC reset are handled without logging the payload |

Unknown section types reject the frame; they are never skipped by scanning for a
plausible next marker. This is intentional: unsupported firmware must not silently
produce incorrect physical slot assignments.

## Newly exposed information and controls

- `get_outputs()` includes every physical slot for diagnostics; `get_zones()` and
  `get_tanks()` continue to omit disabled outputs from normal controls.
- `get_schedules()` returns status, active zone, mode, duration, accessories,
  enabled state, BCD-decoded start time, day/parity rules, zone durations by
  physical slot, and name.
- `get_tasks()` exposes the queue and the first task's remaining duration
  (seconds for cyclone tasks, minutes for other modules).
- `data` includes inputs, pump timings/runtime, irrigation rain/active schedule,
  button configuration, battery, and reset counters when reported.
- `stop_pump()` and `set_schedule_state()` use the commands shown by the
  manufacturer. `set_output_state()` provides physical-slot control for supported
  modules, including hose external-pause, tank-full, v114, disabled-output, and
  cyclone stop-only rules.

Command completion means the request was sent, not that the hardware action has
finished. Subscribers receive actual state updates. In particular, automatic
mode is separate from rain blocking, an active task, or a pump recovery state.

## Captured board observations

The original 364-byte PIC v704 configuration decodes twelve slots, four active
zone entities, one tank, and four schedules. Tank mode is **1 (automatic)**,
level **0 (full)**, and float type **1**. Automatic irrigation mode is **0 (off)**.
Zone labels and their physical slots are retained exactly as reported; the names
"Manguera Verde" and "Pileta" are assigned to zone module types.

A subsequent read-only connection also returned 358-byte configurations, which
were accepted structurally without changing any offsets or installation values.

## Validation and limits

The final regression suite passes 85 tests. Ruff and mypy checks pass.

Tests cover the real binary fixture, synthetic Mini/mixed-type frames, named and
disabled tanks, cyclone extensions, section-like bytes in labels, transitions,
rain updates without mode changes, short/unknown packets, atomicity, configuration
counter mismatch, disconnected sends, persistence formatting, tank rules, callbacks,
and connection cleanup. Synthetic D frames follow the manufacturer decoder;
they are not represented as hardware captures.

The updated client also passed a live read-only connection/configuration/
disconnection check: twelve slots, four zones, four schedules, automatic tank
mode, full tank level, and irrigation off. No pump, zone, tank, schedule, or
controller setting was changed during live checks. Physical command execution, persistence across a power cycle, and every
firmware/hardware combination remain untested. The v114 command exception is
source-backed, but v114 binary compatibility is not established by the v704 board.

Configuration-writing features (editing output types/names/durations, schedules,
inputs, pump delays, button mappings, clock, and maintenance/reset actions) remain
outside the library's current configuration API. Their absence is feature scope,
not a decoder fallback; no guessed command encoding has been added for them.
