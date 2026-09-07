"""Wheel-Dragoon serial framing and typed control/diagnostic packets."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import struct
from typing import Callable

HEADER = b"\xAA\x55"
TYPE_COMMAND = 0x01
TYPE_STATUS = 0x81
TYPE_SYSTEM_STATUS_V2 = 0x82
TYPE_CAN_REQUEST = 0x20
TYPE_CAN_RESPONSE = 0xA0
PAYLOAD_LENGTHS = {
    TYPE_COMMAND: 7, TYPE_STATUS: 7, TYPE_SYSTEM_STATUS_V2: 39,
    TYPE_CAN_REQUEST: 15, TYPE_CAN_RESPONSE: 14,
}
PACKET_OVERHEAD = 4
SYSTEM_STATUS_V2_FIXED = struct.Struct("<BBBBHBHH")
WHEEL_TELEMETRY = struct.Struct("<BhHh")
WHEEL_NAMES = ("lf", "rf", "lr", "rr")
COMMAND_MIN = -(2**15)
COMMAND_MAX = 2**15 - 1
FLAG_ENABLE = 0x01
FLAG_EMERGENCY_STOP = 0x02


def clamp_command(value: int) -> int:
    return max(COMMAND_MIN, min(COMMAND_MAX, int(value)))


def xor_checksum(data: bytes) -> int:
    checksum = 0
    for byte in data:
        checksum ^= byte
    return checksum


def _frame(payload: bytes) -> bytes:
    packet = HEADER + bytes([len(payload)]) + payload
    return packet + bytes([xor_checksum(packet)])


@dataclass(frozen=True)
class MotorCommand:
    seq: int
    v_cmd: int
    w_cmd: int
    enable: bool
    emergency_stop: bool = False

    def pack(self) -> bytes:
        flags = (FLAG_ENABLE if self.enable else 0) | (
            FLAG_EMERGENCY_STOP if self.emergency_stop else 0
        )
        return _frame(struct.pack(
            "<BBhhB", TYPE_COMMAND, self.seq & 0xFF,
            clamp_command(self.v_cmd), clamp_command(self.w_cmd), flags,
        ))


@dataclass(frozen=True)
class MotorStatus:
    seq: int
    state: int
    error: int
    battery_mv: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class WheelTelemetry:
    status: int
    rpm: int
    current_deci_amp: int
    controller_output: int  # signed int16 on the wire


@dataclass(frozen=True)
class SystemStatusV2:
    version: int
    seq: int
    state: int
    system_error: int
    validity: int
    driver_a_voltage_mv: int
    driver_b_voltage_mv: int
    lf: WheelTelemetry
    rf: WheelTelemetry
    lr: WheelTelemetry
    rr: WheelTelemetry

    def to_dict(self) -> dict:
        return {
            "version": self.version, "seq": self.seq, "state": self.state,
            "system_error": self.system_error, "validity": self.validity,
            "drivers": {
                "a": {"voltage_mv": self.driver_a_voltage_mv,
                      "valid": bool(self.validity & 0x10)},
                "b": {"voltage_mv": self.driver_b_voltage_mv,
                      "valid": bool(self.validity & 0x20)},
            },
            "wheels": {
                name: {**asdict(getattr(self, name)),
                       "valid": bool(self.validity & (1 << bit))}
                for bit, name in enumerate(WHEEL_NAMES)
            },
        }


def _integer(value: object, name: str, maximum: int) -> int:
    # JSON booleans/floats are not CAN integers.
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"{name} must be an integer in 0..{maximum}")
    return value


@dataclass(frozen=True)
class CanRequest:
    seq: int
    can_id: int
    dlc: int
    data: tuple[int, ...]
    timeout_ms: int = 100

    @classmethod
    def from_dict(cls, value: object, seq: int) -> CanRequest:
        if not isinstance(value, dict):
            raise ValueError("CAN request must be a JSON object")
        required = {"can_id", "dlc", "data"}
        if not required <= value.keys() or value.keys() - required - {"timeout_ms"}:
            raise ValueError("Expected can_id, dlc, data and optional timeout_ms")
        if not isinstance(value["data"], list):
            raise ValueError("data must be an array of bytes")
        request = cls(seq, value["can_id"], value["dlc"],
                      tuple(value["data"]), value.get("timeout_ms", 100))
        request.validate()
        return request

    def validate(self) -> None:
        _integer(self.seq, "seq", 255)
        _integer(self.can_id, "can_id", 0x7FF)
        _integer(self.dlc, "dlc", 8)
        _integer(self.timeout_ms, "timeout_ms", 100)
        if len(self.data) != self.dlc:
            raise ValueError("data must contain exactly dlc bytes")
        for byte in self.data:
            _integer(byte, "data byte", 255)

    def pack(self) -> bytes:
        self.validate()
        return _frame(struct.pack(
            "<BBHB8sH", TYPE_CAN_REQUEST, self.seq, self.can_id, self.dlc,
            bytes(self.data).ljust(8, b"\0"), self.timeout_ms,
        ))


@dataclass(frozen=True)
class CanResponse:
    seq: int
    status: int
    can_id: int
    dlc: int
    data: bytes

    def to_dict(self) -> dict:
        return {"seq": self.seq, "status": self.status, "can_id": self.can_id,
                "dlc": self.dlc, "data": list(self.data)}


ReceivedPacket = MotorStatus | SystemStatusV2 | CanResponse


def _drop_until_possible_header(buffer: bytearray) -> None:
    index = buffer.find(HEADER)
    if index >= 0:
        del buffer[:index]
    elif buffer and buffer[-1] == HEADER[0]:
        del buffer[:-1]
    else:
        buffer.clear()


def try_parse_packet(
    buffer: bytearray, on_error: Callable[[str], None] | None = None,
) -> ReceivedPacket | None:
    """Consume one received packet, retaining partial frames and resyncing errors.

    Known outbound frames are consumed without decoding. Unsupported versions
    are consumed as complete frames; their payload is never scanned as framing.
    """
    while True:
        _drop_until_possible_header(buffer)
        if len(buffer) < 4:
            return None
        length, packet_type = buffer[2:4]
        if PAYLOAD_LENGTHS.get(packet_type) != length:
            del buffer[0]
            continue
        total = length + PACKET_OVERHEAD
        if len(buffer) < total:
            return None
        packet = bytes(buffer[:total])
        if xor_checksum(packet[:-1]) != packet[-1]:
            del buffer[0]
            continue
        del buffer[:total]
        payload = packet[3:-1]
        if packet_type == TYPE_STATUS:
            return MotorStatus(*struct.unpack("<BBBHH", payload)[1:])
        if packet_type == TYPE_SYSTEM_STATUS_V2:
            if payload[1] != 1:
                if on_error:
                    on_error(f"Unsupported System Status version {payload[1]}")
                continue
            fixed = SYSTEM_STATUS_V2_FIXED.unpack_from(payload)
            wheels = tuple(
                WheelTelemetry(*WHEEL_TELEMETRY.unpack_from(payload, offset))
                for offset in (11, 18, 25, 32)
            )
            return SystemStatusV2(*fixed[1:], *wheels)
        if packet_type == TYPE_CAN_RESPONSE:
            _, seq, status, can_id, dlc, data = struct.unpack("<BBBHB8s", payload)
            if can_id > 0x7FF or dlc > 8 or status > 3:
                if on_error:
                    on_error("Invalid CAN response ID, DLC or status")
                continue
            return CanResponse(seq, status, can_id, dlc, data)


def try_parse_status(buffer: bytearray) -> MotorStatus | SystemStatusV2 | None:
    """Status-only compatibility entry point; consume tunnel frames too."""
    while True:
        packet = try_parse_packet(buffer)
        if packet is None or isinstance(packet, (MotorStatus, SystemStatusV2)):
            return packet
