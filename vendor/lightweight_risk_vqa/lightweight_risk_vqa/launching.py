"""One launch implementation for videos, Isaac and existing live camera topics."""
import os
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, EmitEvent, OpaqueFunction, RegisterEventHandler
from launch.conditions import IfCondition
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def _validate(context):
    get = lambda name: LaunchConfiguration(name).perform(context)
    if get('temporal_mode') == 'lstm' and not Path(get('lstm_checkpoint')).is_file():
        raise ValueError('LSTM requires a trained lstm_checkpoint file. No pretrained LSTM is bundled; use temporal_mode:=heuristic only for the explicitly labelled rule baseline.')
    vqa = get('enable_vqa').lower() in {'true', '1'}
    direct = get('enable_temporal_analysis').lower() in {'true', '1'} and not vqa
    if get('vqa_input_mode') == 'single_now_lstm' and (not vqa or get('temporal_mode') != 'lstm'):
        raise ValueError('single_now_lstm requires enable_vqa:=true, temporal_mode:=lstm and a trained checkpoint')
    if direct and get('temporal_mode') == 'disabled':
        raise ValueError('Direct temporal analysis requires heuristic or lstm; disable enable_temporal_analysis for preview only.')
    if vqa and get('fusion_mode') == 'lstm_future' and get('temporal_mode') != 'lstm':
        raise ValueError('lstm_future fusion requires temporal_mode:=lstm and a trained checkpoint')
    return []


