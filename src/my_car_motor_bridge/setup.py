from glob import glob
import os

from setuptools import find_packages, setup


package_name = "my_car_motor_bridge"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        (
            "share/ament_index/resource_index/packages",
            [os.path.join("resource", package_name)],
        ),
        (os.path.join("share", package_name), ["package.xml"]),
        (
            os.path.join("share", package_name, "launch"),
            glob(os.path.join("launch", "*.launch.py")),
        ),
        (
            os.path.join("share", package_name, "config"),
            glob(os.path.join("config", "*.yaml")),
        ),
    ],
    install_requires=["setuptools", "pyserial"],
    zip_safe=True,
    maintainer="Bak siu",
    maintainer_email="atomicw63.546@gmail.com",
    description="ROS2 motor bridge that converts /cmd_vel to normalized MCU serial packets.",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "motor_bridge_node = my_car_motor_bridge.motor_bridge_node:main",
        ],
    },
)
