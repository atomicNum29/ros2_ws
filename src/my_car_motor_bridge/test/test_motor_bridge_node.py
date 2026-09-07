import json
import time
from types import SimpleNamespace

import pytest
import rclpy
from geometry_msgs.msg import Twist
from rclpy.context import Context
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.parameter import Parameter
from std_msgs.msg import String

import my_car_motor_bridge.motor_bridge_node as bridge_module
from test_protocol import LEGACY, V2, frame
import struct


class FakeTransport:
    def __init__(self, **kwargs):
        self.connected = False
        self.incoming = bytearray()
        self.writes = []
        self.open_count = 0
        self.fail_write = False
        self.fail_read = False

    def open(self):
        self.open_count += 1
        self.connected = True
        return True

    def close(self):
        self.connected = False

    def is_open(self):
        return self.connected

    def write(self, data):
        if self.fail_write:
            self.connected = False
            return False
        assert self.connected
        self.writes.append(data)
        return True

    def read(self, size):
        if self.fail_read:
            self.connected = False
            return b''
        data = bytes(self.incoming[:size])
        del self.incoming[:size]
        return data


@pytest.fixture
def nodes(monkeypatch):
    context = Context()
    rclpy.init(context=context)
    monkeypatch.setattr(bridge_module, 'SerialTransport', FakeTransport)
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(bridge_module, 'time', SimpleNamespace(monotonic=lambda: clock.now))
    created = []

    def make(**parameters):
        node = bridge_module.MotorBridgeNode(
            context=context,
            parameter_overrides=[Parameter(key, value=value) for key, value in parameters.items()],
        )
        node.read_timer.cancel()
        if node.send_timer is not None:
            node.send_timer.cancel()
        created.append(node)
        return node

    yield make, clock, context
    for node in created:
        previous_writes = list(node.transport.writes)
        previous_opens = node.transport.open_count
        node.destroy_node()
        assert not node.transport.is_open()
        if node.serial_mode == "bridge":
            assert node.transport.writes == previous_writes
            assert node.transport.open_count == previous_opens
    context.shutdown()


def test_mode_entities_and_control_commands(nodes):
    make, clock, _ = nodes
    node = make()
    assert node.status_pub is not None and node.cmd_sub is not None and node.send_timer is not None
    assert node.can_rx_pub is None and node.can_tx_sub is None
    node._on_send_timer()
    assert node.transport.writes[-1][5:10] == b'\0' * 5
    twist = Twist()
    twist.linear.x = 0.1
    twist.angular.z = -0.5
    node._on_cmd_vel(twist)
    node.seq = 255
    node._on_send_timer()
    assert node.transport.writes[-1][4:10] == bytes.fromhex('FF 64 00 0C FE 01')
    clock.now += 0.31
    node._on_send_timer()
    assert node.transport.writes[-1][4:10] == b'\0' * 6
    assert node.seq == 1
    node.send_disable_zero()
    assert node.transport.writes[-1][9] == 0


def test_timeout_suppression_and_nonfinite_command(nodes):
    make, _, _ = nodes
    node = make(send_zero_when_timeout=False)
    node._on_send_timer()
    assert not node.transport.writes
    twist = Twist()
    twist.linear.x = float('nan')
    node._on_cmd_vel(twist)
    assert node.last_cmd_time is None


def test_status_selection_and_reconnect_resets(nodes, monkeypatch):
    make, clock, _ = nodes
    node = make()
    published = []
    monkeypatch.setattr(node, '_publish_json', lambda pub, data: published.append(data))
    node.transport.incoming.extend(LEGACY)
    node._on_read_timer()
    assert not published
    clock.now += 1.0
    node.transport.incoming.extend(LEGACY + V2 + LEGACY)
    node._on_read_timer()
    assert len(published) == 2
    assert published[0]['battery_mv'] == 24000
    assert published[1]['version'] == 1
    clock.now += 10
    node.transport.incoming.extend(LEGACY)
    node._on_read_timer()
    assert len(published) == 2
    node.transport.incoming.extend(V2[:10])
    node._on_read_timer()
    assert node.rx_buffer
    node._on_cmd_vel(Twist())
    node.transport.connected = False
    node._try_reconnect(force=True)
    assert not node.rx_buffer and not node.status_policy.v2_seen
    assert node.last_twist is None and node.last_cmd_time is None
    clock.now += 1
    node.transport.incoming.extend(LEGACY)
    node._on_read_timer()
    assert len(published) == 3 and 'battery_mv' in published[-1]


