#!/usr/bin/env python3
"""Runtime acceptance against the isolated sim container, never a hardware graph."""
import argparse
import json
import os
import re
import subprocess
import time
import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from std_msgs.msg import String
from std_srvs.srv import Trigger,SetBool
from control_msgs.action import FollowJointTrajectory
from moveit_msgs.srv import GetPlanningScene
from trajectory_msgs.msg import JointTrajectoryPoint
from builtin_interfaces.msg import Duration
from retail_interfaces.msg import GateState,RiskAdvisory,ObstacleState


class Acceptance(Node):
    def __init__(self):
        super().__init__('retail_acceptance')
        self.state,self.gate,self.odom,self.risk = {},None,None,None
        self.hand = None
        self.create_subscription(String,'/mission/state',lambda m:setattr(self,'state',json.loads(m.data)),10)
        self.create_subscription(GateState,'/base/gate_state',lambda m:setattr(self,'gate',m),10)
        self.create_subscription(Odometry,'/odom',lambda m:setattr(self,'odom',m),10)
        self.create_subscription(RiskAdvisory,'/perception/vqa/chest',lambda m:setattr(self,'risk',m),10)
        self.create_subscription(ObstacleState,'/perception/hand/state',lambda m:setattr(self,'hand',m),10)

    def until(self,predicate,timeout=15):
        deadline = time.monotonic()+timeout
        while time.monotonic()<deadline:
            rclpy.spin_once(self,timeout_sec=0.05)
            if predicate(): return
        raise AssertionError(f'timeout: mission={self.state}, gate={self.gate}')

    def call(self,name,type_=Trigger,value=None,success=True):
        client = self.create_client(type_,name)
        assert client.wait_for_service(timeout_sec=10),name
        req = Trigger.Request() if type_ is Trigger else SetBool.Request(data=value)
        future = client.call_async(req)
        self.until(future.done)
        result = future.result()
        assert result.success == success,(name,result)
        self.destroy_client(client)
        return result

    def stopped(self):
        if not self.odom: return False
        v = self.odom.twist.twist
        return max(abs(v.linear.x),abs(v.linear.y),abs(v.angular.z))<0.01


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['navigation','full','faults','live-vqa','restart','invalid-scene'])
    args=parser.parse_args()
    rclpy.init()
    node=Acceptance()
    try:
        if args.mode=='live-vqa':
            node.until(lambda:node.risk and node.risk.valid and not node.risk.synthetic and node.risk.model_id not in ('','unavailable'),timeout=60)
            print('PASS actual VQA:',node.risk.action,node.risk.model_id,node.risk.raw_response,flush=True)
            node.until(lambda:node.hand and node.hand.valid and not node.hand.synthetic,timeout=15)
            print('PASS actual upstream MediaPipe hand observation:',node.hand.occupied,node.hand.reason,flush=True)
            if os.environ.get('CAPTURE_RVIZ')=='1':
                # RViz loads meshes after the first inference; wait for its first paint.
                until=time.monotonic()+6.0
                while time.monotonic()<until: rclpy.spin_once(node,timeout_sec=.05)
                node.until(lambda:node.risk.valid and node.hand.valid,timeout=15)
                until=time.monotonic()+0.2
                while time.monotonic()<until: rclpy.spin_once(node,timeout_sec=.02)
                windows = subprocess.check_output(['xwininfo','-root','-tree'],text=True)
                window = re.search(r'^\s*(0x[0-9a-f]+).*retail\.rviz.*RViz',windows,re.MULTILINE)
                assert window, 'No retail RViz window on the native display'
                subprocess.run(['import','-display',os.environ['DISPLAY'],'-window',window[1],'/tmp/retail-rviz-vqa.png'],check=True)
            return
        node.until(lambda:node.gate and node.odom and node.risk)
        if args.mode=='restart':
            node.until(lambda:bool(node.state) and node.gate.arms_stowed and node.gate.base_standstill)
            deadline=time.monotonic()+1
            while time.monotonic()<deadline:
                rclpy.spin_once(node,timeout_sec=.05)
                assert node.state.get('phase')=='IDLE' and not node.gate.enabled and node.stopped(), node.state
            print('PASS restart: IDLE, measured stow/standstill, disabled, no goal replay',flush=True)
            return
        if args.mode=='invalid-scene':
            node.until(lambda:node.gate.arms_stowed and node.gate.base_standstill)
            node.call('/mission/place')
            node.until(lambda:node.state.get('phase')=='HOLD')
            assert 'metric reconstruction' in node.state['reason'],node.state
            assert node.gate.arms_stowed and node.stopped()
            print('PASS missing metric scene: placement refused before arm authorization',flush=True)
            return
        if args.mode in ('navigation','full'):
            node.until(lambda:node.gate.base_standstill and node.gate.arms_stowed and node.risk.valid)
            command='run' if args.mode=='full' else 'navigate'
            node.call('/mission/'+command)
            node.until(lambda:node.state.get('phase') not in ('IDLE','DONE'),timeout=10)
            node.until(lambda:node.state.get('phase') in ('DONE','HOLD','FAULT'),timeout=120)
            assert node.state['phase']=='DONE',node.state
            assert node.odom.pose.pose.position.x>1.4,node.odom
            node.until(lambda:node.stopped() and not node.gate.enabled)
            if args.mode=='full':
                client=node.create_client(GetPlanningScene,'/get_planning_scene')
                assert client.wait_for_service(timeout_sec=5)
                request=GetPlanningScene.Request()
                request.components.components=20
                future=client.call_async(request)
                node.until(future.done)
                scene=future.result().scene
                ids={obj.id for obj in scene.world.collision_objects}
                assert {'shelf_back','shelf_level_0','shelf_level_1','shelf_level_2','placed_snack'}<=ids,ids
                assert not any(obj.object.id=='known_snack' for obj in scene.robot_state.attached_collision_objects)
                print('PASS MoveIt scene: shelf geometry committed and snack detached',flush=True)
            print('PASS',args.mode,json.dumps(node.state),'x=',node.odom.pose.pose.position.x,flush=True)
            return
        # An unauthorized client cannot bypass the arm gate through MoveIt/trajectory actions.
        arm=ActionClient(node,FollowJointTrajectory,'/left_joint_trajectory_controller/follow_joint_trajectory')
        assert arm.wait_for_server(timeout_sec=10)
        goal=FollowJointTrajectory.Goal()
        goal.trajectory.joint_names=[f'openarm_left_joint{i}' for i in range(1,8)]
        goal.trajectory.points=[JointTrajectoryPoint(positions=[0.0]*7,time_from_start=Duration(sec=1))]
        future=arm.send_goal_async(goal)
        node.until(future.done)
        assert not future.result().accepted
        print('PASS unauthorized arm goal rejected',flush=True)
        node.until(lambda:node.gate.base_standstill)
        node.call('/mission/navigate')
        node.until(lambda:abs(node.odom.twist.twist.linear.x)>0.02)
        node.call('/demo/set_obstacle',SetBool,True)
        node.until(lambda:node.gate.hold and node.stopped(),timeout=5)
        node.call('/demo/set_obstacle',SetBool,False)
        position=node.odom.pose.pose.position.x
        end=time.monotonic()+2
        while time.monotonic()<end:
            rclpy.spin_once(node,timeout_sec=.05)
            assert node.gate.hold and not node.gate.enabled
            assert abs(node.odom.pose.pose.position.x-position)<0.02
        print('PASS STOP latched; clear/CONTINUE does not resume',flush=True)
        node.until(lambda:node.state.get('phase')=='HOLD')
        node.call('/mission/reset')
        node.until(lambda:node.state.get('phase')=='DONE' and not node.gate.hold)
        assert not node.gate.enabled
        # A competing writer is detected even if it only advertises and publishes zero.
        competing=node.create_publisher(Twist,'/base/cmd_vel',1)
        competing.publish(Twist())
        node.until(lambda:node.gate.hold,timeout=5)
        node.destroy_publisher(competing)
        print('PASS duplicate base writer causes latched HOLD',flush=True)
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__=='__main__':
    main()

