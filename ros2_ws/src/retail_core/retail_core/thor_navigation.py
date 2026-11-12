"""THOR task owner. All physical motion decisions stay with the TidyBot receiver."""
import json
import time
from uuid import uuid4

from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped, Twist, TwistStamped
from lifecycle_msgs.msg import State
from lifecycle_msgs.srv import GetState
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from std_msgs.msg import Header, String
from std_srvs.srv import Trigger
from visualization_msgs.msg import Marker

from retail_interfaces.msg import DriveRequest, DriveState, RiskAdvisory
from .common import now, run, seconds, text_marker
from .drive_timing import CLOCK_UNCERTAINTY, remote_age
from .navigation import validate_goal

STATE_TTL = 0.35


class ThorNavigation(Node):
    def __init__(self):
        super().__init__('thor_navigation')
        self.drive = None
        self.drive_at = self.started = self.sequence = 0
        self.boot_id = self.task_id = ''
        self.phase, self.reason = 'IDLE', 'Waiting for a goal'
        self.mode = 0
        self.target = self.goal_stamp = self.nav_handle = self.risk = None
        self.last_goal_stamp = now(self)
        self.last_goal_rejection = None
        self.nav_pending = False
        self.nav_started_at = 0.0
        self.has_nav_output = False
        self.request_pub = self.create_publisher(DriveRequest, '/drive/request', 1)
        self.velocity_pub = self.create_publisher(DriveRequest, '/drive/velocity', qos_profile_sensor_data)
        self.status = self.create_publisher(String, '/mission/state', 10)
        self.vqa_marker = self.create_publisher(Marker, '/tidybot/vqa_marker', 1)
        self.nav_state = self.create_client(GetState, '/thor/bt_navigator/get_state')
        self.nav_check = None
        self.nav_checked_at = self.nav_ready_at = 0.0
        self.nav = ActionClient(self, NavigateToPose, '/thor/navigate_to_pose')
        self.create_subscription(DriveState, '/drive/state', self.feedback, qos_profile_sensor_data)
        self.create_subscription(TwistStamped, '/thor/nav_velocity', self.velocity, 1)
        self.create_subscription(PoseStamped, '/mission/goal_pose', self.map_goal, 10)
        self.create_subscription(RiskAdvisory, '/perception/vqa/tidybot', self.observe, qos_profile_sensor_data)
        self.create_service(Trigger, '/mission/straight_2m', self.straight_goal)
        self.create_service(Trigger, '/mission/cancel', self.cancel)
        self.create_timer(0.05, self.tick)

    def feedback(self, msg):
        self.drive, self.drive_at = msg, time.monotonic()

    def observe(self, msg):
        self.risk = msg

    def feedback_fresh(self):
        return (self.drive is not None and time.monotonic()-self.drive_at < STATE_TTL
                and 0 <= remote_age(now(self), seconds(self.drive.header.stamp)) < STATE_TTL
                and self.count_publishers('/drive/state') == 1)

    def map_goal(self, msg):
        stamp = seconds(msg.header.stamp)
        try:
            validate_goal(msg, now(self)+CLOCK_UNCERTAINTY)
            if stamp <= self.last_goal_stamp:
                raise ValueError('Goal duplicates or predates this operator request')
            self.last_goal_stamp = stamp
            self.begin(DriveRequest.MAP, msg)
        except ValueError as exc:
            self.last_goal_rejection = {'stamp': stamp, 'reason': str(exc)}
            self.get_logger().warning(f'Goal rejected: {exc}')

    def straight_goal(self, req, res):
        try:
            self.begin(DriveRequest.STRAIGHT, None)
            res.success, res.message = True, self.task_id
        except ValueError as exc:
            res.message = str(exc)
        return res

    def begin(self, mode, target):
        if self.phase in ('STARTING', 'ACTIVE', 'FINISHING', 'CANCELLING') or self.nav_pending or self.nav_handle:
            raise ValueError('An earlier goal is still active or cancelling')
        if not self.feedback_fresh() or self.drive.phase not in ('IDLE', 'DONE') or not self.drive.standstill:
            raise ValueError('TidyBot must be reachable, idle and stopped; reset locally if held')
        if mode == DriveRequest.MAP and not self.nav_ready():
            raise ValueError('THOR Nav2 action server is unavailable')
        self.target, self.mode = target, mode
        self.goal_stamp = seconds(target.header.stamp) if target else now(self)
        self.boot_id, self.task_id = self.drive.boot_id, uuid4().hex
        self.started = time.monotonic()
        self.phase, self.reason = 'STARTING', 'Waiting for TidyBot motion permission'
        self.sequence = 0
        self.has_nav_output = False
        self.last_goal_rejection = None
        self.send(DriveRequest.START)

    def send(self, operation, velocity=None, stamp=None):
        self.sequence += 1
        stamp = stamp or self.get_clock().now().to_msg()
        msg = DriveRequest(header=Header(stamp=stamp, frame_id='base_link'),
                           boot_id=self.boot_id, task_id=self.task_id, sequence=self.sequence,
                           mode=self.mode, operation=operation, velocity=velocity or Twist())
        publisher = self.velocity_pub if operation == DriveRequest.VELOCITY else self.request_pub
        publisher.publish(msg)

    def velocity(self, msg):
        if (self.phase == 'ACTIVE' and self.mode == DriveRequest.MAP and self.nav_handle
                and msg.header.frame_id == 'base_link' and 0 <= now(self)-seconds(msg.header.stamp) < 0.25
                and seconds(msg.header.stamp) > self.nav_started_at and self.count_publishers('/thor/nav_velocity') == 1):
            self.has_nav_output = True
            self.send(DriveRequest.VELOCITY, msg.twist, msg.header.stamp)

    def start_nav(self):
        self.nav_started_at = now(self)
        self.nav_pending = True
        goal = NavigateToPose.Goal(pose=self.target)
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        self.nav.send_goal_async(goal).add_done_callback(self.nav_accepted)

    def nav_accepted(self, future):
        self.nav_pending = False
        try:
            handle = future.result()
        except Exception as exc:
            self.abort(f'Nav2 goal request failed: {exc}')
            return
        if not handle.accepted:
            self.abort('Nav2 refused the goal')
            return
        self.nav_handle = handle
        handle.get_result_async().add_done_callback(self.nav_finished)
        if self.phase != 'ACTIVE':
            handle.cancel_goal_async()

    def nav_finished(self, future):
        self.nav_handle = None
        if self.phase != 'ACTIVE':
            return
        try:
            result = future.result()
        except Exception as exc:
            self.abort(f'Nav2 result failed: {exc}')
            return
        if result.status == GoalStatus.STATUS_SUCCEEDED:
            self.phase, self.reason = 'FINISHING', 'Nav2 arrived; verifying local standstill'
            self.send(DriveRequest.FINISH)
        else:
            self.abort(f'Nav2 returned action status {result.status}')

    def abort(self, reason):
        if self.phase in ('STARTING', 'ACTIVE', 'FINISHING'):
            self.phase, self.reason = 'CANCELLING', reason
            self.send(DriveRequest.CANCEL)
            if self.nav_handle:
                self.nav_handle.cancel_goal_async()

    def cancel(self, req, res):
        res.success = self.phase in ('STARTING', 'ACTIVE', 'FINISHING')
        res.message = 'Stop requested; check measured standstill' if res.success else 'No active task'
        if res.success:
            self.abort('Operator cancelled')
        return res

    def nav_ready(self):
        return time.monotonic()-self.nav_ready_at < 1.0 and self.nav.server_is_ready()

    def check_nav(self):
        at = time.monotonic()
        if at-self.nav_checked_at < 0.25 or not self.nav_state.service_is_ready():
            return
        if self.nav_check and not self.nav_check.done():
            if at-self.nav_checked_at < 1.0:
                return
            self.nav_state.remove_pending_request(self.nav_check)
        self.nav_checked_at = at
        self.nav_check = self.nav_state.call_async(GetState.Request())
        self.nav_check.add_done_callback(self.record_nav_state)

    def record_nav_state(self, future):
        if (future is self.nav_check and not future.cancelled() and future.exception() is None
                and future.result().current_state.id == State.PRIMARY_STATE_ACTIVE):
            self.nav_ready_at = time.monotonic()
        else:
            self.nav_ready_at = 0.0

    def tick(self):
        self.check_nav()
        fresh = self.feedback_fresh()
        matching = fresh and self.drive.boot_id == self.boot_id and self.drive.task_id == self.task_id
        if self.phase in ('STARTING', 'ACTIVE', 'FINISHING'):
            if (not fresh or self.drive.boot_id != self.boot_id
                    or any(self.count_publishers(topic) != 1 for topic in ('/drive/request', '/drive/velocity'))):
                self.abort('TidyBot state unavailable or controller restarted')
            elif matching and self.drive.phase in ('HOLD', 'REJECTED', 'HANDOFF'):
                self.abort(self.drive.reason)
                self.phase = self.drive.phase
            elif matching and self.drive.phase == 'DONE' and self.drive.standstill:
                self.phase, self.reason = 'DONE', self.drive.reason
            elif self.phase == 'STARTING':
                if matching and self.drive.phase == 'ACTIVE':
                    self.phase, self.reason = 'ACTIVE', 'TidyBot granted the motion session'
                    if self.mode == DriveRequest.MAP:
                        self.start_nav()
                elif time.monotonic()-self.started > 2.0:
                    self.abort('TidyBot did not accept the task')
                else:
                    self.send(DriveRequest.START)
            elif self.phase == 'FINISHING':
                if self.drive.phase == 'ACTIVE':
                    self.send(DriveRequest.FINISH)
            elif self.drive.phase == 'ACTIVE' and not self.has_nav_output:
                self.send(DriveRequest.HEARTBEAT)
        if self.phase == 'ACTIVE' and self.mode == DriveRequest.MAP and not self.nav_ready():
            self.abort('THOR Nav2 became inactive')
        if self.phase == 'CANCELLING':
            if matching and self.drive.phase in ('ACTIVE', 'STOPPING'):
                self.send(DriveRequest.CANCEL)
            elif fresh:
                self.phase = 'HOLD' if matching and self.drive.phase == 'HOLD' else 'CANCELLED'
        self.publish_status(fresh)

    def publish_status(self, fresh):
        self.status.publish(String(data=json.dumps({
            'mission_id': self.task_id, 'phase': self.phase, 'reason': self.reason,
            'request_stamp': self.goal_stamp, 'last_goal_rejection': self.last_goal_rejection,
            'drive_phase': self.drive.phase if fresh else 'OFFLINE',
            'standstill': self.drive.standstill if fresh else None,
            'distance': self.drive.distance if fresh else None,
            'owner': self.drive.owner if fresh else None,
            'hardware': self.drive.hardware if fresh else None,
            'nav_ready': self.nav_ready(),
        })))
        risk = self.risk
        age = remote_age(now(self), seconds(risk.header.stamp)) if risk else -1
        good = risk and risk.valid and 0 <= age <= min(2.0, seconds(risk.valid_for))
        label = f'VQA: {risk.action} ({age:.2f} s)' if good else 'VQA: DATA_LOSS / waiting for fresh result'
        if good:
            label += f'\n{risk.explanation[:120]}'
        self.vqa_marker.publish(text_marker(self, label, 2, 1.15))


def main():
    run(ThorNavigation)
