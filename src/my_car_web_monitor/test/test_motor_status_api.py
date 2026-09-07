"""Exercise the web status API with both motor bridge JSON schemas, no camera."""

import asyncio
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest
from std_msgs.msg import String

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from my_car_web_monitor.config import Settings
from my_car_web_monitor.server import create_app


@pytest.mark.parametrize('status', [
    {'seq': 255, 'state': 2, 'error': 4, 'battery_mv': 24000},
    {'version': 1, 'seq': 16, 'state': 1, 'system_error': 4, 'validity': 0,
     'drivers': {'a': {'voltage_mv': 24000, 'valid': False},
                 'b': {'voltage_mv': 23800, 'valid': False}},
     'wheels': {wheel: {'status': 0, 'rpm': -100, 'current_deci_amp': 123,
                        'controller_output': -500, 'valid': False}
                for wheel in ('lf', 'rf', 'lr', 'rr')}},
])
def test_status_survives_http_api(status):
    async def run():
        node = Mock()
        app = create_app(node, Settings(camera_source='synthetic', camera_streams=''))
        async with app.router.lifespan_context(app):
            callback = node.create_subscription.call_args.args[2]
            raw = json.dumps(status)
            callback(String(data=raw))
            messages = []

            async def receive():
                return {'type': 'http.request', 'body': b'', 'more_body': False}

            async def send(message):
                messages.append(message)

            await app({
                'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                'method': 'GET', 'scheme': 'http', 'path': '/control/status',
                'raw_path': b'/control/status', 'query_string': b'', 'root_path': '',
                'headers': [], 'server': ('test', 80), 'client': ('test', 12345),
            }, receive, send)
            assert messages[0]['status'] == 200
            body = b''.join(message.get('body', b'') for message in messages)
            result = json.loads(body)
            assert result['motor_status'] == status
            assert result['motor_status_raw'] == raw
            assert result['motor_status_age_sec'] >= 0
    asyncio.run(run())
