"""ROS image adapter; emits the existing Future Risk person_tracks contract."""
import json
from pathlib import Path
import threading
import time

import cv2
from cv_bridge import CvBridge
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CompressedImage, Image
from std_msgs.msg import Bool, String

from future_risk_vqa.qos import pipeline_pause_qos
from .perception import LightweightPerception, draw_tracks, track_report


class PersonTrackerNode(Node):
    def __init__(self):
        super().__init__('lightweight_person_tracker')
        defaults = dict(model_path='/workspace/yolov8n.pt', device='cpu', image_size=640,
            confidence=0.1, track_high_threshold=0.35, track_buffer=15, inference_hz=5.0,
            max_gap_s=1.0, temporal_mode='heuristic', lstm_checkpoint='', sequence_length=16,
            sample_hz=5.0, temporal_max_gap_s=0.6, temporal_confidence=0.6, cpu_threads=4,
            allow_download=False, left_image_topic='/left_camera/rgb',
            right_image_topic='/right_camera/rgb', use_compressed_image=False,
            max_image_age_s=2.0, publish_debug_snapshots=False, record_tracks_path='')
        for key, value in defaults.items():
            self.declare_parameter(key, value)
        value = lambda key: self.get_parameter(key).value
        self.bridge = CvBridge()
        self.compressed = bool(value('use_compressed_image'))
        self.debug = bool(value('publish_debug_snapshots'))
        self.max_image_age_s = float(value('max_image_age_s'))
        self.engine = LightweightPerception(**{key: value(key) for key in (
            'model_path', 'device', 'image_size', 'confidence', 'track_high_threshold',
            'track_buffer', 'inference_hz', 'max_gap_s', 'temporal_mode', 'lstm_checkpoint',
            'sequence_length', 'sample_hz', 'temporal_max_gap_s', 'temporal_confidence',
            'cpu_threads', 'allow_download')})
        self.record = None
        record = str(value('record_tracks_path'))
        if record:
            Path(record).parent.mkdir(parents=True, exist_ok=True)
            self.record = open(record, 'x', buffering=1)
        self.latest, self.used = {}, {}
        self.lock, self.busy, self.paused = threading.Lock(), False, False
        self.worker = None
        self.tracks_pub = self.create_publisher(String, '/future_risk/person_tracks', 10)
        # Legacy status/preview topic names keep the existing UI and benchmark compatible.
        self.status_pub = self.create_publisher(String, '/future_risk/sam3_status', 10)
        self.preview = {side: self.create_publisher(CompressedImage,
            f'/future_risk/{side}/sam3/compressed', qos_profile_sensor_data)
            for side in ('left', 'right')}
        self.snapshots = {side: self.create_publisher(CompressedImage,
            f'/future_risk/vqa_input/{side}_now/compressed', qos_profile_sensor_data)
            for side in ('left', 'right')} if self.debug else {}
        msg_type = CompressedImage if self.compressed else Image
        for side in ('left', 'right'):
            self.create_subscription(msg_type, str(value(side + '_image_topic')),
                lambda msg, camera=side: self._receive(camera, msg), qos_profile_sensor_data)
        self.create_subscription(Bool, '/future_risk/pipeline_paused', self._pause, pipeline_pause_qos())
        self.create_timer(1.0 / float(value('inference_hz')), self._start)
        self.get_logger().info('YOLO + ByteTrack ready; temporal=' + str(value('temporal_mode')))

    def _receive(self, side, msg):
        try:
            stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
            frame = (cv2.imdecode(np.frombuffer(msg.data, np.uint8), cv2.IMREAD_COLOR)
                     if self.compressed else self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8'))
            if frame is None:
                raise ValueError('Image decode returned no frame')
            with self.lock:
                self.latest[side] = (stamp, frame, time.monotonic())
        except Exception as exc:
            self.get_logger().error(f'{side} image decode: {exc}')

    def _pause(self, msg):
        with self.lock:
            self.paused = bool(msg.data)

    def _start(self):
        with self.lock:
            if self.paused or self.busy or len(self.latest) != 2:
                return
            if any(self.used.get(side) == item[0] for side, item in self.latest.items()):
                return
            if any(time.monotonic() - item[2] > self.max_image_age_s for item in self.latest.values()):
                self.status_pub.publish(String(data=json.dumps({'state': 'stale', 'backend': 'yolo_bytetrack'})))
                return
            frames = {side: (stamp, frame.copy()) for side, (stamp, frame, _) in self.latest.items()}
            self.used = {side: item[0] for side, item in frames.items()}
            self.busy = True
        self.worker = threading.Thread(target=self._process, args=(frames,), daemon=True)
        self.worker.start()

    def _process(self, frames):
        try:
            cameras = {}
            for side, (stamp, frame) in frames.items():
                cameras[side] = self.engine.process(side, stamp, frame)
                if cameras[side] is None:
                    raise ValueError('Duplicate input frame')
                ok, encoded = cv2.imencode('.jpg', draw_tracks(frame, cameras[side]))
                if ok:
                    image = CompressedImage(format='jpeg', data=encoded.tobytes())
                    image.header.stamp.sec = int(stamp)
                    image.header.stamp.nanosec = int((stamp - int(stamp)) * 1e9)
                    self.preview[side].publish(image)
                    if self.debug:
                        self.snapshots[side].publish(image)
            report = track_report(cameras, self.engine.model_path)
            encoded = json.dumps(report)
            if self.record:
                self.record.write(encoded + '\n')
            self.tracks_pub.publish(String(data=encoded))
            self.status_pub.publish(String(data=json.dumps({'state': 'ready',
                'backend': 'yolo_bytetrack', 'temporal_mode': self.engine.temporal.mode,
                'inference_ms': report['inference_ms']})))
        except Exception as exc:
            self.get_logger().error(f'Lightweight perception: {exc}')
            self.status_pub.publish(String(data=json.dumps({'state': 'error', 'error': str(exc)})))
        finally:
            with self.lock:
                self.busy = False

    def close(self):
        if self.worker:
            self.worker.join(timeout=10)
        if self.record and not self.busy:
            self.record.close()


def main(args=None):
    rclpy.init(args=args)
    node = PersonTrackerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.close()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
