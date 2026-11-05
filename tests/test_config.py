"""Configuration regressions using a binary-only capture and synthetic records."""

from pathlib import Path

import pytest

from pyhidromotic import HidromoticClient


async def test_captured_configuration() -> None:
    """Keep physical routing and unknown tank fields faithful to the capture."""
    frame = bytes.fromhex(
        (Path(__file__).parent / "fixtures" / "config_v704.hex").read_text()
    )
    client = HidromoticClient("unused")
    await client._process_binary(frame)

    assert client.data["pic_version"] == 704
    assert {
        key: (value["slot_id"], value["label"], value["duracion"])
        for key, value in client.get_zones().items()
    } == {
        1: (1, "Cancha de Futbol", 15),
        4: (4, "Manguera Verde", 480),
        5: (5, "Grama", 15),
        11: (7, "Pileta", 360),
    }
    assert set(client.data["outputs"]) == set(range(12))
    tank = client.get_tanks()[0]
    assert tank["nivel"] == 0
    assert tank["modo"] == 1
    assert tank["config_raw"] == "010001"
    assert client.get_tank_level(0) == "full"
    assert client.is_auto_riego_on() is False


@pytest.mark.parametrize("state", [0, 7, 10, 11, 49])
async def test_final_unnamed_zone(state: int) -> None:
    """A final six-byte record can carry faults and transition states."""
    frame = (
        b"CN"
        + bytes(14)
        + b"S"
        + bytes([0, 0, 0, 0, 7, 0]) * 11
        + bytes([0x41, 1, 15, 0, state, 0])
    )
    client = HidromoticClient("unused")
    await client._process_binary(frame)
    if state == 7:
        assert not client.get_zones()
    else:
        assert client.get_zones()[0]["estado"] == state
        assert client.data["outputs"][11]["slot_id"] == 11


async def test_tank_mode_and_level_from_fields() -> None:
    """Decode the three tank fields following the length-prefixed label."""
    client = HidromoticClient("unused")
    client.data["auto_riego"] = True
    frame = (
        b"CN"
        + bytes(14)
        + b"S"
        + bytes([0x21, 1, 10, 0, 0, 0, 1, 2, 3])
        + bytes([0, 0, 0, 0, 7, 0]) * 11
    )
    await client._process_binary(frame)
    assert client.get_tanks()[0]["config_raw"] == "010203"
    assert client.get_tanks()[0]["modo"] == 1
    assert client.get_tanks()[0]["nivel"] == 2
    assert client.is_auto_riego_on() is None
