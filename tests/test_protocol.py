"""Regression coverage for record boundaries and controller command semantics."""

import asyncio
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyhidromotic import HidromoticClient
from pyhidromotic.protocol import ProtocolError, decode_config, decode_running


@pytest.fixture
def frame() -> bytes:
    return bytes.fromhex(
        (Path(__file__).parent / "fixtures/config_v704.hex").read_text()
    )


def empty() -> bytes:
    return bytes([0, 0, 0, 0, 7, 0])


def output(kind: int, label: str = "", state: int = 0, extra: bytes = b"") -> bytes:
    name = label.encode()
    return bytes([kind, 1, 15, 0, state, len(name)]) + name + extra


def config(records: list[bytes], tail: bytes = b"") -> bytes:
    return (
        b"CN"
        + bytes(14)
        + b"S"
        + b"".join(records + [empty()] * (12 - len(records)))
        + tail
    )


def running(frame: bytes, body: bytes) -> bytes:
    return b"D" + frame[1:6] + body


def test_mixed_outputs_and_marker_labels() -> None:
    cyclone = bytes([1, 0, 1, 20, 0, 5, 30, 0, 40, 0])
    records = [
        output(0x11, "BRS"),
        output(0x51, "pool"),
        output(0x61, "cyclone", extra=cyclone),
        output(0x21, "named tank", extra=bytes([1, 4, 2])),
        output(0x41, "zone"),
    ]
    state = decode_config(config(records, b"R\x02\x00\x01"))
    assert state["zones"][0]["slot_id"] == 4
    assert state["tanks"][0]["label"] == "named tank"
    assert state["tanks"][0]["nivel"] == 4
    assert state["cyclones"][0]["ciclon"]["proximo_ciclon"] == 40
    assert state["auto_riego"] is True
    assert state["irrigation"]["rain"] == 1


def test_disabled_tank_has_no_extension() -> None:
    state = decode_config(config([output(0x21, "disabled", state=7), output(0x41)]))
    assert not state["tanks"]
    assert state["zones"][0]["slot_id"] == 1
    assert state["outputs"][0]["estado"] == 7


def test_mini_has_six_slots() -> None:
    frame = b"CM" + bytes(14) + b"S" + empty() * 5 + output(0x41)
    assert decode_config(frame)["zones"][0]["slot_id"] == 5


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"C",
        b"CM",
        b"CN" + bytes(14),
        config([output(0x71)]),
        config([output(0x41)]) + b"?",
        config([output(0x21, extra=b"\x01")]),
    ],
)
def test_invalid_config(payload: bytes) -> None:
    with pytest.raises(ProtocolError):
        decode_config(payload)


def test_running_boundaries_and_rain_not_mode(frame: bytes) -> None:
    initial = decode_config(frame)
    initial["auto_riego"] = True
    initial["irrigation"]["modo"] = 1
    snapshot = deepcopy(initial)
    body = (
        b"B\x0b\x02"
        + (123).to_bytes(4, "little")
        + b"S\x21\x01\x04S\x45\x00R\x01\x01"
        + b"H\x00\x01\x05\x00"
        + (18).to_bytes(4, "little")
        + b"E\x00\x01L\x00\x45\x00\x0f\x00"
        + b"L\x01\x42\x01"
    )
    result = decode_running(running(frame, body), initial)
    assert result["pump"]["marcha_min"] == 123
    assert result["pump"]["pausa_externa"] == 2
    assert result["tanks"][0]["nivel"] == 4
    assert result["zones"][4]["estado"] == 0
    assert result["auto_riego"] is True
    assert result["irrigation"]["rain"] == 1
    assert result["schedules"][0]["duracion"] == 18
    assert result["inputs"][0]["estado"] == 1
    assert len(result["tasks"]) == 2
    assert initial == snapshot


def test_cyclone_running_extension() -> None:
    frame = config(
        [output(0x61, extra=bytes([1, 0, 1, 20, 0, 5, 30, 0, 40, 0])), output(0x41)]
    )
    result = decode_running(
        running(frame, b"S\x61\x01\x1e\x00S\x41\x01"), decode_config(frame)
    )
    assert result["cyclones"][0]["ciclon"]["proximo_ciclon"] == 30
    assert result["zones"][0]["estado"] == 1


@pytest.mark.parametrize(
    "body",
    [
        b"B",
        b"B\x01\x00",
        b"S\x21\x01",
        b"R\x00",
        b"H\x00\x01",
        b"E\x04\x01",
        b"S\x11\x01",
        b"?",
        b"L\x00",
    ],
)
async def test_bad_updates_are_atomic(frame: bytes, body: bytes) -> None:
    client = HidromoticClient("unused")
    await client._process_binary(frame)
    original = deepcopy(client.data)
    callback = MagicMock()
    client.register_callback(callback)
    await client._process_binary(running(frame, b"S\x45\x00" + body))
    assert client.data == original
    callback.assert_not_called()


