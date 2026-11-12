"""TidyBot-local ownership, relative drive and guarded Nav2 velocity output."""
import math
import time
from uuid import uuid4

from geometry_msgs.msg import TransformStamped, Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, qos_profile_sensor_data
from sensor_msgs.msg import LaserScan
from std_msgs.msg import Bool, Empty, Header, String
from visualization_msgs.msg import Marker
from std_srvs.srv import SetBool, Trigger
from tf2_ros import TransformBroadcaster

from retail_interfaces.msg import DriveRequest, DriveState, GateState, RiskAdvisory
from .common import now, run, seconds, text_marker
from .drive_timing import remote_age
from .policy import ADVISORY_SPEED_PERCENT, GatePolicy, Observation

COMMAND_TTL = 0.25
ODOM_TTL = 0.5


def planar(twist):
    return twist.linear.x, twist.linear.y, twist.angular.z


def valid_odom(msg):
    p, q = msg.pose.pose.position, msg.pose.pose.orientation
    fields = (p.x, p.y, p.z, q.x, q.y, q.z, q.w, *planar(msg.twist.twist))
    return (msg.header.frame_id == 'odom' and msg.child_frame_id == 'base_link'
            and all(math.isfinite(value) for value in fields)
            and abs(q.x*q.x + q.y*q.y + q.z*q.z + q.w*q.w - 1) < 0.001)


def pose(msg):
    p, q = msg.pose.pose.position, msg.pose.pose.orientation
    return p.x, p.y, math.atan2(2*(q.w*q.z + q.x*q.y), 1-2*(q.y*q.y + q.z*q.z))


