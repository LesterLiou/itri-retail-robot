"""Reuse upstream vision docking; calibration and target are explicit inputs."""
from pathlib import Path
import yaml
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument,OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def start(context):
    path = Path(LaunchConfiguration('config').perform(context))
    config = yaml.safe_load(path.read_text())
    return [
        Node(package='tidybot_ros2_control',executable='aruco_detector',output='screen',parameters=[config['detector']]),
        Node(package='tidybot_ros2_control',executable='docking_controller',output='screen',parameters=[config['controller']]),
    ]


def generate_launch_description():
    return LaunchDescription([DeclareLaunchArgument('config'),OpaqueFunction(function=start)])
