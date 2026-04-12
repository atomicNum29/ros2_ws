import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import ExecuteProcess, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
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

    # # 3. Bag 파일 재생 (파일명 수정 필요)
    # # --clock 옵션이 매우 중요합니다.
    # bag_play_process = ExecuteProcess(
    #     cmd=["ros2", "bag", "play", "your_bag_file_path.mcap", "--clock"],
    #     output="screen",
    # )

    # 4. RViz2 실행 (선택 사항)
    rviz_node = Node(
        package="rviz2",
        executable="rviz2",
        parameters=[{"use_sim_time": True}],
        output="screen",
    )

    return LaunchDescription(
        [robot_description_launch, static_tf_node, rviz_node]
    )
