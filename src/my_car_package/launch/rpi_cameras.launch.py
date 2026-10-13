from launch import LaunchDescription
from launch_ros.actions import Node
from pathlib import Path


def generate_launch_description():

    config_file = Path(__file__).parent / "yaml" / "cameras.yaml"

    front_camera = Node(
        package="camera_ros",
        executable="camera_node",
        name="front_camera",
        parameters=[str(config_file)],
        output="screen",
    )

    rear_camera = Node(
        package="camera_ros",
        executable="camera_node",
        name="rear_camera",
        parameters=[str(config_file)],
        output="screen",
    )

    return LaunchDescription(
        [
            front_camera,
            rear_camera,
        ]
    )
