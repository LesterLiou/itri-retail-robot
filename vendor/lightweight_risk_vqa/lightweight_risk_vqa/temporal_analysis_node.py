"""Publish direct temporal judgments to the monitor without running a VQA node."""
import json

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String

from future_risk_vqa.qos import pipeline_pause_qos
from .direct_temporal import analyze_tracks, temporal_decision


class TemporalAnalysisNode(Node):
    def __init__(self):
        super().__init__('lightweight_temporal_analysis')
        for key, default in dict(temporal_mode='heuristic', temporal_confidence=0.6,
                                 tracking_timeout_s=2.0).items():
            self.declare_parameter(key, default)
        self.mode = self.get_parameter('temporal_mode').value
        if self.mode not in {'heuristic', 'lstm'}:
            raise ValueError('Direct analysis requires heuristic or trained lstm mode')
        self.threshold = self.get_parameter('temporal_confidence').value
        self.timeout = self.get_parameter('tracking_timeout_s').value
        self.report, self.received, self.paused = None, None, False
        self.events = self.create_publisher(String, '/future_risk/vlm_events', 10)
        self.decisions = self.create_publisher(String, '/future_risk/decision', 10)
        self.actions = self.create_publisher(String, '/future_risk/action', 10)
        self.status = self.create_publisher(String, '/future_risk/vqa_status', 10)
        self.create_subscription(String, '/future_risk/person_tracks', self._tracks, 10)
        self.create_subscription(Bool, '/future_risk/pipeline_paused', self._pause, pipeline_pause_qos())
        self.create_timer(0.2, self._heartbeat)

    def _pause(self, msg):
        self.paused = bool(msg.data)

    def _tracks(self, msg):
        try:
            self.report = analyze_tracks(json.loads(msg.data), self.mode, self.threshold)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            self.report = {'availability': 'UNAVAILABLE', 'assessments': [],
                'validation_errors': [str(exc)], 'metadata': {
                    'analysis_mode': 'direct_temporal', 'motion_source': self.mode,
                    'snapshot_stamps_s': {}}}
        self.received = self.get_clock().now().nanoseconds * 1e-9
        self.events.publish(String(data=json.dumps(self.report)))
        self.status.publish(String(data=json.dumps({'state': self.report['availability'].lower(),
            'backend': self.mode, 'analysis_mode': 'direct_temporal', 'llm_used': False})))
        self._heartbeat()

    def _heartbeat(self):
        if self.paused:
            return
        now = self.get_clock().now().nanoseconds * 1e-9
        age = 0.0
        if self.received is not None:
            ages = [now - self.received, *[now - stamp for stamp in self.report['metadata']['snapshot_stamps_s'].values()]]
            age = max(ages) if min(ages) >= -1.0 else float('inf')
        decision = temporal_decision(self.report, now, age, self.timeout)
        self.decisions.publish(String(data=json.dumps(decision)))
        self.actions.publish(String(data=decision['action']))


def main(args=None):
    rclpy.init(args=args)
    node = TemporalAnalysisNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