def test_bridge_never_sends_control_and_does_not_replay(nodes, monkeypatch):
    make, clock, _ = nodes
    node = make(serial_mode='bridge')
    assert node.status_pub is None and node.cmd_sub is None and node.send_timer is None
    assert node.can_rx_pub is not None and node.can_tx_sub is not None
    node._on_send_timer()
    node.send_disable_zero()
    assert not node.transport.writes
    delivered = []
    monkeypatch.setattr(node, '_publish_json', lambda pub, data: delivered.append(data))
    tunnel = b''.join(frame(struct.pack('<BBBHB8s', 0xA0, 0, status, cid, dlc, data))
                      for status, cid, dlc, data in [(2, 0, 0, b''), (0, 0x456, 1, b'\xc8')])
    node.transport.incoming.extend(V2 + LEGACY + tunnel)
    node._on_read_timer()
    assert [d['status'] for d in delivered] == [2, 0]
    assert delivered[-1]['can_id'] == 0x456
    request = String(data='{"can_id":1,"dlc":2,"data":[4,143]}')
    node._on_can_tx(request)
    assert len(node.transport.writes) == 1 and node.transport.writes[0][3] == 0x20
    for bad in ('no JSON', '[]', '{"can_id":4096,"dlc":0,"data":[]}'):
        node._on_can_tx(String(data=bad))
    assert len(node.transport.writes) == 1
    node.transport.connected = False
    node._on_can_tx(request)
    clock.now += 2
    node._try_reconnect()
    assert len(node.transport.writes) == 1
    opens = node.transport.open_count
    node.transport.connected = False
    node.send_disable_zero()
    assert node.transport.open_count == opens


@pytest.mark.parametrize('failure', ['read', 'write'])
def test_transport_failure_resets_session(nodes, failure):
    make, _, _ = nodes
    node = make()
    node.rx_buffer.extend(V2[:5])
    node.status_policy.v2_seen = True
    node._on_cmd_vel(Twist())
    if failure == 'read':
        node.transport.fail_read = True
        node._on_read_timer()
    else:
        node.transport.fail_write = True
        node._on_send_timer()
    assert not node.rx_buffer
    assert node.status_policy.connected_at is None and not node.status_policy.v2_seen
    assert node.last_twist is None


def test_burst_budget_leaves_work_for_next_callback(nodes, monkeypatch):
    make, _, _ = nodes
    node = make(serial_mode='bridge')
    responses = []
    monkeypatch.setattr(node, '_publish_json', lambda pub, data: responses.append(data))
    packet = frame(struct.pack('<BBBHB8s', 0xA0, 1, 0, 0x701, 8, b'12345678'))
    node.transport.incoming.extend(packet * 300)
    node._on_read_timer()
    assert 0 < len(responses) < 300
    assert node.transport.incoming
    node._on_read_timer()
    assert len(responses) == 300
    assert not node.rx_buffer


@pytest.mark.parametrize('parameters', [
    {'serial_mode': 'typo'}, {'read_rate_hz': 0.0},
    {'send_rate_hz': float('nan')}, {'legacy_fallback_timeout_sec': -1.0},
])
def test_invalid_parameters_fail_before_port_open(nodes, monkeypatch, parameters):
    make, _, _ = nodes
    def unexpected_open(*args, **kwargs):
        pytest.fail('invalid parameters must not open transport')
    monkeypatch.setattr(FakeTransport, 'open', unexpected_open)
    with pytest.raises(ValueError):
        make(**parameters)


def test_mode_is_readonly(nodes):
    make, _, _ = nodes
    node = make()
    result = node.set_parameters([Parameter('serial_mode', value='bridge')])
    assert not result[0].successful
    assert node.serial_mode == 'control'


def spin_until(executor, condition, timeout=3):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        executor.spin_once(timeout_sec=0.02)
    assert condition()


@pytest.mark.parametrize('mode', ['control', 'bridge'])
def test_actual_ros_topic_roundtrip(nodes, mode):
    make, _, context = nodes
    node = make(serial_mode=mode)
    peer = Node('test_peer', context=context)
    executor = SingleThreadedExecutor(context=context)
    executor.add_node(node)
    executor.add_node(peer)
    output = []
    topic = '/motor_bridge_node/status' if mode == 'control' else '/motor_bridge_node/can_rx'
    subscription = peer.create_subscription(String, topic, lambda msg: output.append(json.loads(msg.data)), 100)
    publisher = peer.create_publisher(Twist if mode == 'control' else String,
                                     '/cmd_vel' if mode == 'control' else '/motor_bridge_node/can_tx', 10)
    try:
        spin_until(executor, lambda: publisher.get_subscription_count() > 0
                   and peer.count_publishers(topic) > 0)
        if mode == 'control':
            twist = Twist()
            twist.linear.x = 0.1
            publisher.publish(twist)
            spin_until(executor, lambda: node.last_twist is not None)
            node._on_send_timer()
            assert node.transport.writes[-1][5:7] == b'\x64\x00'
            payload = bytearray(V2[3:-1])
            payload[16:18] = bytes.fromhex('0C FE')  # LF controller_output = -500
            node.transport.incoming.extend(frame(payload))
        else:
            publisher.publish(String(data='{"can_id":1,"dlc":2,"data":[4,143]}'))
            spin_until(executor, lambda: bool(node.transport.writes))
            assert node.transport.writes[-1][3] == 0x20
            node.transport.incoming.extend(frame(struct.pack('<BBBHB8s', 0xA0, 0, 0, 0x701, 3, b'\x8f\xf0\0')))
        node._on_read_timer()
        spin_until(executor, lambda: bool(output))
        assert output[0]['version'] == 1 if mode == 'control' else output[0]['can_id'] == 0x701
        if mode == 'control':
            assert output[0]['wheels']['lf']['controller_output'] == -500
    finally:
        peer.destroy_subscription(subscription)
        executor.shutdown()
        peer.destroy_node()
