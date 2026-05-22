"""Small pyserial wrapper used by the ROS node."""

from __future__ import annotations

from typing import Optional

import serial
from serial import SerialException


class SerialTransport:
    def __init__(
        self,
        port: str,
        baudrate: int,
        read_timeout: float,
        write_timeout: float,
    ) -> None:
        self._port = port
        self._baudrate = baudrate
        self._read_timeout = read_timeout
        self._write_timeout = write_timeout
        self._serial: Optional[serial.Serial] = None

    def open(self) -> bool:
        self.close()
        try:
            self._serial = serial.Serial(
                port=self._port,
                baudrate=self._baudrate,
                timeout=self._read_timeout,
                write_timeout=self._write_timeout,
            )
            return bool(self._serial.is_open)
        except SerialException:
            self._serial = None
            return False

    def close(self) -> None:
        if self._serial is None:
            return
        try:
            if self._serial.is_open:
                self._serial.close()
        except SerialException:
            pass
        finally:
            self._serial = None

    def is_open(self) -> bool:
        return bool(self._serial is not None and self._serial.is_open)

    def write(self, data: bytes) -> bool:
        if not self.is_open() or self._serial is None:
            return False
        try:
            self._serial.write(data)
            return True
        except SerialException:
            self.close()
            return False

    def read(self, max_bytes: int = 64) -> bytes:
        if not self.is_open() or self._serial is None:
            return b""
        try:
            return bytes(self._serial.read(max_bytes))
        except SerialException:
            self.close()
            return b""
