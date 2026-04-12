from launch import LaunchDescription
from launch_ros.actions import Node
from launch.substitutions import Command, PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    pkg_share = FindPackageShare("my_car_package")
    xacro_file = PathJoinSubstitution(
        [pkg_share, "urdf", "simple_car_frame.urdf.xacro"]
    )
    rviz_config = PathJoinSubstitution([pkg_share, "rviz", "display.rviz"])

    robot_description = {"robot_description": Command(["xacro ", xacro_file])}

    return LaunchDescription(
        [
            Node(
                package="robot_state_publisher",
                executable="robot_state_publisher",
                parameters=[robot_description],
            ),
            Node(package="joint_state_publisher", executable="joint_state_publisher"),
            Node(package="rviz2", executable="rviz2", arguments=["-d", rviz_config]),
        ]
    )
