import struct

import pytest

from my_car_motor_bridge.protocol import (
    CanRequest, CanResponse, MotorCommand, MotorStatus, SystemStatusV2,
    SYSTEM_STATUS_V2_FIXED, WHEEL_TELEMETRY, try_parse_packet, try_parse_status,
)
from my_car_motor_bridge.command_converter import convert_cmd_vel

# Independent golden fixture from Wheel-Dragoon commit 3327abd README.
V2 = bytes.fromhex(
    'AA 55 3B 82 02 10 01 00 00 3F C0 5D F8 5C A4 38 00 00 '
    '00 64 00 40 E2 01 00 CC 29 00 00 '
    '00 9C FF C0 1D FE FF 84 35 00 00 '
    '00 B0 FF 0F 04 F6 FF 14 37 00 00 '
    '00 50 00 F1 FB 09 00 0C 2B 00 00 F9'
)


def frame(payload):
    data = b'\xaa\x55' + bytes([len(payload)]) + payload
    check = 0
    for byte in data:
        check ^= byte
    return data + bytes([check])


LEGACY = frame(struct.pack('<BBBHH', 0x81, 255, 2, 4, 24000))


def parse_all(buffer):
    packets = []
    while (packet := try_parse_packet(buffer)) is not None:
        packets.append(packet)
    return packets


def test_readme_golden_and_layout():
    assert len(V2) == 63
    assert SYSTEM_STATUS_V2_FIXED.size == 15
    assert WHEEL_TELEMETRY.size == 11
    status = try_parse_packet(bytearray(V2))
    assert isinstance(status, SystemStatusV2)
    assert (status.version, status.seq, status.state, status.system_error) == (2, 16, 1, 0)
    assert (status.driver_a_voltage_mv, status.driver_b_voltage_mv) == (24000, 23800)
    assert [(w.status, w.rpm, w.encoder_count, w.rx_timestamp_us)
            for w in (status.lf, status.rf, status.lr, status.rr)] == [
        (0, 100, 123456, 10700), (0, -100, -123456, 13700),
        (0, -80, -654321, 14100), (0, 80, 654321, 11020),
    ]
    assert status.snapshot_timestamp_us == 14500
    assert all(w['valid'] for w in status.to_dict()['wheels'].values())


@pytest.mark.parametrize('split', range(64))
def test_every_split(split):
    buffer = bytearray(V2[:split])
    output = parse_all(buffer)
    buffer.extend(V2[split:])
    output.extend(parse_all(buffer))
    assert output == [try_parse_packet(bytearray(V2))]
    assert not buffer


def test_bytewise_and_mixed_stream():
    buffer = bytearray()
    packets = []
    for byte in b'noise\xaa' + LEGACY + V2 + V2 + LEGACY:
        buffer.append(byte)
        packets.extend(parse_all(buffer))
    assert [type(p) for p in packets] == [MotorStatus, SystemStatusV2, SystemStatusV2, MotorStatus]
    assert packets[-1].to_dict() == {'seq': 255, 'state': 2, 'error': 4, 'battery_mv': 24000}
    assert not buffer


@pytest.mark.parametrize('bad', [
    b'\xaa\x55\xff\x82', b'\xaa\x55\x27\x99', b'\xaa\x55\x00\x81',
    V2[:-1] + b'\x00', b'\x00' * 10000,
])
def test_resync(bad):
    assert len(parse_all(bytearray(bad + V2))) == 1


def test_corrupt_candidate_rescans_embedded_next_header():
    assert len(parse_all(bytearray(V2[:8] + V2))) == 1


def test_trailing_header_preserved_and_noise_bounded():
    buffer = bytearray(b'noise' * 10000 + b'\xaa')
    assert try_parse_packet(buffer) is None
    assert buffer == b'\xaa'
    buffer.extend(V2[1:])
    assert isinstance(try_parse_packet(buffer), SystemStatusV2)
    assert not buffer


def test_payload_header_is_data_and_integer_boundaries():
    payload = bytearray(V2[3:-1])
    struct.pack_into('<BhiI', payload, 15, 0xAA, -32768, -2147483648, 0x55AA)
    struct.pack_into('<BhiI', payload, 26, 0x55, 32767, 2147483647, 0xFFFFFFFF)
    status = try_parse_packet(bytearray(frame(payload)))
    assert (status.lf.status, status.lf.rpm, status.lf.encoder_count) == (170, -32768, -2147483648)
    assert (status.rf.rpm, status.rf.encoder_count, status.rf.rx_timestamp_us) == (32767, 2147483647, 0xFFFFFFFF)


@pytest.mark.parametrize('wheel,offset', [('lf', 15), ('rf', 26), ('lr', 37), ('rr', 48)])
@pytest.mark.parametrize('wire,expected', [
    ('00 00 00 80', -2147483648), ('0C FE FF FF', -500),
    ('FF FF FF FF', -1), ('00 00 00 00', 0), ('FF FF FF 7F', 2147483647),
])
def test_encoder_count_and_raw_timestamps_preserved_in_json(wheel, offset, wire, expected):
    payload = bytearray(V2[3:-1])
    payload[11:15] = bytes.fromhex('FF FF FF FF')
    payload[offset + 3:offset + 7] = bytes.fromhex(wire)
    payload[offset + 7:offset + 11] = bytes.fromhex('00 00 00 00')
    status = try_parse_packet(bytearray(frame(payload)))
    result = status.to_dict()
    assert result['snapshot_timestamp_us'] == 0xFFFFFFFF
    assert result['wheels'][wheel]['encoder_count'] == expected
    assert result['wheels'][wheel]['rx_timestamp_us'] == 0
    assert result['wheels'][wheel]['valid'] is True
    assert 'controller_output' not in result['wheels'][wheel]
    assert 'current_deci_amp' not in result['wheels'][wheel]


