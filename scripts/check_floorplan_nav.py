#!/usr/bin/env python3
"""Route around the user's wall in the isolated matched fake map/scan world."""
import json
import math
import os
import time
from pathlib import Path
import rclpy
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import OccupancyGrid,Path as NavPath
from rclpy.qos import QoSProfile,DurabilityPolicy
from accept_sim import Acceptance
from retail_core.world import load_world,footprint_clear

assert os.environ.get('CAMERA_SOURCE','synthetic')=='synthetic'
assert os.environ.get('VQA_BACKEND','fixture')=='fixture'
assert os.environ.get('CONTROL_MODE','mission')=='mission'
world=load_world(os.environ['SIM_WORLD_FILE'])
rclpy.init();node=Acceptance();data={}
qos=QoSProfile(depth=1,durability=DurabilityPolicy.TRANSIENT_LOCAL)
node.create_subscription(OccupancyGrid,'/map',lambda m:data.update(map=m),qos)
node.create_subscription(NavPath,'/plan',lambda m:data.update(plan=m),10)
pub=node.create_publisher(PoseStamped,'/mission/goal_pose',10)
try:
    node.until(lambda:node.odom and node.gate and node.gate.arms_stowed and 'map' in data and pub.get_subscription_count(),timeout=20)
    assert math.hypot(node.odom.pose.pose.position.x-.8,node.odom.pose.pose.position.y-.7)<.01
    assert (data['map'].info.width,data['map'].info.height)==(134,90)
    goal=PoseStamped();goal.header.frame_id='map';goal.header.stamp=node.get_clock().now().to_msg()
    goal.pose.position.x,goal.pose.position.y=5.6,3.7;goal.pose.orientation.w=1.0
    stamp=goal.header.stamp.sec+goal.header.stamp.nanosec/1e9;pub.publish(goal)
    node.until(lambda:abs((node.state.get('request_stamp') or -1)-stamp)<1e-6)
    deadline=time.monotonic()+100;samples=0;left_passage=False;maximum_y=.7
    while time.monotonic()<deadline:
        rclpy.spin_once(node,timeout_sec=.03)
        p=node.odom.pose.pose; q=p.orientation
        yaw=2*math.atan2(q.z,q.w)
        assert footprint_clear(p.position.x,p.position.y,yaw,world['bounds'],world['obstacles']),f'Footprint intersects design wall at {p}'
        samples+=1;maximum_y=max(maximum_y,p.position.y)
        if p.position.x<1.2 and 1.8<p.position.y<2.6:left_passage=True
        if node.state.get('phase') in ('DONE','HOLD','FAULT'):break
    assert node.state.get('phase')=='DONE',node.state
    node.until(lambda:node.stopped() and not node.gate.enabled)
    p=node.odom.pose.pose.position
    assert math.hypot(p.x-5.6,p.y-3.7)<.08 and left_passage
    assert data.get('plan') and data['plan'].poses
    print('PASS: matched map/scan, one task-owned goal routes via left passage, conservative footprint never intersects walls, DONE and stopped',json.dumps(dict(samples=samples,x=p.x,y=p.y,maximum_y=maximum_y,phase=node.state['phase'])),flush=True)
finally:
    if node.gate and node.gate.enabled:
        try:node.call('/mission/cancel')
        except Exception:pass
    node.destroy_node();rclpy.shutdown()
