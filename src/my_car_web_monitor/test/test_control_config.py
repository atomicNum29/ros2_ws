"""Independent slider limits and initial speeds."""
import asyncio
import math
from unittest.mock import Mock

import pytest

from my_car_web_monitor.config import Settings
from my_car_web_monitor.server import create_app
from my_car_web_monitor.control import RosControlBridge


def test_config_endpoint_exposes_independent_limits_and_defaults():
    settings = Settings(control_linear_speed_min=0.1, control_linear_speed_default=0.4,
                        control_linear_speed=1.5, control_angular_speed_min=0.2,
                        control_angular_speed_default=0.8, control_angular_speed=2.5)
    app = create_app(Mock(), settings)
    endpoint = next(route.endpoint for route in app.routes if getattr(route, 'path', '') == '/control/config')
    assert asyncio.run(endpoint()) == {
        'linear_speed': 1.5, 'linear_speed_min': 0.1, 'linear_speed_default': 0.4,
        'angular_speed': 2.5, 'angular_speed_min': 0.2, 'angular_speed_default': 0.8,
    }


@pytest.mark.parametrize('axis', ['linear', 'angular'])
@pytest.mark.parametrize('minimum,default,maximum', [(-1, 0, 1), (0.5, 0.2, 1), (0, 2, 1),
                                                   (0, math.nan, 1), (0, 1, math.inf)])
def test_invalid_slider_configuration_rejected(axis, minimum, default, maximum):
    with pytest.raises(ValueError):
        Settings(**{f'control_{axis}_speed_min': minimum,
                    f'control_{axis}_speed_default': default,
                    f'control_{axis}_speed': maximum})


def test_minimum_does_not_block_stop_or_reverse():
    node = Mock()
    bridge = RosControlBridge(node, Settings(control_linear_speed=1.0, control_angular_speed=3.0,
                                            control_linear_speed_min=0.1,
                                            control_angular_speed_min=0.2))
    asyncio.run(bridge.send_command(-5, -5))
    message = node.create_publisher.return_value.publish.call_args.args[0]
    assert (message.linear.x, message.angular.z) == (-1.0, -3.0)
    asyncio.run(bridge.stop())
    message = node.create_publisher.return_value.publish.call_args.args[0]
    assert (message.linear.x, message.angular.z) == (0.0, 0.0)
