import os
from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
    IncludeLaunchDescription,
)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from ament_index_python.packages import get_package_share_directory
from launch.substitutions import LaunchConfiguration
from launch.conditions import IfCondition


def generate_launch_description():
    # 1. 기존 로봇 URDF 실행 (패키지 이름과 launch 파일명을 본인 설정에 맞게 수정하세요)
    robot_description_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            [
                os.path.join(
                    get_package_share_directory("my_car_package"),
                    "launch",
                    "description.launch.py",
                )
            ]
        ),
        launch_arguments={"use_sim_time": "true"}.items(),
    )

    # 2. 끊어진 다리 연결 (Static TF Publisher)
    # camera_pose_frame -> camera_link 연결
    static_tf_node = Node(
        package="tf2_ros",
        executable="static_transform_publisher",
        arguments=["0", "0", "0", "0", "0", "0", "camera_pose_frame", "camera_link"],
        parameters=[{"use_sim_time": True}],
    )

    # 4. Bag 파일 재생 (파일명 옵션으로 지정 필요)
    # --clock 옵션이 매우 중요합니다.
    bag_file_arg = DeclareLaunchArgument(
        "bag_file",
        default_value="your_bag_file_path.mcap",
        description="Path to the bag file to play",
    )
    bag_play_speed_arg = DeclareLaunchArgument(
        "play_speed",
        default_value="0.1",
        description="Playback speed for the bag file (e.g., 0.1 for 10% speed)",
    )
    bag_play_process = ExecuteProcess(
        cmd=[
            "ros2",
            "bag",
            "play",
            LaunchConfiguration("bag_file"),
            "--clock",
            "-r",
            LaunchConfiguration("play_speed"),
            # "-d",
            # "5",
            # "--start-offset",
            # "1",
        ],
        output="screen",
    )

    # 6. RViz2 실행 (선택 사항)
    # use_rviz 매개변수를 통해 RViz2 실행 여부를 제어할 수 있도록 수정
    use_rviz_arg = DeclareLaunchArgument(
        "use_rviz", default_value="false", description="Whether to launch RViz2"
    )
    use_rviz = LaunchConfiguration("use_rviz")
    rviz_config = (
        "/root/.rviz2/Map_TF_RM_LS.rviz"  # RViz 설정 파일 경로 (필요에 따라 수정)
    )
    rviz_node = Node(
        package="rviz2",
        executable="rviz2",
        arguments=["-d", rviz_config],
        parameters=[{"use_sim_time": True}],
        output="screen",
        condition=IfCondition(use_rviz),
    )

    # 앞서 만든 lua 파일이 있는 폴더의 절대 경로를 입력합니다.
    configuration_directory = "/root/cartographer_test"
    configuration_basename = "baseline.lua"

    return LaunchDescription(
        [
            # robot_description_launch,
            # static_tf_node,
            bag_play_process,
            bag_file_arg,
            bag_play_speed_arg,
            use_rviz_arg,
            rviz_node,
            Node(
                package="cartographer_ros",
                executable="cartographer_node",
                name="cartographer_node",
                output="screen",
                parameters=[{"use_sim_time": True}],
                remappings=[("/odom", "/camera/pose/sample")],
                arguments=[
                    "-configuration_directory",
                    configuration_directory,
                    "-configuration_basename",
                    configuration_basename,
                ],
            ),
            Node(
                package="cartographer_ros",
                executable="cartographer_occupancy_grid_node",
                name="cartographer_occupancy_grid_node",
                output="screen",
                parameters=[
                    {
                        "use_sim_time": True,
                        "resolution": 0.05,
                        "publish_period_sec": 1.0,
                    }
                ],
            ),
        ]
    )
