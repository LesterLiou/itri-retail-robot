"""Local arm action gateway, independent of planning and GPU inference."""
import threading
import math
import time
from functools import partial

import rclpy
from rclpy.action import ActionClient,ActionServer,GoalResponse,CancelResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor,ExternalShutdownException
from rclpy.node import Node
from control_msgs.action import FollowJointTrajectory,GripperCommand
from sensor_msgs.msg import JointState
from std_msgs.msg import String
from std_srvs.srv import SetBool,Trigger
from rclpy.qos import qos_profile_sensor_data
from retail_interfaces.msg import GateState,SceneState,ObstacleState
from .common import now,seconds,finite_joints


class ArmGate(Node):
    def __init__(self):
        super().__init__('arm_gate')
        self.group = ReentrantCallbackGroup()
        self.allow_synthetic = self.declare_parameter('allow_synthetic',False).value
        self.lock = threading.RLock()
        self.gate,self.scene,self.hand,self.joints = None,None,None,None
        self.authorized,self.held,self.reserved = False,False,False
        self.active,self.cancel_sent,self.scene_revision = None,False,0
        self.still_since,self.previous_positions,self.previous_at = None,None,None
        self.stop_future,self.stop_deadline = None,0.0
        self.reason = 'disabled; explicit manipulation authorization required'
        self.create_subscription(GateState,'/base/gate_state',lambda m:setattr(self,'gate',m),10)
        self.create_subscription(SceneState,'/perception/shelf/state',lambda m:setattr(self,'scene',m),10)
        self.create_subscription(ObstacleState,'/perception/hand/state',self.hand_observation,10)
        self.create_subscription(JointState,'/joint_states',self.feedback,qos_profile_sensor_data)
        self.state = self.create_publisher(String,'/arm/gate_state',10)
        self.create_service(SetBool,'/arm_gate/authorize',self.authorize)
        self.create_service(Trigger,'/arm_gate/reset_hold',self.reset)
        self.action_clients,self.servers = {},[]
        for side in ('left','right'):
            for controller,type_,suffix in ((f'{side}_joint_trajectory_controller',FollowJointTrajectory,'follow_joint_trajectory'),
                                           (f'{side}_gripper_controller',GripperCommand,'gripper_cmd')):
                name = f'/{controller}/{suffix}'
                client = ActionClient(self,type_,'/hardware'+name,callback_group=self.group)
                self.action_clients[name] = client
                server = ActionServer(self,type_,name,execute_callback=partial(self.execute,name,type_),
                                      goal_callback=partial(self.goal,name),cancel_callback=self.cancel,
                                      callback_group=self.group)
                self.servers.append(server)
        self.create_timer(0.02,self.tick)

    def feedback(self,msg):
        positions = dict(zip(msg.name,msg.position))
        t = time.monotonic()
        finite = bool(positions) and all(math.isfinite(v) for v in positions.values())
        steady = finite and self.previous_positions is not None and positions.keys()==self.previous_positions.keys()
        if steady:
            dt = t-self.previous_at
            steady = dt>0 and all(abs(v-self.previous_positions[n])/dt<0.02 for n,v in positions.items())
        self.still_since = (self.still_since or t) if steady else None
        self.previous_positions,self.previous_at = positions,t
        self.joints = msg

    def hand_observation(self,msg):
        with self.lock:
            if self.authorized and self.hand and (msg.source_id!=self.hand.source_id or seconds(msg.header.stamp)<seconds(self.hand.header.stamp)):
                self.request_stop('hand source restarted or observation time reversed')
            self.hand = msg

    def stopped(self):
        return bool(self.joints and 0<=now(self)-seconds(self.joints.header.stamp)<0.5 and
                    self.still_since and time.monotonic()-self.still_since>=0.3)

    def observations_ok(self):
        t = now(self)
        required = [f'openarm_{side}_joint{i}' for side in ('left','right') for i in range(1,8)]
        required += ['openarm_left_finger_joint1','openarm_right_finger_joint1']
        return bool(self.gate and 0<=t-seconds(self.gate.header.stamp)<0.5 and
                    self.gate.base_inhibited and self.gate.base_standstill and not self.gate.hold and
                    self.scene and self.scene.valid and self.scene.metric_valid and math.isfinite(self.scene.coverage) and 0<=self.scene.coverage<=1 and (self.allow_synthetic or not self.scene.synthetic) and
                    0<=t-seconds(self.scene.header.stamp)<=seconds(self.scene.valid_for) and
                    self.hand and self.hand.valid and not self.hand.occupied and (self.allow_synthetic or not self.hand.synthetic) and
                    0<=t-seconds(self.hand.header.stamp)<=min(seconds(self.hand.valid_for),0.5) and
                    finite_joints(self.joints,required) and 0<=t-seconds(self.joints.header.stamp)<0.5)

    def authorize(self,req,res):
        with self.lock:
            if req.data:
                res.success = bool(not self.held and not self.reserved and self.observations_ok() and self.stopped())
                self.authorized = res.success
                res.message = 'authorized for new goals' if res.success else 'authorization refused: hold/feedback/interlock'
            else:
                self.authorized = False
                if self.active:
                    self.request_stop('authorization withdrawn')
                res.success,res.message = True,'disabled; no queued trajectory will be released'
        return res

    def reset(self,req,res):
        with self.lock:
            res.success = bool(not self.reserved and self.stopped() and self.observations_ok())
            if res.success:
                self.held,self.authorized = False,False
            res.message = 'reset; explicit new authorization required' if res.success else 'reset refused: feedback/interlock'
        return res

    def goal(self,name,request):
        with self.lock:
            allowed = self.authorized and not self.held and not self.reserved and self.observations_ok() and self.action_clients[name].server_is_ready()
            if allowed:
                self.reserved = True
                self.scene_revision = self.scene.revision
                self.cancel_sent = False
                return GoalResponse.ACCEPT
            return GoalResponse.REJECT

    def cancel(self,handle):
        with self.lock:
            self.request_stop('goal cancelled')
        return CancelResponse.ACCEPT

    def request_stop(self,reason):
        self.held,self.authorized,self.reason = True,False,reason
        if self.active and not self.cancel_sent:
            self.active.cancel_goal_async()
            self.cancel_sent = True

    async def execute(self,name,type_,goal_handle):
        result = type_.Result()
        try:
            if not self.authorized or self.held or not self.observations_ok():
                goal_handle.abort()
                return result
            upstream = await self.action_clients[name].send_goal_async(goal_handle.request)
            with self.lock:
                self.active = upstream
                if not upstream.accepted:
                    self.request_stop('underlying controller rejected goal')
                    goal_handle.abort()
                    return result
                if goal_handle.is_cancel_requested or self.held or not self.authorized:
                    self.request_stop('cancelled before controller acceptance')
            response = await upstream.get_result_async()
            result = response.result
            self.stop_future = rclpy.task.Future()
            self.stop_deadline = time.monotonic()+1.0
            verified = await self.stop_future
            if not verified:
                self.request_stop('controller result without verified standstill')
                goal_handle.abort()
            elif goal_handle.is_cancel_requested:
                goal_handle.canceled()
            elif response.status == 4 and not self.held:
                goal_handle.succeed()
            else:
                goal_handle.abort()
            return result
        finally:
            with self.lock:
                self.active,self.reserved,self.stop_future = None,False,None

    def tick(self):
        with self.lock:
            if (self.authorized and not self.observations_ok()) or (self.reserved and self.scene and self.scene.revision!=self.scene_revision):
                self.request_stop('observation/interlock/scene lost during execution')
            if self.stop_future and not self.stop_future.done():
                if self.stopped():
                    self.stop_future.set_result(True)
                elif time.monotonic()>self.stop_deadline:
                    self.stop_future.set_result(False)
            self.state.publish(String(data=f'authorized={self.authorized} hold={self.held} active={self.reserved} stopped={self.stopped()} {self.reason}'))


def main():
    rclpy.init()
    node = ArmGate()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except (KeyboardInterrupt,ExternalShutdownException):
        pass
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.try_shutdown()

