import asyncio
import json
from unittest.mock import Mock

import pytest
from std_msgs.msg import String

from my_car_web_monitor.config import Settings
from my_car_web_monitor.server import create_app


@pytest.mark.parametrize('interval,count', [(0, 100), (0.1, 20)])
def test_status_websocket_delivers_all_messages_to_multiple_readonly_clients(interval, count):
    async def run():
        node = Mock()
        app = create_app(node, Settings(camera_source='synthetic', camera_streams=''))
        async with app.router.lifespan_context(app):
            callback = node.create_subscription.call_args.args[2]
            clients = []
            for _ in range(2):
                incoming, outgoing = asyncio.Queue(), asyncio.Queue()
                await incoming.put({'type': 'websocket.connect'})
                task = asyncio.create_task(app({
                    'type': 'websocket', 'asgi': {'version': '3.0'}, 'scheme': 'ws',
                    'path': '/ws/status', 'raw_path': b'/ws/status', 'query_string': b'',
                    'root_path': '', 'headers': [], 'server': ('test', 80), 'client': ('test', 1),
                    'subprotocols': [],
                }, incoming.get, outgoing.put))
                assert (await asyncio.wait_for(outgoing.get(), 1))['type'] == 'websocket.accept'
                initial = json.loads((await asyncio.wait_for(outgoing.get(), 1))['text'])
                assert initial['motor_status_received_count'] == 0
                clients.append((incoming, outgoing, task))
            try:
                # Even command-shaped messages on the status route are inert.
                await clients[0][0].put({'type': 'websocket.receive', 'text': '{"type":"command","linear":1}'})

                async def publish():
                    for index in range(count):
                        await asyncio.to_thread(callback, String(data=json.dumps({
                            'version': 1, 'seq': (250 + index) % 256,
                            'state': 1, 'system_error': 0, 'controller_output': -index,
                        })))
                        if interval:
                            await asyncio.sleep(interval)

                async def collect(outgoing):
                    result = []
                    while len(result) < count:
                        event = await asyncio.wait_for(outgoing.get(), 3)
                        data = json.loads(event['text'])
                        if data['type'] == 'motor_status':
                            result.append(data)
                    return result

                _, first, second = await asyncio.gather(publish(), collect(clients[0][1]), collect(clients[1][1]))
                for received in [first, second]:
                    assert [p['motor_status_received_count'] for p in received] == list(range(1, count + 1))
                    assert [p['motor_status']['seq'] for p in received] == [(250 + i) % 256 for i in range(count)]
                    assert all(p['stream_dropped_count'] == 0 for p in received)
                    assert all(p['motor_status_age_sec'] >= 0 for p in received)
                # Disconnect one viewer; the other continues receiving.
                await clients[0][0].put({'type': 'websocket.disconnect', 'code': 1000})
                await asyncio.wait_for(clients[0][2], 1)
                callback(String(data='{"seq":99}'))
                next_packet = json.loads((await asyncio.wait_for(clients[1][1].get(), 1))['text'])
                assert next_packet['motor_status_received_count'] == count + 1
                node.create_publisher.return_value.publish.assert_not_called()
            finally:
                for incoming, _, task in clients:
                    if not task.done():
                        await incoming.put({'type': 'websocket.disconnect', 'code': 1000})
                    await asyncio.wait_for(task, 1)
                node.create_publisher.return_value.publish.assert_not_called()
    asyncio.run(run())
