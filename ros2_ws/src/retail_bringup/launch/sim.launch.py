"""Only isolated fake hardware; physical bringup is deliberately a separate deployment."""
import math
from pathlib import Path
import xml.etree.ElementTree as ET
import yaml
import xacro
from ament_index_python.packages import get_package_share_directory as share
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument,OpaqueFunction,IncludeLaunchDescription,RegisterEventHandler,Shutdown
from launch.event_handlers import OnProcessExit
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def robot_description(external_camera):
    path = Path(share('openarm_description'))/'urdf/robot/v10.urdf.xacro'
    xml = xacro.process_file(str(path),mappings={'bimanual':'true','ros2_control':'true','use_fake_hardware':'true'}).toxml()
    root = ET.fromstring(xml)
    for element in root.iter():
        for key in ('name','link'):
            if element.get(key)=='world':
                element.set(key,'base_link')
    base = next(e for e in root.findall('link') if e.get('name')=='base_link')
    visual = ET.SubElement(base,'visual')
    ET.SubElement(visual,'origin',xyz='0 0 0.12')
    ET.SubElement(ET.SubElement(visual,'geometry'),'box',size='0.65 0.7 0.24')
    ET.SubElement(ET.SubElement(visual,'material',name='platform_gray'),'color',rgba='0.22 0.28 0.33 1')

    def fixed(parent,child,xyz,rpy='0 0 0'):
        ET.SubElement(root,'link',name=child)
        joint = ET.SubElement(root,'joint',name=child+'_mount',type='fixed')
        ET.SubElement(joint,'parent',link=parent)
        ET.SubElement(joint,'child',link=child)
        ET.SubElement(joint,'origin',xyz=xyz,rpy=rpy)
    for camera,y in (('chest',0.0),('left_shoulder',0.3),('right_shoulder',-0.3)):
        fixed('base_link',camera+'_link',f'0.15 {y} 1.0')
        if not external_camera:
            fixed(camera+'_link',camera+'_color_optical_frame','0 0 0','-1.57079632679 0 -1.57079632679')
    fixed('openarm_left_hand','left_wrist_link','0 0 0.04')
    if not external_camera:
        fixed('left_wrist_link','left_wrist_color_optical_frame','0 0 0')
    # Assert the URDF cannot select a physical hardware plugin.
    plugins = [e.text for e in root.findall('.//hardware/plugin')]
    if not plugins or any(p!='mock_components/GenericSystem' for p in plugins):
        raise RuntimeError(f'Non-fake hardware rejected: {plugins}')
    return ET.tostring(root,encoding='unicode')


