from glob import glob
import os

from setuptools import find_packages, setup


package_name = "my_car_web_monitor"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        (
            "share/ament_index/resource_index/packages",
            [os.path.join("resource", package_name)],
        ),
        (os.path.join("share", package_name), ["package.xml", "README.md", "PLAN.md", "pyproject.toml"]),
        (
            os.path.join("share", package_name, "web", "static"),
            glob(os.path.join(package_name, "web", "static", "*")),
        ),
    ],
    include_package_data=True,
    package_data={package_name: ["web/static/*"]},
    zip_safe=True,
    maintainer="Bak siu",
    maintainer_email="atomicw63.546@gmail.com",
    description="Browser camera monitoring and ROS2 teleoperation bridge for my_car.",
    license="Apache-2.0",
    tests_require=["pytest"],
)

