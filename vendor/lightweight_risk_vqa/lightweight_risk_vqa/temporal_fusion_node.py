"""ROS adapter between the unmodified VQA node and existing policy/UI."""
import json

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from .fusion import PredictionHistory, fuse_report


class TemporalFusionNode(Node):
    def __init__(self):
        super().__init__('lightweight_temporal_fusion')
        self.declare_parameter('fusion_mode', 'diagnostic')
        self.declare_parameter('history_retention_s', 180.0)
        self.declare_parameter('alignment_tolerance_s', 0.6)
        self.mode = self.get_parameter('fusion_mode').value
        if self.mode not in {'diagnostic', 'lstm_future'}:
            raise ValueError('fusion_mode must be diagnostic or lstm_future')
        self.history = PredictionHistory(self.get_parameter('history_retention_s').value,
                                         self.get_parameter('alignment_tolerance_s').value)
        self.publisher = self.create_publisher(String, '/future_risk/vlm_events', 10)
        self.create_subscription(String, '/future_risk/person_tracks', self._tracks, 10)
        self.create_subscription(String, '/lightweight_risk/vlm_events_raw', self._semantics, 10)

    def _tracks(self, msg):
        self.history.add(json.loads(msg.data))

    def _semantics(self, msg):
        report = fuse_report(json.loads(msg.data), self.history, self.mode)
        self.publisher.publish(String(data=json.dumps(report)))


def main(args=None):
    rclpy.init(args=args)
    node = TemporalFusionNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
