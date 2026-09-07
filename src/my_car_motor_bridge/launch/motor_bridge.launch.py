import os

import yaml

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    config_file = os.path.join(
        get_package_share_directory("my_car_motor_bridge"), "config", "motor_bridge.yaml",
    )
    with open(config_file, encoding="utf-8") as stream:
        defaults = yaml.safe_load(stream)["motor_bridge_node"]["ros__parameters"]
    return LaunchDescription([
        DeclareLaunchArgument(
            "serial_mode", default_value=defaults.get("serial_mode", "control"),
            choices=["control", "bridge"],
        ),
        DeclareLaunchArgument(
            "legacy_fallback_enabled",
            default_value=str(defaults.get("legacy_fallback_enabled", True)).lower(),
            choices=["true", "false"],
        ),
        DeclareLaunchArgument(
            "legacy_fallback_timeout_sec",
            default_value=str(defaults.get("legacy_fallback_timeout_sec", 1.0)),
        ),
        Node(
            package="my_car_motor_bridge", executable="motor_bridge_node",
            name="motor_bridge_node", output="screen",
            parameters=[config_file, {
                "serial_mode": LaunchConfiguration("serial_mode"),
                "legacy_fallback_enabled": ParameterValue(
                    LaunchConfiguration("legacy_fallback_enabled"), value_type=bool,
                ),
                "legacy_fallback_timeout_sec": ParameterValue(
                    LaunchConfiguration("legacy_fallback_timeout_sec"), value_type=float,
                ),
            }],
        ),
    ])
