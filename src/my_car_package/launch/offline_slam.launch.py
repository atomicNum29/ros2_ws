import os
from datetime import datetime
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
    IncludeLaunchDescription,
)
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


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

    # 3. slam_toolbox 실행
    slam_toolbox_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            [
                os.path.join(
                    get_package_share_directory("slam_toolbox"),
                    "launch",
                    # "offline_launch.py",
                    "online_sync_launch.py",
                )
            ]
        ),
        launch_arguments={
            "slam_params_file": "/root/my_offline_params.yaml",
            "use_sim_time": "true",
        }.items(),
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
            "-d",
            "5",
            "--start-offset",
            "1",
        ],
        output="screen",
    )

    # # 5. Bag 파일 녹화
    # bag_file_name = (
    #     f"/bag_files/slam_recording_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    # )
    # bag_record_process = ExecuteProcess(
    #     cmd=[
    #         "ros2",
    #         "bag",
    #         "record",
    #         "--use-sim-time",
    #         "-o",
    #         bag_file_name,
    #         "/tf",
    #         "/tf_static",
    #         "/scan",
    #         "/slam_toolbox/graph_visualization",
    #         "/map",
    #         "/map_metadata",
    #     ],
    #     output="screen",
    # )

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

    return LaunchDescription(
        [
            robot_description_launch,
            static_tf_node,
            slam_toolbox_launch,
            bag_play_process,
            bag_file_arg,
            bag_play_speed_arg,
            use_rviz_arg,
            rviz_node,
            # bag_record_process,
        ]
    )
