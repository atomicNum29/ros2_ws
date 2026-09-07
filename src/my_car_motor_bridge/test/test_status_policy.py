from my_car_motor_bridge.protocol import MotorStatus, try_parse_packet
from my_car_motor_bridge.status_policy import StatusPolicy
from test_protocol import V2

LEGACY = MotorStatus(0, 0, 0, 0)


def test_fallback_then_v2_and_no_downgrade():
    policy = StatusPolicy()
    assert not policy.should_publish(LEGACY, 100)
    policy.reset(100)
    assert not policy.should_publish(LEGACY, 100.99)
    assert policy.should_publish(LEGACY, 101)
    assert policy.should_publish(try_parse_packet(bytearray(V2)), 101.1)
    assert not policy.should_publish(LEGACY, 1000)
    policy.reset(1000)
    assert not policy.should_publish(LEGACY, 1000)
    assert policy.should_publish(LEGACY, 1001)


def test_immediate_v2_and_disabled_fallback():
    policy = StatusPolicy(legacy_enabled=False)
    policy.reset(10)
    assert not policy.should_publish(LEGACY, 100)
    assert policy.should_publish(try_parse_packet(bytearray(V2)), 10)
    policy.reset()
    assert not policy.should_publish(LEGACY, 1000)


def test_zero_wait():
    policy = StatusPolicy(fallback_timeout=0)
    policy.reset(0)
    assert policy.should_publish(LEGACY, 0)
