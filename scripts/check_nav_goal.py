#!/usr/bin/env python3
"""Isolated fixture regression of the task-owned RViz map goal interface."""
import math
import time
import rclpy
from geometry_msgs.msg import PoseStamped
from accept_sim import Acceptance

rclpy.init();node=Acceptance()
publisher=node.create_publisher(PoseStamped,'/mission/goal_pose',10)

def goal(x,y,frame='map'):
    msg=PoseStamped();msg.header.frame_id=frame;msg.header.stamp=node.get_clock().now().to_msg()
    msg.pose.position.x,msg.pose.position.y=x,y;msg.pose.orientation.w=1.0
    return msg

def stamp(msg):return msg.header.stamp.sec+msg.header.stamp.nanosec*1e-9

def accepted(msg):
    publisher.publish(msg)
    node.until(lambda:abs((node.state.get('request_stamp') or -1)-stamp(msg))<1e-6)

def rejected(msg):
    publisher.publish(msg)
    node.until(lambda:abs((node.state.get('last_goal_rejection') or {}).get('stamp',-1)-stamp(msg))<1e-6)

try:
    node.until(lambda:publisher.get_subscription_count()>0 and node.gate and node.odom and node.risk and node.gate.arms_stowed)
    first=goal(0.8,0.3);accepted(first);owner=node.state['mission_id']
    rejected(goal(1.0,-0.2));assert node.state['mission_id']==owner
    rejected(first);assert node.state['mission_id']==owner
    rejected(goal(0.0,0.0,'odom'));assert node.state['mission_id']==owner
    node.until(lambda:math.hypot(node.odom.pose.pose.position.x,node.odom.pose.pose.position.y)>0.03,timeout=30)
    node.call('/mission/cancel')
    node.until(lambda:node.state.get('phase')=='HOLD' and node.gate.hold and node.stopped())
    position=node.odom.pose.pose.position
    frozen=(position.x,position.y)
    end=time.monotonic()+1.0
    while time.monotonic()<end:
        rclpy.spin_once(node,timeout_sec=.05)
        assert not node.gate.enabled
        assert math.hypot(node.odom.pose.pose.position.x-frozen[0],node.odom.pose.pose.position.y-frozen[1])<0.01
    node.call('/mission/reset')
    node.until(lambda:node.state.get('phase')=='DONE' and not node.gate.hold)
    rejected(first)
    assert not node.gate.enabled
    last=goal(0.6,0.2);accepted(last)
    node.until(lambda:node.state.get('phase') in ('DONE','HOLD','FAULT'),timeout=45)
    assert node.state['phase']=='DONE',node.state
    assert not node.gate.enabled and node.stopped()
    print('PASS: task-owned map goal, conflicting/duplicate/invalid goals rejected; cancel/hold/reset never replay old goal; fresh goal completed',node.state,flush=True)
finally:
    node.destroy_node();rclpy.shutdown()
