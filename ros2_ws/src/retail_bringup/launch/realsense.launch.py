from pathlib import Path
import yaml
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument,OpaqueFunction,IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def start(context):
    cameras = yaml.safe_load(Path(LaunchConfiguration('config').perform(context)).read_text())['cameras']
    enabled = [(name,cfg) for name,cfg in cameras.items() if cfg.get('enabled')]
    if not enabled:
        raise ValueError('Enable at least one RealSense camera')
    if len(enabled)>1 and any(not cfg.get('serial_no') for _,cfg in enabled):
        raise RuntimeError('Every enabled camera requires its own serial_no in multi-camera mode')
    serials = [str(cfg.get('serial_no','')).lstrip('_') for _,cfg in enabled if cfg.get('serial_no')]
    if len(serials)!=len(set(serials)):
        raise ValueError('Enabled cameras must have unique serial numbers')
    launch = str(Path(get_package_share_directory('realsense2_camera'))/'launch'/'rs_launch.py')
    actions = []
    for name,cfg in enabled:
        if name not in ('chest','left_shoulder','right_shoulder','left_wrist'):
            raise ValueError('Unknown camera name')
        serial = str(cfg.get('serial_no','')).lstrip('_')
        # rs_launch parses launch arguments as YAML; '_' keeps numeric serials strings.
        serial_arg = '_'+serial if serial else "''"
        actions.append(IncludeLaunchDescription(PythonLaunchDescriptionSource(launch),launch_arguments={
            'camera_namespace':'cameras','camera_name':name,'serial_no':serial_arg,
            'enable_color':'true','enable_depth':'false','enable_infra1':'false','enable_infra2':'false',
            'rgb_camera.color_profile':str(cfg.get('color_profile','640x480x15')),'publish_tf':'true',
        }.items()))
    return actions


def generate_launch_description():
    default = str(Path(get_package_share_directory('retail_bringup'))/'config'/'cameras.example.yaml')
    return LaunchDescription([DeclareLaunchArgument('config',default_value=default),OpaqueFunction(function=start)])

