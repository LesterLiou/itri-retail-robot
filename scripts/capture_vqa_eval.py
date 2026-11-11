#!/usr/bin/env python3
"""Read-only capture of six chest RGB frames for paired offline decoding checks."""
import json
from pathlib import Path
import time
import cv2
from cv_bridge import CvBridge
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image
rclpy.init();node=Node('capture_vqa_eval');latest=[None];bridge=CvBridge();records=[]
node.create_subscription(Image,'/cameras/chest/color/image_raw',lambda m:latest.__setitem__(0,m),qos_profile_sensor_data)
target=Path('/tmp/decoder_eval');target.mkdir(exist_ok=True)
deadline=time.monotonic()+15;last=-1e9
while len(records)<6 and time.monotonic()<deadline:
    rclpy.spin_once(node,timeout_sec=.05);msg=latest[0]
    if not msg:continue
    stamp=msg.header.stamp.sec+msg.header.stamp.nanosec/1e9
    if stamp-last<1:continue
    path=target/f'camera_{len(records)}.png';assert cv2.imwrite(str(path),bridge.imgmsg_to_cv2(msg,'bgr8'))
    records.append(dict(file=path.name,group='camera',stamp=stamp));last=stamp
assert len(records)==6
(target/'frames.json').write_text(json.dumps(records,indent=2)+'\n')
print('Captured 6 original RGB frames with original timestamps; no commands sent')
node.destroy_node();rclpy.shutdown()