@pytest.mark.parametrize('version', [1, 2])
def test_old_layout_is_rejected_even_with_new_version(version):
    payload = bytes([0x82, version]) + bytes(37)
    assert try_parse_packet(bytearray(frame(payload))) is None
    assert isinstance(try_parse_packet(bytearray(frame(payload) + V2)), SystemStatusV2)


@pytest.mark.parametrize('bit', range(6))
def test_individual_validity_and_raw_error_semantics(bit):
    payload = bytearray(V2[3:-1])
    payload[6] = 1 << bit
    # Diagnostic CAN TX error and reserved bit are preserved, never relabelled.
    payload[4:6] = b'\x04\x10'
    status = try_parse_packet(bytearray(frame(payload)))
    result = status.to_dict()
    assert result['state'] == 1
    assert result['system_error'] == 0x1004
    validity = [result['wheels'][w]['valid'] for w in ('lf', 'rf', 'lr', 'rr')]
    validity += [result['drivers'][d]['valid'] for d in ('a', 'b')]
    assert validity == [i == bit for i in range(6)]
    assert result['wheels']['lf']['rpm'] == 100
    assert 'battery_mv' not in result and 'error' not in result


@pytest.mark.parametrize('version', [1, 3])
def test_unsupported_version_consumed_without_hiding_next_frame(version):
    payload = bytearray(V2[3:-1])
    payload[1] = version
    errors = []
    buffer = bytearray(frame(payload) + LEGACY)
    assert isinstance(try_parse_packet(buffer, errors.append), MotorStatus)
    assert errors == [f'Unsupported System Status version {version}']
    assert not buffer


def test_command_wire_unchanged():
    assert convert_cmd_vel(0.1, -0.5) == (100, -500)
    command = MotorCommand(255, 100, -500, True, True).pack()
    assert command == frame(bytes.fromhex('01 FF 64 00 0C FE 03'))
    assert MotorCommand(256, 40000, -40000, False).pack()[4:10] == bytes.fromhex('00 FF 7F 00 80 00')
    assert isinstance(try_parse_packet(bytearray(command + V2)), SystemStatusV2)


@pytest.mark.parametrize('can_id,dlc', [(0, 0), (0x7FF, 8), (1, 2)])
def test_can_request_wire(can_id, dlc):
    request = CanRequest.from_dict({'can_id': can_id, 'dlc': dlc, 'data': list(range(dlc))}, 255)
    expected = frame(struct.pack('<BBHB8sH', 0x20, 255, can_id, dlc, bytes(range(dlc)), 100))
    assert request.pack() == expected
    assert len(expected) == 19


@pytest.mark.parametrize('change', [
    {'can_id': -1}, {'can_id': 2048}, {'can_id': True}, {'can_id': 1.0},
    {'dlc': 9}, {'dlc': -1}, {'dlc': 1}, {'data': [256, 0]},
    {'data': [-1, 0]}, {'data': [False, 0]}, {'data': '00'},
    {'timeout_ms': 101}, {'timeout_ms': -1}, {'timeout_ms': 1.5}, {'seq': 3},
])
def test_can_reject_invalid_json_fields(change):
    with pytest.raises(ValueError):
        CanRequest.from_dict({'can_id': 1, 'dlc': 2, 'data': [4, 143], **change}, 0)


@pytest.mark.parametrize('value', [None, [], 3, {}, {'can_id': 1}])
def test_can_reject_non_request(value):
    with pytest.raises(ValueError):
        CanRequest.from_dict(value, 0)


def test_tunnel_all_sequences_ids_statuses_and_status_wrapper():
    frames = [frame(struct.pack('<BBBHB8s', 0xA0, 9, s, cid, dlc, data))
              for s, cid, dlc, data in [
                  (0, 0x701, 3, b'\x8f\xf0\x00'), (2, 0, 0, b''),
                  (0, 0x456, 1, b'\xc8'), (1, 0, 0, b''), (3, 0, 0, b''),
              ]]
    packets = parse_all(bytearray(b''.join(frames)))
    assert len(packets) == 5 and all(isinstance(p, CanResponse) for p in packets)
    assert [p.seq for p in packets] == [9] * 5
    assert packets[2].to_dict()['data'] == [200, 0, 0, 0, 0, 0, 0, 0]
    assert isinstance(try_parse_status(bytearray(b''.join(frames) + V2)), SystemStatusV2)


@pytest.mark.parametrize('status,can_id,dlc', [(4, 0, 0), (0, 2048, 0), (0, 0, 9)])
def test_invalid_tunnel_recovers(status, can_id, dlc):
    errors = []
    bad = frame(struct.pack('<BBBHB8s', 0xA0, 0, status, can_id, dlc, b''))
    assert isinstance(try_parse_packet(bytearray(bad + V2), errors.append), SystemStatusV2)
    assert len(errors) == 1
