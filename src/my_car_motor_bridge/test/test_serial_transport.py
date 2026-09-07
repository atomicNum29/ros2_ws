from unittest.mock import Mock

import pytest
from serial import SerialException, SerialTimeoutException

from my_car_motor_bridge.serial_transport import SerialTransport


@pytest.mark.parametrize('outcome', [2, SerialTimeoutException('timeout'), SerialException('gone')])
def test_failed_write_closes_transport(outcome):
    transport = SerialTransport('/unused', 115200, 0, 0.05)
    port = Mock(is_open=True)
    if isinstance(outcome, Exception):
        port.write.side_effect = outcome
    else:
        port.write.return_value = outcome
    transport._serial = port
    assert not transport.write(b'123')
    assert not transport.is_open()
    port.close.assert_called_once()


def test_complete_write_and_failed_read():
    transport = SerialTransport('/unused', 115200, 0, 0.05)
    port = Mock(is_open=True)
    transport._serial = port
    port.write.return_value = 3
    assert transport.write(b'123')
    port.read.side_effect = SerialException('unplugged')
    assert transport.read() == b''
    assert not transport.is_open()
