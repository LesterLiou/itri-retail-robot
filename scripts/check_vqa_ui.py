#!/usr/bin/env python3
"""Read-only acceptance of the ROS monitor, actual YOLO evidence and model output."""
import argparse
import json
import os
import re
import subprocess
import time
import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from retail_interfaces.msg import RiskAdvisory

parser=argparse.ArgumentParser();parser.add_argument('--source',choices=('video','external'),required=True)
args=parser.parse_args()
rclpy.init();node=Node('check_ros_vqa_monitor');data={}
node.create_subscription(RiskAdvisory,'/perception/vqa/chest',lambda m:data.update(risk=m),10)
node.create_subscription(String,'/perception/vqa/chest/details',lambda m:data.update(details=json.loads(m.data)),10)
deadline=time.monotonic()+45
try:
    while time.monotonic()<deadline:
        rclpy.spin_once(node,timeout_sec=.05)
        risk=data.get('risk');details=data.get('details',{})
        if not risk or not risk.valid or risk.synthetic or not risk.source_id.startswith(args.source+':'):continue
        stamp=risk.header.stamp.sec+risk.header.stamp.nanosec*1e-9
        if details.get('source_id')!=risk.source_id or abs(details.get('stamp',-1)-stamp)>1e-6:continue
        if details.get('inference_mode')!='yolo-assisted':continue
        assert details['metrics']['bbox_source']=='yolo' and details['metrics']['segmentation_enabled'] is False
        assert details['yolo_hints'] is not None
        assert len(risk.model_id)==64 and risk.raw_response
        windows=subprocess.check_output(['xwininfo','-root','-tree'],text=True)
        match=re.search(r'^\s*(0x[0-9a-f]+).*ROS 2 Monitor',windows,re.M)
        if not match:continue
        # Allow the monitor's 100 ms timer to paint received ROS callbacks.
        until=time.monotonic()+.2
        while time.monotonic()<until:rclpy.spin_once(node,timeout_sec=.02)
        subprocess.run(['import','-display',os.environ['DISPLAY'],'-window',match[1],'/tmp/vqa-ros-ui.png'],check=True)
        print('PASS ROS VQA UI:',json.dumps(dict(source=args.source,action=risk.action,
            model_id=risk.model_id,mode=details['inference_mode'],capture_stamp=stamp,
            people=len(details['metrics']['person_boxes']),inference_ms=details['inference_ms'])),flush=True)
        break
    else:raise RuntimeError('No valid YOLO-assisted ROS evidence and native monitor window before timeout')
finally:
    node.destroy_node();rclpy.shutdown()