class TidyBotDrive(Node):
    def __init__(self):
        super().__init__('tidybot_drive')
        self.hardware = self.declare_parameter('hardware', False).value
        self.map_allowed = self.declare_parameter('allow_map_goals', not self.hardware).value
        self.policy = GatePolicy(('tidybot',), allow_synthetic=not self.hardware)
        self.boot_id = uuid4().hex
        self.task_id = ''
        self.phase, self.reason = 'IDLE', 'Waiting for a fresh goal'
        self.mode, self.sequence = 0, 0
        self.odom = self.scan = self.origin = None
        self.odom_at = self.scan_at = self.still_since = 0.0
        self.last_request = self.started = self.velocity_at = 0.0
        self.last_clock = now(self)
        self.next_status = 0.0
        self.velocity = Twist()
        self.distance = 0.0
        self.armed = not self.hardware
        self.armed_driver = None
        self.arm_deadline = 0.0
        self.interlock_at = self.manual_at = self.manual_enable_at = 0.0
        self.manual_enabled = self.dock_granted = False
        self.manual_driver = None
        self.stop_started = self.completed_at = 0.0
        self.manual = Twist()
        self.driver = None

        latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.cmd = self.create_publisher(Twist, '/base/cmd_vel', 1)
        self.enable = self.create_publisher(Bool, '/base/enable', latched)
        self.frame = self.create_publisher(String, '/base/control_frame', latched)
        self.state = self.create_publisher(DriveState, '/drive/state', 1)
        self.gate = self.create_publisher(GateState, '/base/gate_state', 1)
        self.marker = self.create_publisher(Marker, '/tidybot/drive_marker', 1)
        self.create_subscription(DriveRequest, '/drive/request', self.request, 1)
        self.create_subscription(DriveRequest, '/drive/velocity', self.velocity_request, qos_profile_sensor_data)
        self.create_subscription(Odometry, '/odom', self.odometry, qos_profile_sensor_data)
        self.create_subscription(LaserScan, '/scan', self.laser, qos_profile_sensor_data)
        self.create_subscription(RiskAdvisory, '/perception/vqa/tidybot', self.risk, qos_profile_sensor_data)
        self.create_service(Trigger, '/drive/reset_hold', self.reset)
        if self.hardware:
            self.create_service(SetBool, '/drive/arm_auto', self.arm)
            self.create_subscription(Bool, '/tidybot/auto_interlock', self.interlock, 1)
        self.create_subscription(Twist, '/manual_control/cmd_vel', self.manual_command, 1)
        self.create_subscription(Bool, '/manual_control/tidybot/enable', self.manual_enable, latched)
        self.create_subscription(Empty, '/teleop/stop', self.local_stop, 1)
        self.create_subscription(Bool, '/docking/control_granted', self.dock_control, latched)
        self.create_timer(0.02, self.tick)

    def odometry(self, msg):
        at = time.monotonic()
        if (not valid_odom(msg) or not 0 <= now(self)-seconds(msg.header.stamp) < ODOM_TTL
                or (self.odom and seconds(msg.header.stamp) <= seconds(self.odom.header.stamp))):
            self.odom = None
            self.still_since = 0.0
            if self.phase == 'ACTIVE':
                self.hold('Invalid or non-monotonic odometry')
            return
        if self.odom and self.phase == 'ACTIVE':
            x, y, _ = pose(msg)
            old_x, old_y, _ = pose(self.odom)
            dt = seconds(msg.header.stamp)-seconds(self.odom.header.stamp)
            if math.hypot(x-old_x, y-old_y) > 0.05 + 0.5*dt:
                self.hold('Odometry position jumped')
        if at-self.odom_at > ODOM_TTL or any(abs(value) >= 0.01 for value in planar(msg.twist.twist)):
            self.still_since = 0.0
        if not self.still_since and all(abs(value) < 0.01 for value in planar(msg.twist.twist)):
            self.still_since = at
        self.odom, self.odom_at = msg, at

    def laser(self, msg):
        if msg.ranges and msg.header.frame_id and any(
                math.isfinite(value) and msg.range_min <= value <= msg.range_max for value in msg.ranges):
            self.scan, self.scan_at = msg, time.monotonic()
        else:
            self.scan = None

    def risk(self, msg):
        if msg.camera_id != 'tidybot':
            self.hold('Unexpected VQA camera')
            return
        self.policy.observe('tidybot', Observation(seconds(msg.header.stamp), msg.action, msg.valid,
                                                  msg.synthetic, seconds(msg.valid_for), msg.source_id))
        # Preserve STOP even when a newer CONTINUE arrives before the next timer tick.
        healthy, reason, _ = self.policy.health(now(self))
        if self.phase == 'ACTIVE' and not healthy:
            self.hold(reason)

    def interlock(self, msg):
        self.interlock_at = time.monotonic() if msg.data else 0.0
        if not msg.data and self.phase == 'ACTIVE':
            self.hold('Local autonomous interlock released')

    def manual_command(self, msg):
        self.manual, self.manual_at = msg, time.monotonic()

    def manual_enable(self, msg):
        self.manual_enabled, self.manual_enable_at = msg.data, time.monotonic()
        if msg.data:
            self.preempt('Local manual control requested')

    def dock_control(self, msg):
        self.dock_granted = msg.data
        if msg.data:
            self.preempt('Docking requested local control')

    def preempt(self, reason):
        if self.phase == 'ACTIVE':
            self.hold(reason)
            self.phase = 'HANDOFF'
        self.armed = False

    def local_stop(self, msg):
        self.hold('Local gamepad stop')
        self.manual_driver = None

    def driver_endpoints(self):
        if not self.hardware:
            return ()
        endpoints = []
        for pub in (self.cmd, self.enable, self.frame):
            drivers = self.get_subscriptions_info_by_topic(pub.topic_name)
            matches = [tuple(item.endpoint_gid) for item in drivers if item.node_name == 'tidybot_base_node']
            if len(matches) != 1:
                return None
            endpoints.extend(matches)
        return tuple(endpoints)

    def conflict(self):
        topics = [self.cmd.topic_name, self.enable.topic_name, self.frame.topic_name,
                  '/odom', '/scan', '/drive/request', '/drive/velocity', '/perception/vqa/tidybot']
        topics += ['/manual_control/cmd_vel', '/manual_control/tidybot/enable', '/tidybot/auto_interlock']
        return any(self.count_publishers(topic) > 1 for topic in topics)

    def feedback(self, at):
        fresh = (self.odom is not None and at-self.odom_at < ODOM_TTL
                 and 0 <= now(self)-seconds(self.odom.header.stamp) < ODOM_TTL)
        return fresh, bool(fresh and self.still_since and at-self.still_since >= 0.5)

    def health(self, at, mode):
        if not self.feedback(at)[0]:
            return 'Odometry unavailable or stale'
        if self.hardware and (not self.armed or at-self.interlock_at >= COMMAND_TTL):
            return 'Local autonomous interlock or arming unavailable'
        if mode == DriveRequest.MAP and (
                not self.map_allowed or self.scan is None or at-self.scan_at >= ODOM_TTL
                or not 0 <= now(self)-seconds(self.scan.header.stamp) < ODOM_TTL):
            return 'Map navigation or fresh obstacle scan unavailable'
        good, reason, _ = self.policy.health(now(self))
        return '' if good else reason

    def arm(self, req, res):
        at = time.monotonic()
        if not req.data:
            if self.phase == 'ACTIVE':
                self.hold('Locally disarmed')
            self.armed = False
            res.success, res.message = True, 'Disarmed'
        else:
            driver = self.driver_endpoints()
            healthy_vqa, vqa_reason, _ = self.policy.health(now(self))
            if self.phase not in ('IDLE', 'DONE') or self.policy.hold:
                res.message = f'Drive is {self.phase}: {self.reason}'
            elif self.armed and at < self.arm_deadline:
                res.message = 'A local arming window is already active'
            elif not self.feedback(at)[1]:
                res.message = 'Arming requires fresh odometry and measured standstill'
            elif at-self.interlock_at >= COMMAND_TTL:
                res.message = 'Local /tidybot/auto_interlock is missing, false or stale'
            elif self.conflict() or driver is None or self.manual_active(at) or self.dock_granted:
                res.message = 'Arming requires exclusive idle driver and no manual or docking request'
            elif not healthy_vqa:
                res.message = f'VQA not ready: {vqa_reason}'
            else:
                self.armed = True
                self.armed_driver = driver
                self.arm_deadline = at+15.0
                res.success, res.message = True, 'Armed for one new goal within 15 seconds'
        return res

    def reset(self, req, res):
        at = time.monotonic()
        if (self.phase == 'ACTIVE' or not self.feedback(at)[1] or self.conflict()
                or not self.policy.health(now(self))[0] or self.manual_active(at) or self.dock_granted):
            res.message = 'Reset requires standstill, healthy VQA and exclusive idle control'
            return res
        self.boot_id = uuid4().hex
        self.task_id = ''
        self.phase, self.reason = 'IDLE', 'Reset; submit a new goal'
        self.policy.hold = False
        self.armed = not self.hardware
        res.success, res.message = True, self.reason
        return res

    def hold(self, reason):
        self.phase, self.reason = 'HOLD', reason
        self.policy.stop(reason)
        self.velocity = Twist()
        self.armed = False
        self.manual_driver = None

    def stopping(self, reason):
        self.phase, self.reason = 'STOPPING', reason
        self.stop_started = time.monotonic()
        self.still_since = 0.0
        self.armed = False

    def request(self, msg):
        if msg.operation != DriveRequest.VELOCITY:
            self.apply_request(msg)

    def velocity_request(self, msg):
        if msg.operation == DriveRequest.VELOCITY:
            self.apply_request(msg)

    def apply_request(self, msg):
        at, stamp = time.monotonic(), seconds(msg.header.stamp)
        if msg.boot_id != self.boot_id or not msg.task_id or msg.header.frame_id != 'base_link':
            return
        age = remote_age(now(self), stamp)
        if not 0 <= age < COMMAND_TTL:
            return
        if (self.phase in ('IDLE', 'DONE') and msg.operation == DriveRequest.START
                and msg.task_id != self.task_id and stamp > self.completed_at):
            if msg.mode not in (DriveRequest.MAP, DriveRequest.STRAIGHT):
                return
            self.task_id, self.mode = msg.task_id, msg.mode
            reason = self.health(at, msg.mode)
            if self.hardware and at >= self.arm_deadline:
                reason = 'Local arming window expired'
            driver = self.driver_endpoints()
            if self.hardware and driver != self.armed_driver:
                reason = 'Driver changed after local arming'
            if (reason or not self.feedback(at)[1] or self.conflict() or driver is None
                    or self.policy.hold or self.manual_active(at) or self.dock_granted):
                self.phase, self.reason = 'REJECTED', reason or 'Local control, standstill or interlock unavailable'
                self.armed = False
                return
            self.phase, self.reason = 'ACTIVE', 'Local motion session accepted'
            self.driver, self.origin = driver, pose(self.odom)
            self.started = at
            self.last_request = at-age
            self.sequence = msg.sequence
            self.distance = self.velocity_at = 0.0
            self.velocity = Twist()
            return
        if (self.phase not in ('ACTIVE', 'STOPPING') or msg.task_id != self.task_id or msg.mode != self.mode
                or msg.sequence <= self.sequence):
            return
        if msg.operation not in (DriveRequest.HEARTBEAT, DriveRequest.VELOCITY,
                                 DriveRequest.CANCEL, DriveRequest.FINISH):
            return
        self.sequence, self.last_request = msg.sequence, at-age
        if msg.operation == DriveRequest.CANCEL:
            self.hold('Task cancelled or THOR controller unavailable')
        elif self.phase == 'STOPPING':
            return
        elif msg.operation == DriveRequest.FINISH and self.mode == DriveRequest.MAP:
            self.stopping('Nav2 arrived; waiting for odometry standstill')
        elif msg.operation == DriveRequest.VELOCITY and self.mode == DriveRequest.MAP:
            if not all(math.isfinite(value) for value in planar(msg.velocity)):
                self.hold('Invalid navigation velocity')
                return
            self.velocity, self.velocity_at = msg.velocity, at-age

    def straight_velocity(self):
        x, y, heading = pose(self.odom)
        start_x, start_y, yaw = self.origin
        dx, dy = x-start_x, y-start_y
        self.distance = dx*math.cos(yaw) + dy*math.sin(yaw)
        lateral = dy*math.cos(yaw) - dx*math.sin(yaw)
        yaw_error = math.remainder(heading-yaw, math.tau)
        command = Twist()
        if abs(lateral) > 0.12 or abs(yaw_error) > 0.15 or self.distance < -0.05 or self.distance > 2.06:
            self.hold('Straight-line tracking exceeded position or heading tolerance')
        elif self.distance >= 1.97:
            self.stopping('Distance reached; waiting for odometry standstill')
        else:
            command.linear.x = min(0.18, max(0.04, 0.8*(2.0-self.distance)))
        return command

    def manual_active(self, at):
        return self.manual_enabled and at-self.manual_enable_at < COMMAND_TTL

    def tick(self):
        at, ros_now = time.monotonic(), now(self)
        fresh, stopped = self.feedback(at)
        conflict = self.conflict()
        if ros_now < self.last_clock or conflict or self.policy.hold and self.phase == 'ACTIVE':
            self.hold('Clock moved backwards' if ros_now < self.last_clock else
                      'Multiple publishers on a protected topic' if conflict else self.policy.reason)
        self.last_clock = ros_now
        if self.hardware and self.armed and self.phase in ('IDLE', 'DONE') and (
                at >= self.arm_deadline or not stopped or self.health(at, DriveRequest.STRAIGHT)
                or self.driver_endpoints() != self.armed_driver):
            self.armed = False
        output, enabled, owner = Twist(), False, 'NONE'
        if self.phase == 'ACTIVE':
            reason = self.health(at, self.mode)
            if reason or at-self.last_request >= COMMAND_TTL or at-self.started > 90 or self.driver_endpoints() != self.driver:
                self.hold(reason or 'Motion session expired, disconnected or driver restarted')
            elif self.mode == DriveRequest.STRAIGHT:
                output = self.straight_velocity()
                enabled = self.phase == 'ACTIVE'
            elif self.velocity_at and at-self.velocity_at < COMMAND_TTL:
                output, enabled = self.velocity, True
            elif self.velocity_at or at-self.started > 2.0:
                self.hold('Nav2 velocity stream unavailable')
            if enabled:
                command = output
                _, _, warning = self.policy.health(ros_now)
                speed_percent = ADVISORY_SPEED_PERCENT['DANGER_WARNING' if warning else 'CONTINUE']
                factor = speed_percent / 100
                output = Twist()
                vx, vy, wz = planar(command)
                output.linear.x = factor*max(-0.25, min(0.25, vx))
                output.linear.y = factor*max(-0.15, min(0.15, vy))
                output.angular.z = factor*max(-0.5, min(0.5, wz))
                owner = 'AUTO'
                self.reason = f'VQA speed scale {speed_percent}%'
        if self.phase == 'STOPPING':
            if self.mode == DriveRequest.STRAIGHT and fresh:
                x, y, _ = pose(self.odom)
                x0, y0, yaw = self.origin
                self.distance = (x-x0)*math.cos(yaw) + (y-y0)*math.sin(yaw)
            if not fresh or at-self.stop_started > 3.0:
                self.hold('Could not verify standstill after commanding a stop')
            elif stopped:
                if self.mode == DriveRequest.STRAIGHT and not 1.94 <= self.distance <= 2.06:
                    self.hold('Final distance outside tolerance')
                else:
                    self.phase, self.reason = 'DONE', 'Standstill verified'
                    self.completed_at = ros_now
        if self.phase in ('IDLE', 'DONE', 'REJECTED', 'HANDOFF') and not conflict and fresh:
            driver = self.driver_endpoints()
            manual_ok = (self.manual_active(at) and at-self.manual_at < COMMAND_TTL
                         and all(math.isfinite(value) for value in planar(self.manual))
                         and driver is not None)
            if manual_ok and (stopped or driver == self.manual_driver):
                self.manual_driver = driver
                output, enabled, owner = self.manual, True, 'LOCAL_MUX'
            else:
                self.manual_driver = None
        else:
            self.manual_driver = None
        self.cmd.publish(output)
        self.enable.publish(Bool(data=enabled))
        self.frame.publish(String(data='local'))
        if at < self.next_status:
            return
        self.next_status = at+0.08
        header = self.get_clock().now().to_msg()
        self.state.publish(DriveState(header=Header(stamp=header, frame_id='base_link'),
                                    boot_id=self.boot_id, task_id=self.task_id, phase=self.phase,
                                    reason=self.reason, standstill=stopped, enabled=enabled,
                                    hardware=self.hardware, owner=owner, distance=self.distance, output=output))
        self.gate.publish(GateState(header=Header(stamp=header, frame_id='base_link'), enabled=enabled,
                                    hold=self.phase in ('HOLD', 'HANDOFF'), base_standstill=stopped,
                                    base_inhibited=not enabled, arms_stowed=not self.hardware or at-self.interlock_at < COMMAND_TTL,
                                    reason=self.reason))
        self.marker.publish(text_marker(self, f'TidyBot: {self.phase}\n{self.reason}', 1, 0.85))


class OdomTF(Node):
    def __init__(self):
        super().__init__('tidybot_odom_tf')
        self.tf = TransformBroadcaster(self)
        self.create_subscription(Odometry, '/odom', self.odom, qos_profile_sensor_data)

    def odom(self, msg):
        if valid_odom(msg) and 0 <= now(self)-seconds(msg.header.stamp) < ODOM_TTL:
            transform = TransformStamped(header=msg.header, child_frame_id=msg.child_frame_id)
            transform.transform.translation.x = msg.pose.pose.position.x
            transform.transform.translation.y = msg.pose.pose.position.y
            transform.transform.rotation = msg.pose.pose.orientation
            self.tf.sendTransform(transform)


def main():
    run(TidyBotDrive)


def odom_tf_main():
    run(OdomTF)