def start(context):
    package = Path(share('retail_bringup'))
    upstream = Path(share('openarm_bimanual_moveit_config'))
    value = lambda n:LaunchConfiguration(n).perform(context)
    external_camera = value('camera_source')=='external'
    if value('camera_source') not in ('synthetic','video','external'):
        raise ValueError('camera_source must be synthetic, video or external')
    robot = robot_description(external_camera)
    semantic = (upstream/'config/openarm_bimanual.srdf').read_text()
    kinematics = yaml.safe_load((upstream/'config/kinematics.yaml').read_text())
    joint_limits = yaml.safe_load((upstream/'config/joint_limits.yaml').read_text())
    controllers = yaml.safe_load((upstream/'config/moveit_controllers.yaml').read_text())
    ompl = {'planning_plugin':'ompl_interface/OMPLPlanner',
            'request_adapters':'default_planner_request_adapters/AddTimeOptimalParameterization default_planner_request_adapters/ResolveConstraintFrames default_planner_request_adapters/FixWorkspaceBounds default_planner_request_adapters/FixStartStateBounds default_planner_request_adapters/FixStartStateCollision default_planner_request_adapters/FixStartStatePathConstraints',
            'start_state_max_bounds_error':0.1,
            'planner_configs':{'RRTConnectkConfigDefault':{'type':'geometric::RRTConnect','range':0.0}}}
    for group in ('left_arm','right_arm','left_gripper','right_gripper'):
        ompl[group] = {'planner_configs':['RRTConnectkConfigDefault']}
    moveit = {'robot_description':robot,'robot_description_semantic':semantic,
              'robot_description_kinematics':kinematics,'robot_description_planning':joint_limits,
              'planning_pipelines':['ompl'],'default_planning_pipeline':'ompl','ompl':ompl,
              'publish_robot_description':True,'publish_robot_description_semantic':True,
              'publish_planning_scene':True,'publish_geometry_updates':True,
              'publish_state_updates':True,'publish_transforms_updates':True,
              'allow_trajectory_execution':True,**controllers}
    nodes = [
        Node(package='robot_state_publisher',executable='robot_state_publisher',parameters=[{'robot_description':robot}],output='screen'),
        Node(package='controller_manager',executable='ros2_control_node',namespace='hardware',
             parameters=[{'robot_description':robot},str(package/'config/controllers.yaml')],
             remappings=[('/hardware/joint_states','/joint_states')],output='screen'),
        Node(package='controller_manager',executable='spawner',
             arguments=['joint_state_broadcaster','left_joint_trajectory_controller','right_joint_trajectory_controller',
                        'left_gripper_controller','right_gripper_controller','-c','/hardware/controller_manager',
                        '--controller-manager-timeout','30'],output='screen'),
        Node(package='moveit_ros_move_group',executable='move_group',parameters=[moveit],output='screen'),
        Node(package='retail_core',executable='fake_base',output='screen'),
        Node(package='retail_core',executable='base_gate',output='screen',
             parameters=[{'required_cameras':value('cameras').split(','),'allow_synthetic':value('vqa_backend')=='fixture','stow_positions':[0.0]*14}]),
        Node(package='retail_core',executable='arm_gate',parameters=[{'allow_synthetic':True}],output='screen'),
        Node(package='retail_core',executable='scene_manager',parameters=[{'backend':value('scene_backend')}],output='screen'),
        Node(package='retail_core',executable='view_planner',output='screen'),
        Node(package='retail_core',executable='mission_manager',output='screen'),
        IncludeLaunchDescription(PythonLaunchDescriptionSource(str(package/'launch/perception.launch.py')),launch_arguments={
            'vqa_backend':value('vqa_backend'),'hand_backend':value('hand_backend'),
            'vqa_mode':value('vqa_mode'),
            'cameras':value('cameras'),'worker_url':value('worker_url'),
            'input_source':value('camera_source'),
            'reconstruction_url':value('reconstruction_url')}.items()),
    ]
    if not external_camera:
        nodes.append(Node(package='retail_core',executable='camera_source',output='screen',
                          parameters=[{'video_path':value('video_path') if value('camera_source')=='video' else ''}]))
    if value('navigation')=='true':
        params = str(package/'config/nav2.yaml')
        nodes.extend([
            Node(package='nav2_map_server',executable='map_server',name='map_server',
                 parameters=[params,{'yaml_filename':str(package/'maps/shop.yaml')}],output='screen'),
            Node(package='nav2_planner',executable='planner_server',name='planner_server',parameters=[params],output='screen'),
            Node(package='nav2_controller',executable='controller_server',name='controller_server',
                 parameters=[params],remappings=[('cmd_vel','/navigation/cmd_vel')],output='screen'),
            Node(package='nav2_bt_navigator',executable='bt_navigator',name='bt_navigator',
                 parameters=[params,{'default_nav_to_pose_bt_xml':str(package/'config/navigate.xml'),
                                     'default_nav_through_poses_bt_xml':str(package/'config/navigate_through.xml')}],output='screen'),
            Node(package='nav2_lifecycle_manager',executable='lifecycle_manager',name='lifecycle_manager_navigation',
                 parameters=[{'autostart':True,'node_names':['map_server','planner_server','controller_server','bt_navigator']}],output='screen'),
        ])
    if value('rviz')=='true':
        nodes.append(Node(package='rviz2',executable='rviz2',arguments=['-d',str(package/'rviz/retail.rviz')],
                          parameters=[moveit],output='screen'))
    # Critical process exits stop the entire fake system; no implicit respawn or goal replay.
    for node in nodes[:2]+nodes[3:10]:
        nodes.append(RegisterEventHandler(OnProcessExit(target_action=node,on_exit=[Shutdown(reason='critical integration process exited')])) )
    return nodes


def generate_launch_description():
    defaults = {'rviz':'false','navigation':'true','vqa_backend':'fixture','hand_backend':'fixture','vqa_mode':'vqa-only',
                'camera_source':'synthetic','scene_backend':'known_fixture','video_path':'','cameras':'chest',
                'worker_url':'http://vqa-worker:8090/infer','reconstruction_url':''}
    return LaunchDescription([*(DeclareLaunchArgument(k,default_value=v) for k,v in defaults.items()),OpaqueFunction(function=start)])

