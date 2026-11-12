"""Launch pinned TidyBot driver and gamepad with upstream manual mux remapped."""
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import Shutdown
from launch_ros.actions import Node


def generate_launch_description():
    package = 'tidybot_ros2_control'
    config = get_package_share_directory(package) + '/config/gamepad.yaml'

    def start(executable, name, source=package, remappings=(), parameters=None):
        return Node(package=source, executable=executable, name=name,
                    parameters=parameters or [config], remappings=list(remappings),
                    output='screen', on_exit=[Shutdown(reason=f'{name} exited')])

    return LaunchDescription([
        start('joy_node', 'joy_node', 'joy'),
        start('gamepad_teleop', 'tidybot_gamepad_teleop', remappings=[
            ('/cmd_vel', '/teleop/cmd_vel'), ('/tidybot/enable', '/teleop/enable'),
            ('/tidybot/control_frame', '/teleop/control_frame'), ('/tidybot/stop', '/teleop/stop'),
        ]),
        start('control_mux', 'tidybot_control_mux', remappings=[
            (topic, '/manual_control' + topic) for topic in
            ('/cmd_vel', '/tidybot/enable', '/tidybot/control_frame')
        ]),
        start('base_node', 'tidybot_base_node', parameters=[config, {'require_enable': True}]),
    ])