async def test_bad_config_preserves_last_state(frame: bytes) -> None:
    client = HidromoticClient("unused")
    await client._process_binary(frame)
    original = deepcopy(client.data)
    for payload in [b"CM", frame[:-1], config([output(0x71)])]:
        await client._process_binary(payload)
        assert client.data == original


async def test_update_before_config_and_changed_counter_requests_refresh(
    frame: bytes,
) -> None:
    client = HidromoticClient("unused")
    client._ws = MagicMock(closed=False)
    client._ws.send_str = AsyncMock()
    await client._process_binary(running(frame, b"B" + bytes(6)))
    assert client.data == {}
    client._ws.send_str.assert_awaited_once()
    await client._process_binary(frame)
    old = deepcopy(client.data)
    update = bytearray(running(frame, b"S\x45\x00"))
    update[4] ^= 1
    await client._process_binary(bytes(update))
    assert client.data == old
    assert client._ws.send_str.await_count == 2


async def test_disconnected_and_failed_send_do_not_report_success() -> None:
    client = HidromoticClient("unused")
    client.data["auto_riego"] = False
    callback = MagicMock()
    client.register_callback(callback)
    with pytest.raises(ConnectionError):
        await client.set_auto_riego(True)
    client._ws = MagicMock(closed=False)
    client._ws.send_str = AsyncMock(side_effect=ConnectionError("lost"))
    with pytest.raises(ConnectionError):
        await client.set_auto_riego(True)
    assert client.data["auto_riego"] is False
    callback.assert_not_called()


async def test_nonpersistent_mode_and_confirmation(frame: bytes) -> None:
    client = HidromoticClient("unused")
    await client._process_binary(frame)
    client._ws = MagicMock(closed=False)
    client._ws.send_str = AsyncMock()
    callback = MagicMock()
    client.register_callback(callback)
    await client.set_auto_riego(True, persist=False)
    assert client.is_auto_riego_on() is False
    assert "#@RA1;" in client._ws.send_str.call_args_list[0].args[0]
    assert "#@+;" not in client._ws.send_str.call_args_list[0].args[0]
    callback.assert_not_called()
    # Change the mode field in the structurally located R record.
    changed = frame[:344] + b"R\x01" + frame[346:]
    await client._process_binary(changed)
    assert client.is_auto_riego_on() is True
    callback.assert_called_once()


async def test_tank_guard_and_v114_commands() -> None:
    client = HidromoticClient("unused")
    client._ws = MagicMock(closed=False)
    client._ws.send_str = AsyncMock()
    frame = config([output(0x21, extra=b"\x01\x00\x01")])
    await client._process_binary(frame)
    with pytest.raises(ValueError, match="full"):
        await client.set_tank_state(0, True)
    client.data["pic_version"] = 114
    client.data["tanks"][0]["nivel"] = 1
    await client.set_tank_state(0, True)
    assert "#@T0M1;" in client._ws.send_str.call_args.args[0]
    await client.set_tank_state(0, False)
    assert "#@S0M0;" in client._ws.send_str.call_args.args[0]


async def test_additional_controls(frame: bytes) -> None:
    client = HidromoticClient("unused")
    await client._process_binary(frame)
    client._ws = MagicMock(closed=False)
    client._ws.send_str = AsyncMock()
    await client.stop_pump()
    await client.set_schedule_state(10, True)
    assert "#@B0;" in client._ws.send_str.call_args_list[0].args[0]
    assert "#@HAM1;" in client._ws.send_str.call_args_list[1].args[0]
    with pytest.raises(ValueError):
        await client.set_schedule_state(9, True)
    with pytest.raises(ValueError):
        await client.set_output_state(0, True)
    with pytest.raises(ValueError, match="full"):
        await client.set_output_state(3, True)


async def test_callback_can_unregister_without_skipping_next(frame: bytes) -> None:
    client = HidromoticClient("unused")
    first = MagicMock(side_effect=lambda _: unregister())
    unregister = client.register_callback(first)
    second = MagicMock()
    client.register_callback(second)
    await client._process_binary(frame)
    first.assert_called_once()
    second.assert_called_once()
    unregister()


async def test_connect_failure_cleans_owned_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    session.ws_connect = AsyncMock(side_effect=ConnectionError("offline"))
    session.close = AsyncMock()
    monkeypatch.setattr("pyhidromotic.client.aiohttp.ClientSession", lambda: session)
    client = HidromoticClient("unused")
    assert await client.connect() is False
    session.close.assert_awaited_once()
    assert client._session is None
    assert client._reconnect_task is not None
    await client.disconnect()


