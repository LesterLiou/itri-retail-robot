#!/usr/bin/env python3
"""Actual camera/model → task-owned Nav2 → isolated kinematic base. No real motors."""
import json
import math
import os
import time
import rclpy
from geometry_msgs.msg import PoseStamped
from accept_sim import Acceptance

assert os.environ.get('CAMERA_SOURCE')=='external' and os.environ.get('VQA_BACKEND')=='http'
rclpy.init();node=Acceptance();publisher=node.create_publisher(PoseStamped,'/mission/goal_pose',10)
try:
    node.until(lambda:'fake_tidybot' in node.get_node_names() and node.state.get('runtime')=='simulation' and
        node.odom and node.gate and node.gate.base_standstill and node.gate.arms_stowed and node.risk and
        not node.risk.synthetic and node.risk.valid and node.risk.action=='CONTINUE',timeout=60)
    start=(node.odom.pose.pose.position.x,node.odom.pose.pose.position.y)
    print('Explicit test reset of stopped FAKE base, followed by one fresh map goal; no model output is altered',flush=True)
    node.call('/mission/reset')
    node.until(lambda:node.state.get('phase')=='DONE' and not node.gate.hold)
    node.until(lambda:node.risk.valid and node.risk.action=='CONTINUE')
    node.until(lambda:publisher.get_subscription_count()>0)
    goal=PoseStamped();goal.header.frame_id='map';goal.header.stamp=node.get_clock().now().to_msg()
    goal.pose.position.x,goal.pose.position.y=start[0]+0.4,start[1]
    goal.pose.orientation.w=1.0
    stamp=goal.header.stamp.sec+goal.header.stamp.nanosec*1e-9
    publisher.publish(goal)
    node.until(lambda:abs((node.state.get('request_stamp') or -1)-stamp)<1e-6)
    moved=0.0;seen_permission=False;seen_nonzero=False;actions=set()
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        rclpy.spin_once(node,timeout_sec=.03)
        if node.risk:actions.add(node.risk.action)
        if node.gate and node.gate.enabled:seen_permission=True
        if node.odom:
            p=node.odom.pose.pose.position
            moved=max(moved,math.hypot(p.x-start[0],p.y-start[1]))
            if abs(node.odom.twist.twist.linear.x)+abs(node.odom.twist.twist.linear.y)>0.001:seen_nonzero=True
        if node.state.get('phase') in ('DONE','HOLD','FAULT'):break
    node.until(lambda:node.stopped() and not node.gate.enabled,timeout=10)
    result=dict(navigation_outcome=node.state.get('phase'),moved_metres=round(moved,4),
        motion_permitted=seen_permission,nonzero_odometry=seen_nonzero,actions=sorted(actions),
        reason=node.state.get('reason'),model_id=node.risk.model_id,synthetic=node.risk.synthetic)
    print('Live camera/VQA navigation observation:',json.dumps(result),flush=True)
    assert seen_permission and seen_nonzero and moved>0.01,result
    assert node.state.get('phase') in ('DONE','HOLD'),result
    print('PASS: actual VQA permitted measured fake-base motion; terminal outcome above is reported without forcing clear',flush=True)
finally:
    if node.gate and node.gate.enabled:
        try:node.call('/mission/cancel')
        except Exception:pass
    node.destroy_node();rclpy.shutdown()
