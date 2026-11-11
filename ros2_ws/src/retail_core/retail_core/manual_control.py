"""Explicit MoveIt control for the exclusive fake-hardware manual profile."""
import argparse
import math
import os
import signal
import time
import uuid
import xml.etree.ElementTree as ET

import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import QoSProfile,DurabilityPolicy,qos_profile_sensor_data
from rclpy.signals import SignalHandlerOptions
from sensor_msgs.msg import JointState
from std_msgs.msg import String
from std_srvs.srv import SetBool,Trigger
from moveit_msgs.action import MoveGroup
from moveit_msgs.msg import Constraints,JointConstraint
from retail_interfaces.msg import GateState,SceneState
from retail_interfaces.srv import ManualLease
from .common import now,seconds,finite_joints


class Control(Node):
    def __init__(self):
        super().__init__('manual_moveit_control')
        self.gate=self.scene=self.joints=self.robot=None
        self.goal=None
        self.acceptance_live=False
        self.verified=False
        self.owner_id=uuid.uuid4().hex
        self.lease_active=False
        self.heartbeat=self.create_publisher(String,'/manual/heartbeat',1)
        self.create_timer(0.05,lambda:self.heartbeat.publish(String(data=self.owner_id)) if self.lease_active else None)
        self.previous=None
        self.still_since=None
        self.create_subscription(GateState,'/base/gate_state',lambda m:setattr(self,'gate',m),10)
        self.create_subscription(SceneState,'/perception/shelf/state',lambda m:setattr(self,'scene',m),10)
        self.create_subscription(JointState,'/joint_states',self.feedback,qos_profile_sensor_data)
        self.create_subscription(String,'/robot_description',lambda m:setattr(self,'robot',m.data),
            QoSProfile(depth=1,durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.move=ActionClient(self,MoveGroup,'/move_action')

    def feedback(self,msg):
        values=dict(zip(msg.name,msg.position))
        steady=False
        if self.previous and finite_joints(msg,list(values)):
            old,t=self.previous;dt=seconds(msg.header.stamp)-t
            steady=values.keys()==old.keys() and dt>0 and all(abs(values[n]-old[n])/dt<0.02 for n in values)
        self.still_since=(self.still_since or time.monotonic()) if steady else None
        self.previous=(values,seconds(msg.header.stamp))
        self.joints=msg

    def stopped(self):
        return bool(self.joints and 0<=now(self)-seconds(self.joints.header.stamp)<0.5 and
                    self.still_since and time.monotonic()-self.still_since>=0.3)

    def until(self,predicate,timeout=15,guard=None):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            rclpy.spin_once(self,timeout_sec=0.03)
            if guard:guard()
            if predicate():return
        raise RuntimeError('Timed out waiting for current ROS feedback')

    def call(self,name,kind,value=None):
        client=self.create_client(kind,name)
        if not client.wait_for_service(timeout_sec=5):raise RuntimeError('Missing '+name)
        request=kind.Request()
        if value is not None:request.data=value
        future=client.call_async(request)
        self.until(future.done)
        result=future.result()
        self.destroy_client(client)
        if not result.success:raise RuntimeError(name+': '+result.message)
        return result

    def check_profile(self):
        if os.environ.get('CONTROL_MODE')!='moveit':
            raise RuntimeError('Start make moveit first; mission and manual modes are exclusive')
        self.until(lambda:self.robot and self.gate and self.joints and self.scene)
        plugins=[p.text for p in ET.fromstring(self.robot).findall('.//hardware/plugin')]
        if not plugins or any(p!='mock_components/GenericSystem' for p in plugins):
            raise RuntimeError('Only verified fake hardware is permitted')
        if '/mission/run' in dict(self.get_service_names_and_types()):
            raise RuntimeError('Mission manager is running; manual control refused')
        self.verified=True

    def prepare(self):
        self.check_profile()
        if not self.lease_active:self.set_lease(True)
        self.call('/base_gate/inhibit',SetBool,True)
        self.until(lambda:self.gate.base_inhibited and self.gate.base_standstill and self.stopped())
        self.call('/arm_gate/authorize',SetBool,False)
        previous_revision=self.scene.revision
        self.call('/scene/scan',Trigger)
        self.until(lambda:self.scene and self.scene.revision>previous_revision and self.scene.valid and self.scene.metric_valid and
            0<=now(self)-seconds(self.scene.header.stamp)<2)
        self.call('/arm_gate/authorize',SetBool,True)
        print('Prepared fresh fake scene revision',self.scene.revision,'; base inhibited, measured stopped; existing scene TTL applies',flush=True)

    def set_lease(self,acquire):
        client=self.create_client(ManualLease,'/manual/lease')
        if not client.wait_for_service(timeout_sec=5):raise RuntimeError('Manual watchdog unavailable')
        future=client.call_async(ManualLease.Request(owner_id=self.owner_id,acquire=acquire))
        self.until(future.done)
        result=future.result();self.destroy_client(client)
        if not result.success:raise RuntimeError(result.message)
        self.lease_active=acquire

    def move_to(self,group,values):
        self.prepare()
        if len(values)!=7 or not all(math.isfinite(v) for v in values):raise ValueError('Seven finite joint angles required')
        revision=self.scene.revision
        goal=MoveGroup.Goal()
        goal.request.group_name=group
        goal.request.pipeline_id='ompl'
        goal.request.planner_id='RRTConnectkConfigDefault'
        goal.request.allowed_planning_time=3.0
        goal.request.num_planning_attempts=1
        goal.request.max_velocity_scaling_factor=0.15
        goal.request.max_acceleration_scaling_factor=0.15
        goal.request.start_state.is_diff=True
        goal.request.start_state.joint_state=self.joints
        side=group.split('_')[0]
        constraints=Constraints()
        for i,value in enumerate(values,1):
            constraints.joint_constraints.append(JointConstraint(joint_name=f'openarm_{side}_joint{i}',
                position=value,tolerance_above=0.02,tolerance_below=0.02,weight=1.0))
        goal.request.goal_constraints=[constraints]
        goal.planning_options.plan_only=False
        if not self.move.wait_for_server(timeout_sec=5):raise RuntimeError('MoveIt unavailable')
        def guard():
            if not self.scene or self.scene.revision!=revision or not self.scene.valid or not 0<=now(self)-seconds(self.scene.header.stamp)<=seconds(self.scene.valid_for):
                raise RuntimeError('Bound scene revision expired or changed')
        self.acceptance_live=True
        future=self.move.send_goal_async(goal)
        def accepted(done):
            handle=done.result()
            if not self.acceptance_live and handle and handle.accepted:handle.cancel_goal_async()
        future.add_done_callback(accepted)
        self.until(future.done,guard=guard)
        self.goal=future.result()
        if not self.goal.accepted:raise RuntimeError('MoveIt goal rejected')
        result=self.goal.get_result_async()
        self.until(result.done,timeout=20,guard=guard)
        response=result.result()
        self.until(self.stopped)
        if response.status!=4 or response.result.error_code.val!=1:
            raise RuntimeError('MoveIt failed: '+str(response.result.error_code.val))
        self.call('/arm_gate/authorize',SetBool,False)
        print('PASS: MoveIt fresh plan executed through arm gate; measured joint standstill; base remains inhibited',flush=True)

    def stop_motion(self):
        self.acceptance_live=False
        if self.goal:self.goal.cancel_goal_async()
        for name,kind,value in [('/arm_gate/authorize',SetBool,False),('/base_gate/stop',Trigger,None)]:
            try:self.call(name,kind,value)
            except Exception as exc:print(str(exc),flush=True)
        self.until(self.stopped)
        print('Stopped: measured standstill; HOLD remains latched',flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=('prepare','move','stop','disarm'))
    parser.add_argument('--group',choices=('left_arm','right_arm'),default='right_arm')
    parser.add_argument('--joints',nargs=7,type=float,default=[0,0,0,0.3,0,0,0])
    args=parser.parse_args()
    if os.environ.get('CONTROL_MODE')!='moveit':
        parser.exit(2,'目前是導航／任務模式，不能手動控制手臂。請在宿主機取消任務後執行 make stop、make moveit，再開新的 make shell。\n')
    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    def interrupted(signum,frame):raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,interrupted)
    node=Control()
    try:
        node.check_profile()
        if args.command=='prepare':
            node.prepare()
            print('GUI execution window: 15 seconds. Re-plan with Plan & Execute; this terminal maintains the lease.',flush=True)
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:rclpy.spin_once(node,timeout_sec=0.03)
            node.call('/arm_gate/authorize',SetBool,False)
            node.until(node.stopped)
            print('GUI execution window closed; disarmed',flush=True)
        elif args.command=='stop':node.stop_motion()
        elif args.command=='disarm':
            node.call('/arm_gate/authorize',SetBool,False)
            node.until(node.stopped)
            print('Disarmed; measured joint standstill; base remains inhibited',flush=True)
        else:node.move_to(args.group,args.joints)
    except KeyboardInterrupt:
        if node.verified and node.lease_active:node.stop_motion()
        return 130
    except BaseException:
        if node.verified and node.lease_active:node.stop_motion()
        raise
    finally:
        node.acceptance_live=False
        if node.lease_active:
            try:node.set_lease(False)
            except Exception as exc:print('Lease not released: '+str(exc),flush=True)
        node.destroy_node()
        rclpy.shutdown()