async def test_explicit_reconnect_restores_retry_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    ws = MagicMock(closed=False)
    ws.send_str = AsyncMock()
    ws.close = AsyncMock()
    ws.__aiter__.return_value = []
    session.ws_connect = AsyncMock(return_value=ws)
    session.close = AsyncMock()
    client = HidromoticClient("unused", session=session)
    await client.disconnect()
    # No one-second initialization delay in this lifecycle test.
    real_sleep = asyncio.sleep

    async def fast_sleep(_: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr("pyhidromotic.client.asyncio.sleep", fast_sleep)
    assert await client.connect()
    assert client._should_reconnect
    await client.disconnect()
    session.close.assert_not_awaited()
    assert not client.connected


async def test_listener_survives_bad_frame(frame: bytes) -> None:
    """Malformed data must not prevent the next valid update from arriving."""
    import aiohttp

    client = HidromoticClient("unused")
    client._should_reconnect = False
    ws = MagicMock(closed=False)
    ws.close = AsyncMock()
    ws.__aiter__.return_value = [
        MagicMock(type=aiohttp.WSMsgType.BINARY, data=b"CM"),
        MagicMock(type=aiohttp.WSMsgType.BINARY, data=frame),
    ]
    client._ws = ws
    await client._listen()
    assert len(client.get_zones()) == 4
    assert client._ws is None
    ws.close.assert_awaited_once()


async def test_retry_failures_do_not_create_duplicate_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = HidromoticClient("unused")
    client.RECONNECT_INTERVAL = 0
    attempts = 0

    async def connect() -> bool:
        nonlocal attempts
        attempts += 1
        client._schedule_reconnect()
        if attempts == 3:
            client._connected = True
        return client.connected

    monkeypatch.setattr(client, "connect", connect)
    client._schedule_reconnect()
    task = client._reconnect_task
    assert task is not None
    await asyncio.wait_for(task, 1)
    assert attempts == 3
    assert client._reconnect_task is task
    await client.disconnect()


async def test_concurrent_connect_and_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connect finishing during disconnect must not leave a live listener."""
    entered, resume = asyncio.Event(), asyncio.Event()
    ws = MagicMock(closed=False)
    ws.close = AsyncMock()
    ws.send_str = AsyncMock()
    ws.__aiter__.return_value = []

    async def ws_connect(*_args: object, **_kwargs: object) -> MagicMock:
        entered.set()
        await resume.wait()
        return ws

    session = MagicMock()
    session.ws_connect = ws_connect

    async def no_delay(_delay: float) -> None:
        return None

    monkeypatch.setattr("pyhidromotic.client.asyncio.sleep", no_delay)
    client = HidromoticClient("unused", session=session)
    connecting = asyncio.create_task(client.connect())
    await entered.wait()
    disconnecting = asyncio.create_task(client.disconnect())
    resume.set()
    await asyncio.gather(connecting, disconnecting)
    assert not client.connected
    assert client._listener_task is None
    assert client._reconnect_task is None
    assert not client._should_reconnect


async def test_failed_initial_send_cleans_up() -> None:
    ws = MagicMock(closed=False)
    ws.send_str = AsyncMock(side_effect=ConnectionError("lost"))
    ws.close = AsyncMock()
    session = MagicMock()
    session.ws_connect = AsyncMock(return_value=ws)
    session.close = AsyncMock()
    client = HidromoticClient("unused", session=session)
    assert not await client.connect()
    ws.close.assert_awaited_once()
    session.close.assert_not_awaited()
    await client.disconnect()


async def test_generic_hose_pool_and_cyclone_controls() -> None:
    client = HidromoticClient("unused")
    client._ws = MagicMock(closed=False)
    client._ws.send_str = AsyncMock()
    frame = config([output(0x11), output(0x51), output(0x61, extra=bytes(10))])
    await client._process_binary(frame)
    await client.set_output_state(0, True)
    await client.set_output_state(1, True)
    await client.set_output_state(2, False)
    assert "#@S0M1;" in client._ws.send_str.call_args_list[0].args[0]
    assert "#@S1M1;" in client._ws.send_str.call_args_list[1].args[0]
    assert "#@S2M0;" in client._ws.send_str.call_args_list[2].args[0]
    with pytest.raises(ValueError, match="stopped"):
        await client.set_output_state(2, True)
    client.data["pump"]["pausa_externa"] = 1
    with pytest.raises(ValueError, match="pause"):
        await client.set_output_state(0, True)


async def test_json_handshake_not_logged_and_pic_reset_refreshes(
    frame: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    client = HidromoticClient("unused")
    client._ws = MagicMock(closed=False)
    client._ws.send_str = AsyncMock()
    await client._process_binary(frame)
    import logging

    with caplog.at_level(logging.DEBUG):
        await client._process_text('{"result":{"wifi":{"pass":"secret-for-test"}}}')
    assert "secret-for-test" not in caplog.text
    await client._process_text('{"result":"chi pic reset"}')
    assert client.data == {}
    assert "#@C;" in client._ws.send_str.call_args.args[0]
