"""ROS camera conversion, bounded delivery and WebRTC track integration."""

import asyncio
from io import BytesIO
import threading
from unittest.mock import Mock

import numpy as np
from PIL import Image as PillowImage
import pytest
import rclpy
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from rclpy.context import Context
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import CompressedImage, Image

from my_car_web_monitor.config import Settings
from my_car_web_monitor.sources.ros2_source import RosImageSource, decode_image
from my_car_web_monitor.streaming.webrtc import WebRTCVideoTrack
import my_car_web_monitor.streaming.webrtc as webrtc
from my_car_web_monitor.streams import StreamRegistry, parse_stream_specs


def mono(value=42):
    return Image(width=2, height=2, encoding="mono8", step=3,
                 data=[value, value, 255, value, value, 255])


@pytest.mark.parametrize("encoding,data,expected", [
    ("mono8", [7], [7, 7, 7]),
    ("8UC1", [9], [9, 9, 9]),
    ("rgb8", [10, 20, 30], [10, 20, 30]),
    ("bgr8", [30, 20, 10], [10, 20, 30]),
    ("rgba8", [10, 20, 30, 99], [10, 20, 30]),
    ("bgra8", [30, 20, 10, 99], [10, 20, 30]),
])
def test_raw_encodings_with_padding(encoding, data, expected):
    message = Image(width=1, height=2, encoding=encoding, step=len(data) + 1,
                    is_bigendian=1, data=(data + [255]) * 2)
    frame = decode_image(message)
    assert frame.tolist() == [[expected], [expected]]
    assert frame.flags.c_contiguous
    message.data[0] = 0
    assert frame[0, 0].tolist() == expected


@pytest.mark.parametrize("changes", [
    {"encoding": "16UC1"}, {"width": 0}, {"step": 1}, {"data": [1]},
])
def test_bad_raw_images_are_rejected(changes):
    message = mono()
    for name, value in changes.items():
        setattr(message, name, value)
    with pytest.raises(ValueError):
        decode_image(message)


@pytest.mark.parametrize("codec", ["JPEG", "PNG"])
def test_compressed_grayscale(codec):
    buffer = BytesIO()
    PillowImage.new("L", (4, 2), 50).save(buffer, format=codec)
    message = CompressedImage(format=codec.lower(), data=buffer.getvalue())
    frame = decode_image(message)
    assert frame.shape == (2, 4, 3)
    assert np.all(frame == 50)


def test_compressed_color_and_invalid_data():
    buffer = BytesIO()
    PillowImage.new("RGB", (2, 2), (10, 20, 30)).save(buffer, format="PNG")
    assert decode_image(CompressedImage(format="rgb8; png compressed bgr8",
                                       data=buffer.getvalue()))[0, 0].tolist() == [10, 20, 30]
    with pytest.raises(ValueError, match="compressedDepth"):
        decode_image(CompressedImage(format="16UC1; compressedDepth png", data=b"bad"))
    with pytest.raises(OSError):
        decode_image(CompressedImage(format="jpeg", data=b"bad"))


def test_lifecycle_latest_frame_multiple_viewers_and_restart():
    async def run():
        node = Mock()
        source = RosImageSource(node, "/camera/fisheye1/image_raw", 200)
        await source.start()
        await source.start()
        node.create_subscription.assert_called_once()
        callback = node.create_subscription.call_args.args[2]
        qos = node.create_subscription.call_args.args[3]
        assert qos.reliability == ReliabilityPolicy.BEST_EFFORT
        assert qos.depth == 1
        first = asyncio.create_task(source.read())
        second = asyncio.create_task(source.read())
        await asyncio.sleep(0.02)
        assert not first.done() and not second.done()

        def publish_burst():
            for value in range(100):
                callback(mono(value))

        thread = threading.Thread(target=publish_burst)
        thread.start()
        thread.join()
        frames = await asyncio.wait_for(asyncio.gather(first, second), 1)
        assert all(np.all(frame == 99) for frame in frames)
        track = WebRTCVideoTrack(source)
        video = await track.recv()
        assert video.width == 2 and video.height == 2
        assert video.pts > 0
        assert np.all(video.to_ndarray(format="rgb24") == 99)
        await source.stop()
        await source.stop()
        node.destroy_subscription.assert_called_once()
        track.stop()

        await source.start()
        pending = asyncio.create_task(source.read())
        callback(mono(200))  # callback from the retired subscription is ignored
        await asyncio.sleep(0.02)
        assert not pending.done()
        await source.stop()
        with pytest.raises(RuntimeError, match="not started"):
            await asyncio.wait_for(pending, 1)
    asyncio.run(run())


