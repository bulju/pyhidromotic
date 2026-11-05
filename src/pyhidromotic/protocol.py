"""Bounded binary decoding, verified against the board's local h.js (ESP 806).

Lengths are protocol fields, never offsets specific to a captured installation.
Unknown sections reject a frame rather than scanning payload bytes as markers.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .const import STATE_DISABLED


class ProtocolError(ValueError):
    """A frame is incomplete, unsupported, or structurally invalid."""


class Reader:
    """Read little-endian fields without crossing a frame boundary."""

    def __init__(self, data: bytes, offset: int = 0) -> None:
        self.data = data
        self.offset = offset

    def take(self, size: int) -> bytes:
        if self.offset + size > len(self.data):
            raise ProtocolError(f"Truncated field at byte {self.offset}")
        value = self.data[self.offset : self.offset + size]
        self.offset += size
        return value

    def number(self, size: int = 1) -> int:
        return int.from_bytes(self.take(size), "little")

    def label(self) -> str:
        return self.take(self.number()).decode("utf-8", errors="replace")


def _groups(state: dict[str, Any]) -> None:
    """Build public views while retaining disabled outputs for diagnostics."""
    names = {
        0x10: "hoses",
        0x20: "tanks",
        0x40: "zones",
        0x50: "pools",
        0x60: "cyclones",
    }
    labels = {0x10: "Hose", 0x20: "Tank", 0x40: "Zone", 0x50: "Pool", 0x60: "Cyclone"}
    for name in names.values():
        state[name] = {}
    for output in state.get("outputs", {}).values():
        kind = int(output["tipo"], 16) & 0xF0
        if kind not in names or output["estado"] == STATE_DISABLED:
            continue
        item = dict(output)
        item["label"] = output["label"] or f"{labels[kind]} {output['id'] + 1}"
        state[names[kind]][output["id"]] = item


def _task(r: Reader) -> dict[str, Any]:
    index, kind, mode = r.number(), r.number(), r.number()
    task = {"id": index, "tipo": f"{kind:02X}", "modo": mode}
    if index == 0:
        task["restante"] = r.number(2)
    return task


def decode_config(data: bytes) -> dict[str, Any]:
    """Decode a whole C frame atomically."""
    r = Reader(data)
    if r.take(1) != b"C":
        raise ProtocolError("Expected configuration")
    model = r.number()
    if model not in (ord("M"), ord("N")):
        raise ProtocolError("Unsupported controller model")
    state: dict[str, Any] = {
        "is_mini": model == ord("M"),
        "pic_version": r.number(2),
        "config_counter": r.number(2),
        "pic_id": f"0x{r.number():02X}",
        "pic_rev": r.number(),
        "header_raw": r.take(8).hex(),
        "outputs": {},
        "pump": {},
        "schedules": {},
        "inputs": {},
        "tasks": [],
        "auto_riego": None,
    }
    count = 6 if state["is_mini"] else 12
    seen: set[str] = set()
    while r.offset < len(data):
        marker = chr(r.number())
        if marker in ("S", "B", "E", "R"):
            if marker in seen:
                raise ProtocolError(f"Duplicate {marker} section")
            seen.add(marker)
        if marker == "l":
            state["led"] = r.number()
        elif marker == "k":
            state["button"] = {
                "enabled": r.number(),
                "functions": list(r.take(10)),
                "count": r.number(2),
                "fault": r.number(),
            }
        elif marker == "E":
            for index in range(4):
                state["inputs"][index] = dict(
                    zip(("tipo", "invert", "actual", "estado"), r.take(4), strict=True)
                )
        elif marker == "B":
            state["pump"] = {
                "estado": r.number(),
                "pausa_externa": r.number(),
                "marcha_min": r.number(4),
                "off_duracion_ms": r.number(2),
                "on_delay_ms": r.number(2),
                "off_delay_ms": r.number(2),
                "pozo_seco_start_s": r.number(2),
                "pozo_seco_retry_s": r.number(2),
            }
        elif marker == "S":
            for slot in range(count):
                kind, pump = r.number(), r.number()
                if pump not in (0, 1):
                    raise ProtocolError("Invalid pump association")
                module, index = kind & 0xF0, (kind & 0x0F) - 1
                if kind and (module not in (0x10, 0x20, 0x40, 0x50, 0x60) or index < 0):
                    raise ProtocolError(f"Unsupported output type {kind:02X}")
                output = {
                    "id": index,
                    "slot_id": slot,
                    "tipo": f"{kind:02X}",
                    "bomba": pump,
                    "duracion": r.number(2),
                    "estado": r.number(),
                    "label": r.label(),
                }
                # Disabled outputs have no module-specific extension (local h.js).
                if output["estado"] != STATE_DISABLED:
                    if module == 0x20:
                        raw = r.take(3)
                        output.update(
                            modo=raw[0],
                            nivel=raw[1],
                            tipo_flotante=raw[2],
                            config_raw=raw.hex(),
                        )
                    elif module == 0x60:
                        output["ciclon"] = {
                            "modo_ante_tareas": r.number(),
                            "modo_fin_de_tareas": r.number(),
                            "modo_tiempo": r.number(),
                            "d_ante_tareas_s": r.number(2),
                            "d_fin_de_tareas_s": r.number(),
                            "bomba_marcha_duracion_min": r.number(2),
                            "proximo_ciclon": r.number(2),
                        }
                state["outputs"][slot] = output
        elif marker == "H":
            index = r.number()
            schedule: dict[str, Any] = {
                "id": index,
                "estado": r.number(),
                "zona_activa": r.number(),
                "modo": r.number(),
                "duracion": r.number(4),
                "accesorios": r.number(),
                "habilitado": r.number(),
            }
            hour, minute = r.number(), r.number()

            def bcd(value: int) -> int:
                return (value >> 4) * 10 + (value & 15)

            schedule.update(
                hora=f"{bcd(hour):02d}:{bcd(minute):02d}",
                paridad=r.number(),
                dias=r.number(),
                zonas_min=[r.number(2) for _ in range(count)],
                label=r.label(),
            )
            state["schedules"][index] = schedule
        elif marker == "R":
            mode, active, rain = r.number(), r.number(), r.number()
            state["irrigation"] = {
                "modo": mode,
                "active_schedule": active,
                "rain": rain,
            }
            state["auto_riego"] = mode != 0
        elif marker == "L":
            state["tasks"].append(_task(r))
        elif marker == "b":
            state["battery"] = r.number(2)
        elif marker == "u":
            state["uart_id"] = r.number()
            state["uart_resets"] = r.number(2)
        elif marker == "c":
            state["esp_resets"] = r.number(2)
        else:
            raise ProtocolError(
                f"Unknown configuration section {marker!r} at {r.offset - 1}"
            )
    if "S" not in seen:
        raise ProtocolError("Missing output section")
    _groups(state)
    return state


def decode_running(data: bytes, previous: dict[str, Any]) -> dict[str, Any]:
    """Apply D updates to a copy, preserving mode absent from running R records."""
    r = Reader(data)
    if r.take(1) != b"D":
        raise ProtocolError("Expected running update")
    model, version, counter = r.number(), r.number(2), r.number(2)
    if (
        model != (ord("M") if previous.get("is_mini") else ord("N"))
        or version != previous.get("pic_version")
        or counter != previous.get("config_counter")
    ):
        raise ProtocolError("Running update needs a matching configuration")
    state = deepcopy(previous)
    state["tasks"] = []
    by_type = {
        int(o["tipo"], 16): o for o in state["outputs"].values() if int(o["tipo"], 16)
    }
    while r.offset < len(data):
        marker = chr(r.number())
        if marker == "B":
            state.setdefault("pump", {}).update(
                estado=r.number(), pausa_externa=r.number(), marcha_min=r.number(4)
            )
        elif marker == "E":
            index, value = r.number(), r.number()
            if index not in state.get("inputs", {}):
                raise ProtocolError("Unknown input")
            state["inputs"][index]["estado"] = value
        elif marker == "S":
            kind, value = r.number(), r.number()
            if kind not in by_type:
                raise ProtocolError("Unknown output")
            output = by_type[kind]
            output["estado"] = value
            if kind & 0xF0 == 0x20:
                output["nivel"] = r.number()
            elif kind & 0xF0 == 0x60 and output.get("ciclon", {}).get("modo_tiempo"):
                output["ciclon"]["proximo_ciclon"] = r.number(2)
        elif marker == "H":
            index = r.number()
            if index not in state.get("schedules", {}):
                raise ProtocolError("Unknown schedule")
            state["schedules"][index].update(
                estado=r.number(),
                zona_activa=r.number(),
                modo=r.number(),
                duracion=r.number(4),
            )
        elif marker == "R":
            state.setdefault("irrigation", {}).update(
                active_schedule=r.number(), rain=r.number()
            )
        elif marker == "L":
            state["tasks"].append(_task(r))
        elif marker == "k":
            state.setdefault("button", {})["count"] = r.number(2)
        elif marker == "b":
            state["battery"] = r.number(2)
        elif marker == "u":
            state["uart_id"] = r.number()
            state["uart_resets"] = r.number(2)
        else:
            raise ProtocolError(f"Unknown running section {marker!r} at {r.offset - 1}")
    _groups(state)
    return state
