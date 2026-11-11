#!/usr/bin/env python3
"""Read-only, time-sampled perception health; never publishes commands."""
import argparse
from collections import Counter
import json
import statistics
import time
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image
from std_msgs.msg import String
from retail_interfaces.msg import RiskAdvisory

parser=argparse.ArgumentParser();parser.add_argument('--seconds',type=float,default=30)
args=parser.parse_args();rclpy.init();node=Node('measure_vqa_health')
data={};latencies={};samples=Counter();ages=[];frames=[0]
def details(msg):
    value=json.loads(msg.data)
    if value.get('inference_ms') is not None:latencies[value.get('stamp')]=value['inference_ms']
def image(msg):data['image']=msg;frames[0]+=1
node.create_subscription(RiskAdvisory,'/perception/vqa/chest',lambda m:data.update(risk=m),10)
node.create_subscription(Image,'/cameras/chest/color/image_raw',image,qos_profile_sensor_data)
node.create_subscription(String,'/perception/vqa/chest/details',details,10)
def stamp(msg):return msg.header.stamp.sec+msg.header.stamp.nanosec/1e9
def sample():
    now=node.get_clock().now().nanoseconds/1e9;risk=data.get('risk');image=data.get('image')
    age=now-stamp(risk) if risk else float('inf')
    valid=bool(risk and risk.valid and 0<=age<=2 and image and 0<=now-stamp(image)<=2)
    samples['valid' if valid else (risk.reason if risk else 'waiting')]+=1
    if risk:ages.append(age)
node.create_timer(.05,sample);end=time.monotonic()+args.seconds
while time.monotonic()<end:rclpy.spin_once(node,timeout_sec=.05)
print(json.dumps(dict(duration_sec=args.seconds,samples=dict(samples),valid_fraction=round(samples['valid']/max(1,sum(samples.values())),4),
    image_fps=round(frames[0]/args.seconds,2),completed_results=len(latencies),
    inference_ms_median=statistics.median(latencies.values()) if latencies else None,
    inference_ms_max=max(latencies.values()) if latencies else None,
    capture_age_median=statistics.median(ages) if ages else None,capture_age_max=max(ages) if ages else None),indent=2))
node.destroy_node();rclpy.shutdown()
