"""WebSocket client for Hidromotic CHI Smart irrigation controllers."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from contextlib import suppress
from typing import Any, cast

import aiohttp

from .const import (
    STATE_DISABLED,
    TANK_EMPTY,
    TANK_FULL,
    TANK_LEVEL_FAIL,
    TANK_MEDIUM,
    TANK_SENSOR_FAIL,
)
from .protocol import ProtocolError, decode_config, decode_running

_LOGGER = logging.getLogger(__name__)


def hex_to_int(h: str) -> int:
    """Convert one hexadecimal digit to an integer."""
    if len(h) != 1:
        raise ValueError("Expected one hexadecimal digit")
    return int(h, 16)


def int_to_hex(i: int) -> str:
    """Encode one hexadecimal protocol index."""
    if not 0 <= i <= 15:
        raise ValueError("Protocol index must be between 0 and 15")
    return f"{i:X}"


class HidromoticClient:
    """WebSocket client for Hidromotic device."""

    RECONNECT_INTERVAL = 30  # seconds

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize the client.

        Args:
            host: The hostname or IP address of the Hidromotic device.
            session: Optional aiohttp ClientSession to use. If not provided,
                     a new session will be created.
        """
        self.host = host
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session = session
        self._owns_session = session is None
        self._callbacks: list[Callable[[dict[str, Any]], None]] = []
        self._connected = False
        self._should_reconnect = True
        self._reconnect_task: asyncio.Task[None] | None = None
        self._listener_task: asyncio.Task[None] | None = None
        self._data: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._connection_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        """Return if connected to device."""
        return self._connected

    @property
    def data(self) -> dict[str, Any]:
        """Return current device data."""
        return self._data

    def register_callback(
        self, callback: Callable[[dict[str, Any]], None]
    ) -> Callable[[], None]:
        """Register a callback for data updates.

        Args:
            callback: Function to call when device data is updated.

        Returns:
            A function that can be called to unregister the callback.
        """
        self._callbacks.append(callback)

        def unregister() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return unregister

    def _notify(self) -> None:
        """Notify a snapshot of subscriptions, allowing removal during callbacks."""
        for callback in tuple(self._callbacks):
            try:
                callback(self._data)
            except Exception:
                _LOGGER.exception("Error in callback")

    def _schedule_reconnect(self) -> None:
        if self._should_reconnect and (
            self._reconnect_task is None or self._reconnect_task.done()
        ):
            self._reconnect_task = asyncio.create_task(self._reconnect())

    async def connect(self) -> bool:
        """Connect and request configuration; reported data arrives asynchronously."""
        async with self._connection_lock:
            if self._connected:
                return True
            self._should_reconnect = True
            try:
                if self._session is None:
                    self._session = aiohttp.ClientSession()
                    self._owns_session = True
                self._ws = await self._session.ws_connect(
                    f"ws://{self.host}/rpc", heartbeat=30
                )
                await self._send_command("e")
                await asyncio.sleep(1)
                await self._send_command("#@C;")
                self._connected = True
                self._listener_task = asyncio.create_task(self._listen())
                return True
            except (Exception, asyncio.CancelledError) as err:
                if self._ws:
                    await self._ws.close()
                    self._ws = None
                if self._session and self._owns_session:
                    await self._session.close()
                    self._session = None
                self._connected = False
                if isinstance(err, asyncio.CancelledError):
                    raise
                _LOGGER.error("Failed to connect to Hidromotic: %s", err)
                self._schedule_reconnect()
                return False

    async def disconnect(self) -> None:
        """Stop retries and release resources, preserving externally owned sessions."""
        self._should_reconnect = False
        retry = self._reconnect_task
        if retry and retry is not asyncio.current_task():
            retry.cancel()
            with suppress(asyncio.CancelledError):
                await retry
        async with self._connection_lock:
            # A concurrent connect may have finished while we acquired this lock.
            self._should_reconnect = False
            self._connected = False
            listener = self._listener_task
            if listener and listener is not asyncio.current_task():
                listener.cancel()
                with suppress(asyncio.CancelledError):
                    await listener
            self._reconnect_task = self._listener_task = None
            if self._ws:
                await self._ws.close()
                self._ws = None
            if self._session and self._owns_session:
                await self._session.close()
                self._session = None

    async def _send_command(self, command: str) -> None:
        """Send JSON-RPC, raising on disconnection instead of silently succeeding."""
        async with self._lock:
            if not self._ws or self._ws.closed:
                raise ConnectionError("Hidromotic is not connected")
            payload: dict[str, Any] = {"method": "hdmt", "params": {"c": command}}
            if not command.startswith("#"):
                payload["id"] = 1111
            await self._ws.send_str(json.dumps(payload, separators=(",", ":")))

    async def _listen(self) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    await self._process_binary(msg.data)
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    await self._process_text(msg.data)
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Error in WebSocket listener")
        finally:
            if self._ws is ws:
                self._connected = False
                self._ws = None
                await ws.close()
                self._notify()
                self._schedule_reconnect()

    async def _reconnect(self) -> None:
        while self._should_reconnect and not self._connected:
            await asyncio.sleep(self.RECONNECT_INTERVAL)
            if self._should_reconnect and await self.connect():
                break

    async def _process_text(self, data: str) -> None:
        """Process text message (JSON)."""
        try:
            parsed = json.loads(data)
            if isinstance(parsed, dict) and "error" in parsed:
                _LOGGER.warning("Controller returned an RPC error")
            elif isinstance(parsed, dict) and parsed.get("result") == "chi pic reset":
                self._data = {}
                self._notify()
                await self.refresh()
        except json.JSONDecodeError:
            _LOGGER.debug("Ignoring non-JSON text frame")

    async def _process_binary(self, data: bytes) -> None:
        """Reject malformed frames atomically without losing the connection."""
        if not data or data[:1] not in (b"C", b"D"):
            return
        try:
            if data[:1] == b"C":
                await self._parse_full_config(data)
            else:
                await self._parse_running_data(data)
        except ProtocolError as err:
            _LOGGER.warning("Ignoring invalid controller frame: %s", err)
            if data[:1] == b"D" and self._ws and not self._ws.closed:
                await self.refresh()
            return
        self._notify()

    async def _parse_full_config(self, data: bytes) -> None:
        """Publish only a completely validated configuration."""
        self._data = decode_config(data)

    async def _parse_running_data(self, data: bytes) -> None:
        """Apply bounded updates to a copy of the matching configuration."""
        self._data = decode_running(data, self._data)

    async def set_zone_state(self, zone_id: int, on: bool) -> None:
        """Turn a zone on or off.

        Args:
            zone_id: The zone ID to control.
            on: True to turn on, False to turn off.
        """
        zone = self._data.get("zones", {}).get(zone_id)
        if not zone:
            _LOGGER.warning("Zone %d not found", zone_id)
            return

        slot_id = zone["slot_id"]
        state = 1 if on else 0
        command = f"#@S{int_to_hex(slot_id)}M{state};"
        _LOGGER.debug("Setting zone %d (slot %d) to %s", zone_id, slot_id, state)
        await self._send_command(command)

    async def set_tank_state(self, tank_id: int, on: bool) -> None:
        """Turn a tank on or off.

        Args:
            tank_id: The tank ID to control.
            on: True to start filling, False to stop filling.
        """
        tank = self._data.get("tanks", {}).get(tank_id)
        if not tank:
            _LOGGER.warning("Tank %d not found", tank_id)
            return

        if (
            tank.get("nivel") == 0
            and tank.get("modo")
            and on
            and tank.get("estado") in (0, 5)
        ):
            raise ValueError("Automatic tank is already full")
        if on and self._data.get("pic_version") == 114:
            await self._send_command(f"#@T{tank_id}M1;")
        else:
            await self._send_command(f"#@S{int_to_hex(tank['slot_id'])}M{int(on)};")

    async def set_auto_riego(self, on: bool, *, persist: bool = True) -> None:
        """Request automatic mode and refresh; only reported state is confirmed.

        persist saves to controller flash, matching the manufacturer interface.
        Use persist=False for temporary changes without a flash write.
        """
        await self._send_command(f"#@RA{int(on)};" + ("#@+;" if persist else ""))
        await self.refresh()

    async def stop_pump(self) -> None:
        """Stop the pump using the manufacturer's stop command."""
        await self._send_command("#@B0;")

    async def set_schedule_state(self, schedule_id: int, on: bool) -> None:
        """Start or stop a known irrigation schedule."""
        if schedule_id not in self._data.get("schedules", {}):
            raise ValueError("Unknown irrigation schedule")
        await self._send_command(f"#@H{int_to_hex(schedule_id)}M{int(on)};")

    async def set_output_state(self, slot_id: int, on: bool) -> None:
        """Control a known physical output, honoring module-specific rules."""
        output = self._data.get("outputs", {}).get(slot_id)
        if output is None or output["estado"] == STATE_DISABLED:
            raise ValueError("Unknown or disabled output")
        module = int(output["tipo"], 16) & 0xF0
        if module == 0x10 and on and self.get_pump().get("pausa_externa", 0):
            raise ValueError("Hose is blocked by external pause")
        if module == 0x60 and on:
            raise ValueError("Cyclone can only be stopped manually")
        if module == 0x20:
            if (
                on
                and output.get("nivel") == 0
                and output.get("modo")
                and output["estado"] in (0, 5)
            ):
                raise ValueError("Automatic tank is already full")
            if on and self._data.get("pic_version") == 114:
                await self._send_command(f"#@T{output['id']}M1;")
                return
        if module not in (0x10, 0x20, 0x40, 0x50, 0x60):
            raise ValueError("Unsupported output module")
        await self._send_command(f"#@S{int_to_hex(slot_id)}M{int(on)};")

    def get_outputs(self) -> dict[int, dict[str, Any]]:
        """Return all physical outputs, including disabled outputs for diagnostics."""
        return cast(dict[int, dict[str, Any]], self._data.get("outputs", {}))

    def get_schedules(self) -> dict[int, dict[str, Any]]:
        """Return decoded irrigation schedules."""
        return cast(dict[int, dict[str, Any]], self._data.get("schedules", {}))

    def get_tasks(self) -> list[dict[str, Any]]:
        """Return tasks; remaining time is seconds for cyclones, minutes otherwise."""
        return cast(list[dict[str, Any]], self._data.get("tasks", []))

    async def refresh(self) -> None:
        """Request fresh data from device."""
        await self._send_command("#@C;")

    def get_zones(self) -> dict[int, dict[str, Any]]:
        """Get all active zones."""
        return cast(dict[int, dict[str, Any]], self._data.get("zones", {}))

    def get_tanks(self) -> dict[int, dict[str, Any]]:
        """Get all active tanks."""
        return cast(dict[int, dict[str, Any]], self._data.get("tanks", {}))

    def get_pump(self) -> dict[str, Any]:
        """Get pump status."""
        return cast(dict[str, Any], self._data.get("pump", {}))

    def is_auto_riego_on(self) -> bool | None:
        """Return automatic irrigation mode, or None when not decoded."""
        return self._data.get("auto_riego")

    def is_zone_on(self, zone_id: int) -> bool:
        """Check if a zone is on.

        Args:
            zone_id: The zone ID to check.

        Returns:
            True if the zone is on, False otherwise.
        """
        zone = self._data.get("zones", {}).get(zone_id)
        if zone:
            return bool(zone.get("estado", 0) == 1)
        return False

    def is_tank_full(self, tank_id: int) -> bool:
        """Check if a tank is full.

        Args:
            tank_id: The tank ID to check.

        Returns:
            True if the tank is full, False otherwise.
        """
        tank = self._data.get("tanks", {}).get(tank_id)
        if tank:
            return bool(tank.get("nivel") == TANK_FULL)
        return False

    def is_tank_empty(self, tank_id: int) -> bool:
        """Check if a tank is empty.

        Args:
            tank_id: The tank ID to check.

        Returns:
            True if the tank is empty, False otherwise.
        """
        tank = self._data.get("tanks", {}).get(tank_id)
        if tank:
            return bool(tank.get("nivel") == TANK_EMPTY)
        return False

    def get_tank_level(self, tank_id: int) -> str:
        """Get tank level as a string.

        Args:
            tank_id: The tank ID to check.

        Returns:
            One of: "full", "empty", "sensor_fail", "level_fail", "medium", "unknown"
        """
        tank = self._data.get("tanks", {}).get(tank_id)
        if not tank:
            return "unknown"
        nivel = tank.get("nivel", 0xFF)
        if nivel == TANK_FULL:
            return "full"
        if nivel == TANK_EMPTY:
            return "empty"
        if nivel == TANK_SENSOR_FAIL:
            return "sensor_fail"
        if nivel == TANK_LEVEL_FAIL:
            return "level_fail"
        if nivel == TANK_MEDIUM:
            return "medium"
        return "unknown"
