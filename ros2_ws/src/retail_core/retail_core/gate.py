import math
import time
from functools import partial

from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from sensor_msgs.msg import JointState
from std_msgs.msg import Bool, String
from std_srvs.srv import SetBool, Trigger
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from retail_interfaces.msg import RiskAdvisory, GateState
from .common import now, seconds, run, text_marker,finite_joints
from .policy import ADVISORY_SPEED_PERCENT, GatePolicy, Observation
from visualization_msgs.msg import Marker
from action_msgs.srv import CancelGoal
from action_msgs.msg import GoalStatusArray
from rclpy.qos import QoSProfile,DurabilityPolicy


class BaseGate(Node):
    def __init__(self):
        super().__init__('base_gate')
        cameras = self.declare_parameter('required_cameras', ['chest']).value
        allow = self.declare_parameter('allow_synthetic', False).value
        self.policy = GatePolicy(cameras, allow)
        self.stow_positions = self.declare_parameter('stow_positions',[-1000.0]*14).value
        if len(self.stow_positions)!=14 or not all(math.isfinite(v) for v in self.stow_positions):
            raise ValueError('stow_positions requires 14 finite joint values')
        self.arm_still_since,self.previous_joints,self.previous_joint_at = None,None,None
        self.source = 'navigation'
        self.commands = {}
        self.joints = None
        self.odom = None
        self.still_since = None
        self.last_tick = now(self)
        self.was_held = False
        self.nav_cancel = self.create_client(CancelGoal,'/navigate_to_pose/_action/cancel_goal')
        self.nav_cancel_future,self.nav_cancel_at = None,0.0
        self.nav_active,self.dock_active,self.dock_at = False,False,0.0
        qos = QoSProfile(depth=1,durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.dock_grant = self.create_publisher(Bool,'/docking/control_granted',qos)
        self.create_subscription(GoalStatusArray,'/navigate_to_pose/_action/status',self.nav_status,qos)
        self.create_subscription(Bool,'/docking/active',self.dock_status,1)
        self.create_service(SetBool,'/docking/set_control',self.dock_lease)
        self.output = self.create_publisher(Twist, '/base/cmd_vel', 1)
        self.enable_pub = self.create_publisher(Bool, '/base/enable', qos)
        self.frame_pub = self.create_publisher(String, '/base/control_frame', qos)
        self.state_pub = self.create_publisher(GateState, '/base/gate_state', 10)
        self.markers = self.create_publisher(Marker, '/retail/status_markers', 10)
        for camera in cameras:
            self.create_subscription(RiskAdvisory, f'/perception/vqa/{camera}', partial(self.risk, camera), 10)
        for source in ('navigation', 'docking', 'teleop'):
            self.create_subscription(Twist, f'/{source}/cmd_vel', partial(self.command, source), 1)
            self.create_service(Trigger, f'/base_gate/select_{source}', partial(self.select, source))
        self.create_subscription(Odometry, '/odom', self.odometry, 10)
        self.create_subscription(JointState, '/joint_states', self.joint_state, qos_profile_sensor_data)
        self.create_service(SetBool, '/base_gate/enable', self.enable)
        self.create_service(SetBool, '/base_gate/inhibit', self.inhibit)
        self.create_service(Trigger, '/base_gate/reset_hold', self.reset)
        self.create_service(Trigger, '/base_gate/stop', self.stop)
        self.create_timer(0.02, self.tick)

    def risk(self, camera, msg):
        self.policy.observe(camera, Observation(seconds(msg.header.stamp), msg.action, msg.valid,
                                               msg.synthetic, seconds(msg.valid_for), msg.source_id))

    def nav_status(self,msg):
        self.nav_active = any(s.status in (1,2,3) for s in msg.status_list)

    def dock_status(self,msg):
        self.dock_active,self.dock_at = msg.data,time.monotonic()

    def dock_lease(self,req,res):
        if not req.data:
            if self.source == 'docking':
                self.policy.enabled = False
                self.source = 'navigation'
                self.commands.clear()
            res.success,res.message = True,'dock lease released; no automatic navigation resume'
            return res
        fresh,stowed,stopped = self.feedback()
        healthy,_,_ = self.policy.health(now(self))
        res.success = bool(fresh and stowed and stopped and healthy and not self.nav_active and
                           not self.policy.enabled and not self.policy.hold and not self.policy.inhibited and not self.conflict())
        if res.success:
            self.source = 'docking'
            self.policy.enabled = True
            self.commands.clear()
            self.dock_at = time.monotonic()
            self.dock_active = False
        res.message = 'exclusive docking lease granted' if res.success else 'dock lease refused: cancel navigation and verify stop/interlocks'
        return res

    def command(self, source, msg):
        self.commands[source] = (msg, time.monotonic())

    def joint_state(self, msg):
        positions = dict(zip(msg.name,msg.position))
        t = time.monotonic()
        steady = bool(self.previous_joints and positions.keys()==self.previous_joints.keys() and self.previous_joint_at and t>self.previous_joint_at)
        if steady:
            steady = all(math.isfinite(v) and abs(v-self.previous_joints[n])/(t-self.previous_joint_at)<0.02 for n,v in positions.items())
        self.arm_still_since = (self.arm_still_since or t) if steady else None
        self.previous_joints,self.previous_joint_at = positions,t
        self.joints = msg

    def odometry(self, msg):
        self.odom = msg
        v = msg.twist.twist
        stopped = all(math.isfinite(x) and abs(x) < 0.01 for x in (v.linear.x, v.linear.y, v.angular.z))
        if stopped:
            self.still_since = self.still_since or time.monotonic()
        else:
            self.still_since = None

    def feedback(self):
        t = now(self)
        fresh = self.odom is not None and self.joints is not None
        if fresh:
            fresh = all(0 <= t-seconds(m.header.stamp) < 0.5 for m in (self.odom, self.joints))
        values = dict(zip(self.joints.name, self.joints.position)) if self.joints else {}
        names = [f'openarm_{side}_joint{i}' for side in ('left','right') for i in range(1,8)]
        fresh &= finite_joints(self.joints,names+['openarm_left_finger_joint1','openarm_right_finger_joint1'])
        if self.odom:
            p,q,v = self.odom.pose.pose.position,self.odom.pose.pose.orientation,self.odom.twist.twist
            numbers = (p.x,p.y,p.z,q.x,q.y,q.z,q.w,v.linear.x,v.linear.y,v.angular.z)
            fresh &= all(math.isfinite(x) for x in numbers) and abs(q.x*q.x+q.y*q.y+q.z*q.z+q.w*q.w-1)<1e-3
            fresh &= self.odom.header.frame_id=='odom' and self.odom.child_frame_id=='base_link'
        stowed = all(n in values and math.isfinite(values[n]) and abs(values[n]-ref) < 0.08 for n,ref in zip(names,self.stow_positions))
        stowed &= bool(self.arm_still_since and time.monotonic()-self.arm_still_since>=0.3)
        stopped = bool(fresh and self.still_since and time.monotonic()-self.still_since >= 0.5)
        return bool(fresh), stowed, stopped

    def conflict(self):
        outputs = self.get_publishers_info_by_topic('/base/cmd_vel')
        risk_conflict = any(len(self.get_publishers_info_by_topic(f'/perception/vqa/{c}')) > 1 for c in self.policy.cameras)
        source_conflict = any(len(self.get_publishers_info_by_topic(f'/{s}/cmd_vel')) > 1 for s in ('navigation','docking','teleop'))
        return len(outputs) > 1 or risk_conflict or source_conflict

    def enable(self, req, res):
        if not req.data:
            self.policy.enabled = False
            self.commands.clear()
            res.success, res.message = True, 'disabled; commands discarded'
            return res
        fresh, stowed, stopped = self.feedback()
        healthy, reason, _ = self.policy.health(now(self))
        res.success = bool(healthy and fresh and stowed and stopped and not self.nav_active and not self.policy.hold and not self.policy.inhibited and not self.conflict())
        self.policy.enabled = res.success
        if res.success:
            self.commands.clear()  # Never release commands from before the enable transition.
        res.message = 'enabled: new commands only' if res.success else f'enable refused: {reason}/feedback/hold/inhibit/active navigation'
        return res

    def inhibit(self, req, res):
        fresh, stowed, stopped = self.feedback()
        if not req.data and not (fresh and stowed and stopped):
            res.success, res.message = False, 'release refused: arms/base not verified'
            return res
        self.policy.inhibited = req.data
        self.policy.enabled = False
        self.commands.clear()
        res.success, res.message = True, 'base inhibition updated; explicit enable still required'
        return res

    def reset(self, req, res):
        fresh, stowed, stopped = self.feedback()
        res.success, res.message = self.policy.reset(now(self), stopped, stowed, fresh, self.conflict() or self.nav_active)
        self.commands.clear()
        return res

    def stop(self, req, res):
        self.policy.stop('operator/mission stop')
        self.commands.clear()
        res.success, res.message = True, 'HOLD requested; inspect odom for physical/fake standstill'
        return res

    def select(self, source, req, res):
        fresh, _, stopped = self.feedback()
        res.success = bool(fresh and stopped and not self.policy.enabled)
        if res.success:
            self.source = source
            self.commands.clear()
        res.message = f'owner={self.source}; handoff requires disabled and stopped'
        return res

    def tick(self):
        t = now(self)
        if t < self.last_tick:
            self.policy.stop('ROS time moved backwards')
            self.commands.clear()
        self.last_tick = t
        fresh, stowed, stopped = self.feedback()
        allowed, warning = self.policy.permit(t, stowed, fresh, self.conflict())
        docking_granted = allowed and self.source == 'docking'
        self.dock_grant.publish(Bool(data=docking_granted))
        if docking_granted and (not self.dock_active or time.monotonic()-self.dock_at>0.25):
            allowed = False
            if time.monotonic()-self.dock_at>0.25:
                self.policy.stop('docking heartbeat lost')
        if self.policy.hold and (self.nav_active or not self.was_held):
            self.commands.clear()
            if self.nav_cancel.service_is_ready() and time.monotonic()-self.nav_cancel_at>0.2 and (self.nav_cancel_future is None or self.nav_cancel_future.done()):
                self.nav_cancel_at = time.monotonic()
                self.nav_cancel_future = self.nav_cancel.call_async(CancelGoal.Request())
        self.was_held = self.policy.hold
        msg = Twist()
        speed_percent = ADVISORY_SPEED_PERCENT['DANGER_WARNING' if warning else 'CONTINUE']
        if allowed and self.source in self.commands:
            cmd, received = self.commands[self.source]
            values = (cmd.linear.x, cmd.linear.y, cmd.angular.z)
            if time.monotonic()-received <= 0.25 and all(math.isfinite(x) for x in values):
                factor = speed_percent / 100
                msg.linear.x = max(-0.25, min(0.25, values[0])) * factor
                msg.linear.y = max(-0.25, min(0.25, values[1])) * factor
                msg.angular.z = max(-0.5, min(0.5, values[2])) * factor
            elif time.monotonic()-received > 0.25:
                self.policy.stop('command source timed out')
                allowed = False
        self.output.publish(msg)
        self.enable_pub.publish(Bool(data=allowed))
        self.frame_pub.publish(String(data='local'))
        state = GateState()
        state.header.stamp = self.get_clock().now().to_msg()
        state.header.frame_id = 'base_link'
        state.enabled, state.hold = allowed, self.policy.hold
        state.base_inhibited = self.policy.inhibited
        state.base_standstill, state.arms_stowed = stopped, stowed and fresh
        state.reason = self.policy.reason if not allowed else f'NAVIGATING / speed scale {speed_percent}%'
        self.state_pub.publish(state)
        self.markers.publish(text_marker(self, state.reason, 20, 2.25, (1.0,0.4,0.1) if state.hold else (0.2,0.9,1.0)))


def main():
    run(BaseGate)

