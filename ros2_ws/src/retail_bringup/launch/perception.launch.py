from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument,OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def start(context):
    cameras = LaunchConfiguration('cameras').perform(context).split(',')
    return [
        Node(package='retail_core',executable='vqa_adapter',output='screen',
             parameters=[{'backend':LaunchConfiguration('vqa_backend').perform(context),'cameras':cameras,
                          'inference_mode':LaunchConfiguration('vqa_mode').perform(context),
                          'input_source':LaunchConfiguration('input_source').perform(context),
                          'worker_url':LaunchConfiguration('worker_url').perform(context)}]),
        Node(package='retail_core',executable='hand_adapter',output='screen',
             parameters=[{'backend':LaunchConfiguration('hand_backend').perform(context),'input_source':LaunchConfiguration('input_source').perform(context)}]),
        Node(package='retail_core',executable='reconstruction_adapter',output='screen',
             parameters=[{'worker_url':LaunchConfiguration('reconstruction_url').perform(context)}]),
    ]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('vqa_backend',default_value='http'),
        DeclareLaunchArgument('vqa_mode',default_value='vqa-only'),
        DeclareLaunchArgument('hand_backend',default_value='mediapipe'),
        DeclareLaunchArgument('cameras',default_value='chest'),
        DeclareLaunchArgument('input_source',default_value='ros'),
        DeclareLaunchArgument('worker_url',default_value='http://vqa-worker:8090/infer'),
        DeclareLaunchArgument('reconstruction_url',default_value=''),
        OpaqueFunction(function=start)])

