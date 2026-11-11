"""Explicit CLI for the isolated simulation; it never publishes velocity."""
import argparse
import json
import math
import time
import rclpy
from rclpy.node import Node
from std_srvs.srv import Trigger,SetBool
from std_msgs.msg import String
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Odometry
from retail_interfaces.msg import GateState,RiskAdvisory
from .common import seconds


def show_status(node):
    samples={}
    subscriptions=[
        node.create_subscription(String,'/mission/state',lambda m:samples.update(mission=json.loads(m.data)),10),
        node.create_subscription(GateState,'/base/gate_state',lambda m:samples.update(gate=m),10),
        node.create_subscription(RiskAdvisory,'/perception/vqa/chest',lambda m:samples.update(vqa=m),10),
        node.create_subscription(Odometry,'/odom',lambda m:samples.update(odom=m),10),
    ]
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        rclpy.spin_once(node,timeout_sec=.05)
        if len(samples)==4:break
    at=node.get_clock().now().nanoseconds/1e9
    result={'mission':samples.get('mission','unavailable')}
    gate=samples.get('gate')
    result['gate']=dict(enabled=gate.enabled,hold=gate.hold,reason=gate.reason,
        base_standstill=gate.base_standstill,arms_stowed=gate.arms_stowed,
        feedback_age_sec=round(at-seconds(gate.header.stamp),3)) if gate else 'unavailable'
    vqa=samples.get('vqa')
    result['vqa']=dict(action=vqa.action,valid=vqa.valid,reason=vqa.reason,synthetic=vqa.synthetic,
        capture_age_sec=round(at-seconds(vqa.header.stamp),3)) if vqa else 'unavailable'
    odom=samples.get('odom')
    result['odometry']=dict(x=odom.pose.pose.position.x,y=odom.pose.pose.position.y,
        vx=odom.twist.twist.linear.x,vy=odom.twist.twist.linear.y,wz=odom.twist.twist.angular.z) if odom else 'unavailable'
    print(json.dumps(result,indent=2,ensure_ascii=False))
    for subscription in subscriptions:node.destroy_subscription(subscription)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command',choices=['status','navigate','goto','run','place','cancel','reset','obstacle','clear'])
    parser.add_argument('--x',type=float,default=1.0)
    parser.add_argument('--y',type=float,default=0.0)
    parser.add_argument('--yaw',type=float,default=0.0)
    parser.add_argument('--wait',action='store_true',help='Monitor an accepted goto for up to 120 seconds; exiting this monitor does not cancel motion')
    args,ros_args = parser.parse_known_args()
    rclpy.init(args=ros_args)
    node = Node('retail_demo_control')
    if args.command=='status':
        show_status(node);node.destroy_node();rclpy.try_shutdown();return
    if args.command=='goto':
        if not all(math.isfinite(v) for v in (args.x,args.y,args.yaw)):raise ValueError('Finite goal required')
        state={}
        node.create_subscription(String,'/mission/state',lambda m:state.update(json.loads(m.data)),10)
        publisher=node.create_publisher(PoseStamped,'/mission/goal_pose',10)
        end=time.monotonic()+5
        while publisher.get_subscription_count()==0 and time.monotonic()<end:rclpy.spin_once(node,timeout_sec=.05)
        if publisher.get_subscription_count()==0:raise RuntimeError('Mission goal interface unavailable')
        goal=PoseStamped();goal.header.frame_id='map';goal.header.stamp=node.get_clock().now().to_msg()
        goal.pose.position.x,goal.pose.position.y=args.x,args.y
        goal.pose.orientation.z,goal.pose.orientation.w=math.sin(args.yaw/2),math.cos(args.yaw/2)
        stamp=goal.header.stamp.sec+goal.header.stamp.nanosec*1e-9
        publisher.publish(goal)
        end=time.monotonic()+5
        while time.monotonic()<end:
            rclpy.spin_once(node,timeout_sec=.05)
            if abs((state.get('request_stamp') or -1)-stamp)<1e-6:
                print('Accepted map goal (permission/execution still pending):',json.dumps(state),flush=True)
                if args.wait:
                    last=None
                    deadline=time.monotonic()+120
                    while time.monotonic()<deadline:
                        phase=state.get('phase')
                        if phase!=last:
                            print('Phase:',phase,'Reason:',state.get('reason',''),flush=True);last=phase
                        if phase in ('DONE','HOLD','FAULT'):break
                        rclpy.spin_once(node,timeout_sec=.05)
                    else:print('Monitor timed out; the mission was NOT cancelled. Use status / cancel explicitly.',flush=True)
                    show_status(node)
                else:print('Use demo_control status for the outcome, or goto --wait to monitor a new goal.',flush=True)
                node.destroy_node();rclpy.try_shutdown();return
            rejected=state.get('last_goal_rejection') or {}
            if abs(rejected.get('stamp',-1)-stamp)<1e-6:raise RuntimeError(rejected['reason'])
        raise RuntimeError('Goal acknowledgement timed out')
    if args.command in ('obstacle','clear'):
        service,type_ = '/demo/set_obstacle',SetBool
        req = SetBool.Request(data=args.command=='obstacle')
    else:
        service,type_ = '/mission/'+args.command,Trigger
        req = Trigger.Request()
    client = node.create_client(type_,service)
    if not client.wait_for_service(timeout_sec=10):
        raise RuntimeError(f'{service} unavailable')
    future = client.call_async(req)
    rclpy.spin_until_future_complete(node,future,timeout_sec=10)
    if not future.done() or not future.result().success:
        raise RuntimeError(str(future.result()) if future.done() else 'service timed out')
    print(future.result().message)
    node.destroy_node()
    rclpy.try_shutdown()

