#!/usr/bin/env bash
set -eo pipefail
source /opt/itri/setup.bash
test "$(id -u)" -ne 0
test "${ROS_DISTRO}" = humble
write_probe=$(mktemp /workspace/ros2_ws/.write-check.XXXXXX)
rm "$write_probe"
python3 -c 'import rclpy, cv_bridge, numpy, yaml; from control_msgs.action import FollowJointTrajectory; from nav2_msgs.action import NavigateToPose; from moveit_msgs.msg import PlanningScene; print("PASS: ROS Python interfaces")'
for package in controller_manager joint_trajectory_controller moveit_ros_move_group nav2_bringup rmw_cyclonedds_cpp; do
    ros2 pkg prefix "${package}"
done
colcon --help >/dev/null
vcs --help >/dev/null
python3 /opt/itri/ros_smoke.py
# Exercise a clean CMake/ament build without adding a test package to the repo.
check_ws=$(mktemp -d /tmp/itri-dev-check.XXXXXX)
trap 'rm -rf "$check_ws"' EXIT
mkdir -p "$check_ws/src/toolchain_probe"
cat > "$check_ws/src/toolchain_probe/package.xml" <<'XML'
<?xml version="1.0"?>
<package format="3">
  <name>toolchain_probe</name><version>0.0.0</version>
  <description>Temporary container toolchain acceptance</description>
  <maintainer email="dev@example.invalid">Development</maintainer>
  <license>Apache-2.0</license>
  <buildtool_depend>ament_cmake</buildtool_depend>
  <export><build_type>ament_cmake</build_type></export>
</package>
XML
cat > "$check_ws/src/toolchain_probe/CMakeLists.txt" <<'CMAKE'
cmake_minimum_required(VERSION 3.16)
project(toolchain_probe)
find_package(ament_cmake REQUIRED)
ament_package()
CMAKE
cd "$check_ws"
colcon build --event-handlers console_direct+
test -f install/toolchain_probe/share/toolchain_probe/package.xml
echo 'PASS: non-root Humble development toolchain and colcon build'
