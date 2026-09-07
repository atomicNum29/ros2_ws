import asyncio
import json

from my_car_web_monitor.status_stream import StatusStream


def test_executor_thread_burst_preserves_every_snapshot_and_wrap():
    async def run():
        stream = StatusStream()
        clients = [stream.subscribe(), stream.subscribe()]
        for client in clients:
            assert (await client.queue.get()).count == 0

        def publish():
            for index in range(200):
                stream.publish(json.dumps({'seq': (250 + index) % 256, 'rpm': -index}))
        await asyncio.to_thread(publish)
        for client in clients:
            received = [await asyncio.wait_for(client.queue.get(), 1) for _ in range(200)]
            assert [s.count for s in received] == list(range(1, 201))
            assert [s.value['seq'] for s in received] == [(250 + i) % 256 for i in range(200)]
            assert [s.value['rpm'] for s in received] == [-i for i in range(200)]
            assert all(json.loads(s.raw) == s.value for s in received)
            assert client.dropped == 0
            stream.unsubscribe(client)
    asyncio.run(run())


def test_slow_subscriber_reports_overflow_without_blocking_other_clients():
    async def run():
        stream = StatusStream(queue_size=8)
        slow, fast = stream.subscribe(), stream.subscribe()
        await slow.queue.get()
        await fast.queue.get()
        received = []
        for index in range(30):
            stream.publish(json.dumps({'seq': index}))
            received.append((await asyncio.wait_for(fast.queue.get(), 1)).value['seq'])
        assert received == list(range(30))
        assert fast.dropped == 0
        assert slow.dropped == 22
        assert [slow.queue.get_nowait().value['seq'] for _ in range(8)] == list(range(22, 30))
        stream.unsubscribe(slow)
        stream.unsubscribe(fast)
    asyncio.run(run())


def test_unsubscribe_ignores_already_scheduled_delivery_and_reconnect_gets_latest():
    async def run():
        stream = StatusStream()
        client = stream.subscribe()
        await client.queue.get()
        stream.publish('{"seq":3}')
        stream.unsubscribe(client)
        await asyncio.sleep(0)
        assert client.queue.empty()
        new_client = stream.subscribe()
        last = await new_client.queue.get()
        assert last.value == {'seq': 3} and last.count == 1
        stream.publish('invalid JSON')
        bad = await new_client.queue.get()
        assert bad.value is None and bad.raw == 'invalid JSON' and bad.count == 2
        stream.unsubscribe(new_client)
        assert not stream._clients
    asyncio.run(run())
