"""Binary packet protocol shared by the ROS motor bridge and MCU."""

from __future__ import annotations

from dataclasses import dataclass
import struct


HEADER = b"\xAA\x55"
TYPE_COMMAND = 0x01
TYPE_STATUS = 0x81

PAYLOAD_LENGTH = 7
PACKET_OVERHEAD = 4  # header(2) + length(1) + checksum(1)
PACKET_LENGTH = PACKET_OVERHEAD + PAYLOAD_LENGTH

COMMAND_MIN = -1000
COMMAND_MAX = 1000

FLAG_ENABLE = 0x01
FLAG_EMERGENCY_STOP = 0x02


def clamp_command(value: int) -> int:
    """Clamp a normalized command to the supported MCU command range."""
    return max(COMMAND_MIN, min(COMMAND_MAX, int(value)))


def xor_checksum(data: bytes) -> int:
    """Return XOR checksum over all bytes in data."""
    checksum = 0
    for byte in data:
        checksum ^= byte
    return checksum & 0xFF


@dataclass(frozen=True)
class MotorCommand:
    seq: int
    v_cmd: int
    w_cmd: int
    enable: bool
    emergency_stop: bool = False

    def pack(self) -> bytes:
        flags = 0
        if self.enable:
            flags |= FLAG_ENABLE
        if self.emergency_stop:
            flags |= FLAG_EMERGENCY_STOP

        payload = struct.pack(
            "<BBhhB",
            TYPE_COMMAND,
            self.seq & 0xFF,
            clamp_command(self.v_cmd),
            clamp_command(self.w_cmd),
            flags,
        )
        packet_without_checksum = HEADER + bytes([len(payload)]) + payload
        return packet_without_checksum + bytes([xor_checksum(packet_without_checksum)])


@dataclass(frozen=True)
class MotorStatus:
    seq: int
    state: int
    error: int
    battery_mv: int


def _drop_until_possible_header(buffer: bytearray) -> None:
    header_index = buffer.find(HEADER)
    if header_index >= 0:
        del buffer[:header_index]
        return

    # Preserve a trailing 0xAA because it may be the first byte of the next header.
    if buffer and buffer[-1] == HEADER[0]:
        del buffer[:-1]
    else:
        buffer.clear()


def try_parse_status(buffer: bytearray) -> MotorStatus | None:
    """Parse one status packet from buffer, resynchronizing on invalid bytes."""
    while True:
        if len(buffer) < len(HEADER):
            return None

        _drop_until_possible_header(buffer)

        if len(buffer) < 3:
            return None

        payload_length = buffer[2]
        if payload_length != PAYLOAD_LENGTH:
            del buffer[0]
            continue

        total_length = PACKET_OVERHEAD + payload_length
        if len(buffer) < total_length:
            return None

        packet = bytes(buffer[:total_length])
        expected_checksum = xor_checksum(packet[:-1])
        received_checksum = packet[-1]
        if expected_checksum != received_checksum:
            del buffer[0]
            continue

        payload = packet[3:-1]
        packet_type = payload[0]
        if packet_type != TYPE_STATUS:
            del buffer[0]
            continue

        _, seq, state, error, battery_mv = struct.unpack("<BBBHH", payload)
        del buffer[:total_length]
        return MotorStatus(
            seq=seq,
            state=state,
            error=error,
            battery_mv=battery_mv,
        )
