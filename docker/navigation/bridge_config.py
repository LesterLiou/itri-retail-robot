"""Construct the allowlist for a particular pair of local and remote domains."""
import os
from tempfile import NamedTemporaryFile
import yaml


def main():
    local = int(os.environ['ROS_DOMAIN_ID'])
    remote = int(os.environ['NAV_DOMAIN_ID'])
    if local == remote:
        raise SystemExit('Motion and navigation require distinct ROS domains')
    topics = {
        'drive/request': {'type': 'retail_interfaces/msg/DriveRequest', 'reversed': True,
                          'qos': {'depth': 1}},
        'drive/velocity': {'type': 'retail_interfaces/msg/DriveRequest', 'reversed': True,
                           'qos': {'reliability': 'best_effort', 'depth': 1}},
        'perception/vqa/tidybot': {'type': 'retail_interfaces/msg/RiskAdvisory', 'reversed': True,
                                   'qos': {'reliability': 'best_effort', 'depth': 5}},
        'drive/state': {'type': 'retail_interfaces/msg/DriveState', 'qos': {'depth': 1}},
        'base/gate_state': {'type': 'retail_interfaces/msg/GateState'},
        'tidybot/drive_marker': {'type': 'visualization_msgs/msg/Marker'},
        'odom': {'type': 'nav_msgs/msg/Odometry', 'qos': {'depth': 5}},
        'scan': {'type': 'sensor_msgs/msg/LaserScan', 'qos': {'reliability': 'best_effort', 'depth': 5}},
        'tf': {'type': 'tf2_msgs/msg/TFMessage', 'qos': {'reliability': 'reliable', 'depth': 30}},
        'tf_static': {'type': 'tf2_msgs/msg/TFMessage', 'qos': {'durability': 'transient_local',
                                                              'reliability': 'reliable', 'depth': 10}},
    }
    config = {'name': 'tidybot_navigation_bridge', 'from_domain': local, 'to_domain': remote,
              'topics': topics}
    with NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as file:
        yaml.safe_dump(config, file)
    os.execvp('ros2', ['ros2', 'run', 'domain_bridge', 'domain_bridge', file.name])


if __name__ == '__main__':
    main()
