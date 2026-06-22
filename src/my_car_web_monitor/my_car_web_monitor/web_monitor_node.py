from __future__ import annotations

import logging
import threading

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
import uvicorn

from my_car_web_monitor.config import settings
from my_car_web_monitor.server import create_app


def main(args: list[str] | None = None) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    rclpy.init(args=args)
    node = Node("web_monitor_node")
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    spin_thread = threading.Thread(target=executor.spin, daemon=True)
    spin_thread.start()

    app = create_app(node, settings)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=settings.host,
            port=settings.port,
            log_level=settings.log_level,
            reload=False,
        )
    )

    try:
        server.run()
    finally:
        executor.shutdown()
        spin_thread.join(timeout=2.0)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()

