from launch import LaunchDescription
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory

import os


def generate_launch_description():
    config_file = os.path.join(
        get_package_share_directory("my_car_motor_bridge"),
        "config",
        "motor_bridge.yaml",
    )

    return LaunchDescription(
        [
            Node(
                package="my_car_motor_bridge",
                executable="motor_bridge_node",
                name="motor_bridge_node",
                output="screen",
                parameters=[config_file],
            )
        ]
    )