def test_bad_frame_recovery_and_cancel_before_first_image():
    async def run():
        node = Mock()
        source = RosImageSource(node, "/image", 200)
        await source.start()
        callback = node.create_subscription.call_args.args[2]
        pending = asyncio.create_task(source.read())
        callback(Image(width=2, height=2, encoding="mono8", step=2, data=[1]))
        await asyncio.sleep(0.02)
        assert not pending.done()
        callback(mono(88))
        assert np.all(await asyncio.wait_for(pending, 1) == 88)
        await source.stop()
        await source.start()
        pending = asyncio.create_task(source.read())
        await asyncio.sleep(0.01)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await source.stop()
    asyncio.run(run())


@pytest.mark.parametrize("streams", [
    "left:ros_image", "left:ros_image:", "left:ros_compressed",
    ":ros_image:/image", "left:ros_image:/image:extra",
    "left:synthetic,left:ros_image:/image",
])
def test_invalid_stream_specs(streams):
    with pytest.raises(ValueError):
        parse_stream_specs(Settings(camera_streams=streams))


def test_registry_mixes_sources_and_releases_ros_subscription():
    async def run():
        node = Mock()
        registry = StreamRegistry(Settings(camera_streams=(
            "debug:synthetic,left:ros_image:/camera/fisheye1/image_raw,"
            "right:ros_compressed:/camera/fisheye2/image_raw/compressed")), node)
        assert registry.list_streams() == [
            {"id": "debug", "source_type": "synthetic", "device": ""},
            {"id": "left", "source_type": "ros_image", "device": "/camera/fisheye1/image_raw"},
            {"id": "right", "source_type": "ros_compressed",
             "device": "/camera/fisheye2/image_raw/compressed"},
        ]
        node.create_subscription.assert_not_called()
        await registry._managers["right"].ensure_source_started()
        assert node.create_subscription.call_args.args[0] is CompressedImage
        await registry.close()
        node.destroy_subscription.assert_called_once()
    asyncio.run(run())


def test_webrtc_two_viewers_receive_ros_video_and_last_close_unsubscribes(monkeypatch):
    # Exercise real SDP, RTP encoding/decoding and connection cleanup locally,
    # without external STUN servers or camera hardware.
    make_peer = lambda: RTCPeerConnection(RTCConfiguration(iceServers=[]))
    monkeypatch.setattr(webrtc, "RTCPeerConnection", make_peer)

    async def run():
        node = Mock()
        source = RosImageSource(node, "/fisheye", 30)
        manager = webrtc.PeerManager(source)
        clients = []
        tasks = []
        try:
            for _ in range(2):
                client = make_peer()
                clients.append(client)
                received = asyncio.get_running_loop().create_future()

                @client.on("track")
                def on_track(track, received=received):
                    received.set_result(track)

                client.addTransceiver("video", direction="recvonly")
                await client.setLocalDescription(await client.createOffer())
                answer = await manager.create_answer({
                    "sdp": client.localDescription.sdp, "type": "offer",
                })
                await client.setRemoteDescription(RTCSessionDescription(**answer))
                track = await asyncio.wait_for(received, 2)
                node.create_subscription.call_args.args[2](
                    Image(width=32, height=32, encoding="mono8", step=32, data=[73] * 1024))
                tasks.append(asyncio.create_task(track.recv()))
            frames = await asyncio.wait_for(asyncio.gather(*tasks), 10)
            assert all((frame.width, frame.height) == (32, 32) for frame in frames)
            assert all(abs(float(frame.to_ndarray(format="rgb24").mean()) - 73) < 5
                       for frame in frames)
            node.create_subscription.assert_called_once()
            peers = list(manager._pcs)
            await manager._discard_peer(peers[0])
            node.destroy_subscription.assert_not_called()
            await manager._discard_peer(peers[1])
            node.destroy_subscription.assert_called_once()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.gather(*(client.close() for client in clients))
            await manager.close()
    asyncio.run(run())


@pytest.mark.parametrize("reliability", [ReliabilityPolicy.BEST_EFFORT, ReliabilityPolicy.RELIABLE])
def test_real_ros_publisher_to_video_track(reliability):
    async def run():
        context = Context()
        rclpy.init(context=context)
        node = Node("ros_image_source_test", context=context)
        executor = SingleThreadedExecutor(context=context)
        executor.add_node(node)
        source = RosImageSource(node, "/test/fisheye/image_raw", 30)
        publisher = node.create_publisher(Image, source.topic,
                                         QoSProfile(depth=1, reliability=reliability))
        track = WebRTCVideoTrack(source)
        pending = None
        try:
            await source.start()
            pending = asyncio.create_task(track.recv())
            for _ in range(200):
                publisher.publish(mono(73))
                executor.spin_once(timeout_sec=0.01)
                await asyncio.sleep(0.01)
                if pending.done():
                    break
            frame = await asyncio.wait_for(pending, 1)
            assert frame.to_ndarray(format="rgb24").tolist() == [[[73] * 3] * 2] * 2
        finally:
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            track.stop()
            await source.stop()
            executor.shutdown()
            node.destroy_node()
            context.shutdown()
    asyncio.run(run())
