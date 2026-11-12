"""Run from the isolated fake Compose network, with no robot device access."""
import json
import math
import time

from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Odometry
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from std_msgs.msg import String
from std_srvs.srv import Trigger

from retail_interfaces.msg import DriveRequest, DriveState
from retail_core.tidybot_drive import pose


class Acceptance(Node):
    def __init__(self):
        super().__init__('navigation_acceptance')
        self.state = self.odom = None
        self.mission = {}
        self.create_subscription(String, '/mission/state', self.status, 1)
        self.create_subscription(DriveState, '/drive/state', self.drive, qos_profile_sensor_data)
        self.create_subscription(Odometry, '/odom', self.odometry, qos_profile_sensor_data)
        self.goals = self.create_publisher(PoseStamped, '/mission/goal_pose', 1)
        self.risk = self.create_publisher(String, '/test/vqa_action', 1)
        self.straight = self.create_client(Trigger, '/mission/straight_2m')
        self.cancel = self.create_client(Trigger, '/mission/cancel')

    def status(self, msg):
        self.mission = json.loads(msg.data)

    def drive(self, msg):
        self.state = msg

    def odometry(self, msg):
        self.odom = msg

    def until(self, condition, message, timeout=25):
        deadline = time.monotonic() + timeout
        while rclpy.ok() and time.monotonic() < deadline:
            rclpy.spin_once(self, timeout_sec=0.05)
            if condition():
                return
        raise RuntimeError(f'{message}. Latest state: {self.state}')

    def phase(self, *expected):
        return self.state and self.state.phase in expected

    def call(self, service, expect=True):
        self.until(service.service_is_ready, 'Service unavailable', 10)
        result = service.call_async(Trigger.Request())
        rclpy.spin_until_future_complete(self, result, timeout_sec=5)
        assert result.done(), 'Service did not reply'
        assert result.result().success == expect, result.result().message

    def goal(self, x, y):
        msg = PoseStamped()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = 'map'
        msg.pose.position.x, msg.pose.position.y = x, y
        msg.pose.orientation.w = 1.0
        self.goals.publish(msg)

    def run(self, scenario):
        self.until(lambda: self.phase('IDLE', 'DONE') and self.state.standstill and not self.state.hardware
                   and self.odom and self.goals.get_subscription_count() and self.risk.get_subscription_count(),
                   'Fake control chain unavailable', 15)
        if scenario == 'straight':
            old_task = self.state.task_id
            self.call(self.straight)
            self.until(lambda: self.phase('DONE') and self.state.standstill and self.state.task_id != old_task,
                       'Straight target did not stop', 55)
            assert 1.94 <= self.state.distance <= 2.06, self.state
            print(f'PASS straight distance={self.state.distance:.3f} m, measured standstill')
        elif scenario == 'map':
            self.until(lambda: self.mission.get('nav_ready'), 'Nav2 did not become active', 15)
            old_task = self.state.task_id
            self.goal(0.8, 0.2)
            self.until(lambda: self.phase('DONE') and self.state.standstill and self.state.task_id != old_task, 'Nav2 goal did not stop')
            x, y, _ = pose(self.odom)
            assert math.hypot(x-0.8, y-0.2) <= 0.08, self.odom
            print(f'PASS Nav2 map goal, pose=({x:.3f}, {y:.3f}) m, measured standstill')
        elif scenario == 'conflict':
            self.call(self.straight)
            self.until(lambda: self.phase('ACTIVE') and self.state.enabled, 'No autonomous motion')
            # The extra request publisher is on the THOR graph. The coordinator
            # detects the conflict and revokes the session across the bridge.
            self.conflict = self.create_publisher(DriveRequest, '/drive/request', 1)
            self.until(lambda: self.phase('HOLD') and self.state.standstill, 'Conflict did not stop')
            print(f'PASS duplicate writer, {self.state.reason}, measured standstill')
        else:
            self.call(self.straight)
            self.until(lambda: self.phase('ACTIVE') and 0.071 <= self.state.output.linear.x <= 0.073,
                       'Expected 40% straight output (0.072 m/s)')
            if scenario == 'risk':
                self.risk.publish(String(data='DANGER_WARNING'))
                self.until(lambda: self.phase('ACTIVE') and 0.017 <= self.state.output.linear.x <= 0.019,
                           'VQA warning did not reduce velocity', 3)
                self.risk.publish(String(data='STOP'))
            elif scenario == 'loss':
                self.risk.publish(String(data='OFFLINE'))
            else:
                self.call(self.cancel)
            self.until(lambda: self.phase('HOLD') and self.state.standstill, 'No measured HOLD standstill', 5)
            reason = self.state.reason
            self.risk.publish(String(data='CONTINUE'))
            self.call(self.straight, expect=False)
            assert self.phase('HOLD') and not self.state.enabled
            print(f'PASS {scenario}: {reason}, measured standstill and new task refused while held')


if __name__ == '__main__':
    import sys

    rclpy.init(args=[])
    node = Acceptance()
    try:
        node.run(sys.argv[1])
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
