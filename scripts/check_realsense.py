#!/usr/bin/env python3
"""Read-only acceptance of live RealSense RGB -> actual VQA; never sends goals."""
import argparse
import json
import math
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image, CameraInfo
from retail_interfaces.msg import RiskAdvisory, GateState


class Check(Node):
    def __init__(self):
        super().__init__('realsense_vqa_acceptance')
        self.images = 0
        self.image = self.info = self.risk = self.gate = self.annotated = None
        self.create_subscription(Image, '/cameras/chest/color/image_raw', self.on_image, qos_profile_sensor_data)
        self.create_subscription(CameraInfo, '/cameras/chest/color/camera_info', lambda m: setattr(self, 'info', m), qos_profile_sensor_data)
        self.create_subscription(Image, '/perception/vqa/chest/annotated', lambda m: setattr(self, 'annotated', m), qos_profile_sensor_data)
        self.create_subscription(RiskAdvisory, '/perception/vqa/chest', lambda m: setattr(self, 'risk', m), 10)
        self.create_subscription(GateState, '/base/gate_state', lambda m: setattr(self, 'gate', m), 10)

    def on_image(self, message):
        self.images += 1
        self.image = message

    def ready(self, mode):
        if not self.risk or not self.gate:
            return False
        assert not self.gate.enabled, 'Acceptance requires disabled fake robot'
        assert self.gate.base_standstill and self.gate.arms_stowed, 'Expected measured fake stow/standstill'
        assert not self.risk.synthetic, 'Actual VQA must never use fixture observations'
        assert self.risk.source_id.startswith('external:'), self.risk.source_id
        if mode == 'loss':
            return self.risk.action == 'DATA_LOSS' and not self.risk.valid and 'expired' in self.risk.reason
        if self.images < 15 or not self.info or not self.annotated or not self.risk.valid:
            return False
        age = (self.get_clock().now().nanoseconds - self.risk.header.stamp.sec*10**9 - self.risk.header.stamp.nanosec)/1e9
        if not 0 <= age < 2:
            return False
        assert self.image.header.frame_id == self.info.header.frame_id == self.risk.header.frame_id
        assert self.image.width == self.info.width and self.image.height == self.info.height
        assert all(math.isfinite(v) for v in self.info.k) and self.info.k[0] > 0 and self.info.k[4] > 0
        assert self.image.encoding in ('rgb8', 'bgr8')
        assert self.count_publishers('/cameras/chest/color/image_raw') == 1, 'Duplicate RGB publishers'
        assert len(self.risk.model_id) == 64 and len(self.risk.prompt_id) == 64
        assert self.risk.raw_response and self.risk.action in ('STOP', 'DANGER_WARNING', 'CONTINUE', 'OBSERVE')
        return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('stream', 'loss'), default='stream', nargs='?')
    parser.add_argument('--timeout', type=float, default=60)
    args = parser.parse_args()
    rclpy.init()
    node = Check()
    try:
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.1)
            if node.ready(args.mode):
                risk = node.risk
                print('PASS RealSense '+args.mode+': '+json.dumps(dict(
                    frames=node.images, frame=risk.header.frame_id, action=risk.action,
                    valid=risk.valid, reason=risk.reason, model_id=risk.model_id,
                    prompt_id=risk.prompt_id, source=risk.source_id,
                    observation=risk.observation, explanation=risk.explanation,
                    robot_enabled=node.gate.enabled), ensure_ascii=False), flush=True)
                return
        raise RuntimeError(f'Timeout: frames={node.images}, info={node.info is not None}, '
                           f'annotated={node.annotated is not None}, '
                           f'action={node.risk.action if node.risk else None}')
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
