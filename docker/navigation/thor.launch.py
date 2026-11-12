"""Navigation-only Jazzy launch with the repository's no-recovery behavior tree."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from launch import LaunchDescription
from launch.actions import OpaqueFunction, Shutdown
from launch_ros.actions import Node
import yaml


def navigation_nodes(context):
    root = Path(__file__).resolve().parent
    parameters = yaml.safe_load((root/'nav2.yaml').read_text())
    params = lambda name: parameters[name]['ros__parameters']
    params('map_server')['topic_name'] = '/map'
    params('planner_server')['GridBased']['plugin'] = 'nav2_navfn_planner::NavfnPlanner'
    controller = params('controller_server')
    controller['progress_checker_plugins'] = [controller.pop('progress_checker_plugin')]
    controller['enable_stamped_cmd_vel'] = True
    controller['controller_frequency'] = 20.0
    navigator = params('bt_navigator')
    navigator.pop('plugin_lib_names')
    work = Path(context.launch_configurations['navigation_tmp'])
    tree = work/'navigate.xml'
    tree.write_text((root/'navigate.xml').read_text().replace('<root ', '<root BTCPP_format="4" ', 1))
    navigator.update(navigators=['navigate_to_pose'], default_nav_to_pose_bt_xml=str(tree))
    navigator['navigate_to_pose'] = {'plugin': 'nav2_bt_navigator::NavigateToPoseNavigator'}
    footprint = os.environ.get('NAV_FOOTPRINT')
    live = os.environ.get('NAV_LIVE_MAP') == 'true'
    if live and not footprint:
        raise RuntimeError('NAV_FOOTPRINT must be measured and configured for a live map')
    for name in ('local_costmap', 'global_costmap'):
        if footprint:
            parameters[name][name]['ros__parameters']['footprint'] = footprint
    global_map = parameters['global_costmap']['global_costmap']['ros__parameters']
    global_map['static_layer']['map_topic'] = '/map'
    if live:
        local_map = parameters['local_costmap']['local_costmap']['ros__parameters']
        global_map['plugins'].insert(1, 'obstacle_layer')
        global_map['obstacle_layer'] = local_map['obstacle_layer']
    param_path = work/'params.yaml'
    param_path.write_text(yaml.safe_dump({'thor': parameters}))

    def nav_node(package, executable, extra=None, remappings=()):
        return Node(package=package, executable=executable, name=executable, namespace='thor',
                    parameters=[str(param_path), extra or {}],
                    remappings=[('odom', '/odom'), ('map', '/map'), *remappings],
                    output='screen', on_exit=[Shutdown(reason=f'{executable} stopped')])

    names = ['map_server', 'planner_server', 'controller_server', 'bt_navigator']
    return [
        nav_node('nav2_map_server', 'map_server', {'yaml_filename': os.environ['NAV_MAP_FILE']}),
        nav_node('nav2_planner', 'planner_server'),
        nav_node('nav2_controller', 'controller_server', remappings=[('cmd_vel', '/thor/nav_velocity')]),
        nav_node('nav2_bt_navigator', 'bt_navigator'),
        nav_node('nav2_lifecycle_manager', 'lifecycle_manager',
                 {'autostart': True, 'node_names': names}),
        Node(package='retail_core', executable='thor_navigation', output='screen',
             on_exit=[Shutdown(reason='THOR task owner stopped')]),
    ]


def generate_launch_description():
    temp = TemporaryDirectory(prefix='thor-navigation-')
    # Keep the files available while the lifecycle nodes are running.
    def launch(context):
        context.launch_configurations['navigation_tmp'] = temp.name
        return navigation_nodes(context)

    return LaunchDescription([
        OpaqueFunction(function=launch),
    ])