def make_launch(mode):
    if mode not in {'video', 'live', 'isaac'}:
        raise ValueError('Unknown source mode')
    share = get_package_share_directory('lightweight_risk_vqa')
    arg = LaunchConfiguration
    compressed_default = 'true' if mode == 'live' else 'false'
    left = '/camera/left/color/image_raw/compressed' if mode == 'live' else (
        '/camera/left/color/image_raw' if mode == 'isaac' else '/left_camera/rgb')
    right = left.replace('/left/', '/right/').replace('/left_camera/', '/right_camera/')
    declarations = [
        DeclareLaunchArgument('config', default_value=os.path.join(share, 'config', 'lightweight.yaml')),
        DeclareLaunchArgument('model_path', default_value='/workspace/yolov8n.pt'),
        DeclareLaunchArgument('device', default_value='cpu'),
        DeclareLaunchArgument('temporal_mode', default_value='heuristic', choices=['heuristic', 'lstm', 'disabled']),
        DeclareLaunchArgument('lstm_checkpoint', default_value=''),
        DeclareLaunchArgument('fusion_mode', default_value='diagnostic', choices=['diagnostic', 'lstm_future']),
        DeclareLaunchArgument('vqa_input_mode', default_value='temporal_storyboard',
                              choices=['temporal_storyboard', 'single_now_lstm']),
        DeclareLaunchArgument('left_image_topic', default_value=left),
        DeclareLaunchArgument('right_image_topic', default_value=right),
        DeclareLaunchArgument('use_compressed_image', default_value=compressed_default),
        DeclareLaunchArgument('enable_vqa', default_value='false'),
        DeclareLaunchArgument('enable_temporal_analysis', default_value='true'),
        DeclareLaunchArgument('enable_gui', default_value='true'),
        DeclareLaunchArgument('enable_logger', default_value='true'),
        DeclareLaunchArgument('controller_url', default_value='http://127.0.0.1:11000'),
        DeclareLaunchArgument('model_name', default_value='robopoint_vqa_only_317677'),
        DeclareLaunchArgument('record_tracks_path', default_value=''),
        DeclareLaunchArgument('run_name', default_value='lightweight_' + mode),
        DeclareLaunchArgument('output_root', default_value='/workspace/results/lightweight_risk_vqa'),
    ]
    common = {'use_sim_time': mode == 'isaac'}
    images = {**common, 'left_image_topic': arg('left_image_topic'),
        'right_image_topic': arg('right_image_topic'),
        'use_compressed_image': ParameterValue(arg('use_compressed_image'), value_type=bool)}
    direct = PythonExpression(["'", arg('enable_vqa'), "'.lower() not in ('true','1') and '",
        arg('enable_temporal_analysis'), "'.lower() in ('true','1')"])
    preview_only = PythonExpression(["'", arg('enable_vqa'), "'.lower() not in ('true','1') and '",
        arg('enable_temporal_analysis'), "'.lower() not in ('true','1')"])
    single_now = PythonExpression(["'", arg('vqa_input_mode'), "' == 'single_now_lstm'"])
    nodes = [
        Node(package='lightweight_risk_vqa', executable='person_tracker_node',
            name='lightweight_person_tracker', output='screen', parameters=[arg('config'), images, {
                'model_path': arg('model_path'), 'device': arg('device'),
                'temporal_mode': arg('temporal_mode'), 'lstm_checkpoint': arg('lstm_checkpoint'),
                'record_tracks_path': arg('record_tracks_path'),
                'publish_debug_snapshots': ParameterValue(PythonExpression([
                    "'", arg('enable_vqa'), "'.lower() not in ('true','1')"]), value_type=bool)}]),
        Node(package='future_risk_vqa', executable='ground_trajectory_node',
            name='ground_trajectory', output='screen', parameters=[arg('config'), common]),
        Node(package='future_risk_vqa', executable='future_vqa_node', name='future_vqa',
            output='screen', condition=IfCondition(arg('enable_vqa')),
            parameters=[arg('config'), images, {'backend': 'llava_worker',
                'controller_url': arg('controller_url'), 'model_name': arg('model_name'),
                'storyboard_mode': ParameterValue(PythonExpression([
                    "'current_crop' if '", arg('vqa_input_mode'),
                    "' == 'single_now_lstm' else 'track_crops'"]), value_type=str),
                'temporal_frame_count': ParameterValue(PythonExpression([
                    "1 if '", arg('vqa_input_mode'), "' == 'single_now_lstm' else 3"]), value_type=int),
                'temporal_window_s': ParameterValue(PythonExpression([
                    "0.0 if '", arg('vqa_input_mode'), "' == 'single_now_lstm' else 2.0"]), value_type=float)}],
            remappings=[('/future_risk/vlm_events', '/lightweight_risk/vlm_events_raw')]),
        Node(package='lightweight_risk_vqa', executable='temporal_fusion_node',
            name='lightweight_temporal_fusion', output='screen',
            condition=IfCondition(arg('enable_vqa')),
            parameters=[arg('config'), common, {'fusion_mode': ParameterValue(PythonExpression([
                "'lstm_future' if '", arg('vqa_input_mode'),
                "' == 'single_now_lstm' else '", arg('fusion_mode'), "'"]), value_type=str)}]),
        Node(package='future_risk_vqa', executable='future_policy_node', name='future_risk_policy',
            output='screen', condition=IfCondition(arg('enable_vqa')),
            parameters=[arg('config'), common]),
        Node(package='future_risk_vqa', executable='future_policy_node', name='future_risk_policy',
            output='screen', condition=IfCondition(preview_only),
            parameters=[arg('config'), common, {'decision_mode': 'observe_only'}]),
        Node(package='lightweight_risk_vqa', executable='temporal_analysis_node',
            name='lightweight_temporal_analysis', output='screen', condition=IfCondition(direct),
            parameters=[arg('config'), common, {'temporal_mode': arg('temporal_mode')}]),
        Node(package='future_risk_vqa', executable='experiment_logger_node',
            name='future_risk_experiment_logger', output='screen',
            condition=IfCondition(arg('enable_logger')), parameters=[arg('config'), common, {
                'output_root': arg('output_root'), 'run_name': arg('run_name')}]),
    ]
    gui = Node(package='lightweight_risk_vqa', executable='research_gui',
        name='lightweight_research_gui', output='screen', condition=IfCondition(arg('enable_gui')),
        parameters=[common, {'preview_only': ParameterValue(PythonExpression([
            "'", arg('enable_vqa'), "'.lower() not in ('true','1')"]), value_type=bool),
            'direct_temporal': ParameterValue(direct, value_type=bool),
            'single_now_lstm': ParameterValue(single_now, value_type=bool)}],
        additional_env={'QT_XCB_GL_INTEGRATION': 'none'})
    nodes += [gui, RegisterEventHandler(OnProcessExit(target_action=gui,
        on_exit=[EmitEvent(event=Shutdown(reason='lightweight GUI closed'))]))]
    if mode == 'video':
        declarations += [
            DeclareLaunchArgument('left_video', default_value='/workspace/example/video/0610_take3n4.mp4'),
            DeclareLaunchArgument('right_video', default_value='/workspace/example/video/0610x9_v0629.mp4')]
        nodes.insert(0, Node(package='future_risk_vqa', executable='dual_video_source_node',
            name='future_risk_video_source', output='screen', parameters=[arg('config'), images, {
                'left_video_path': arg('left_video'), 'right_video_path': arg('right_video')}]))
    return LaunchDescription([*declarations, OpaqueFunction(function=_validate), *nodes])
