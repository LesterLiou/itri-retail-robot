"""Isolated local-domain manual handoff, disconnect and restart assertions."""
import sys
import time

from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, qos_profile_sensor_data
from std_msgs.msg import Bool

from retail_interfaces.msg import DriveState
from retail_core.common import seconds


def main(scenario):
    rclpy.init(args=[])
    node = Node('navigation_local_acceptance')
    state = None
    still_since = previous_still_duration = 0.0

    def update(msg):
        nonlocal state
        state = msg

    def odometry(msg):
        nonlocal still_since, previous_still_duration
        stamp = seconds(msg.header.stamp)
        velocity = msg.twist.twist
        if all(abs(v) < 0.01 for v in (velocity.linear.x, velocity.linear.y, velocity.angular.z)):
            still_since = still_since or stamp
        elif still_since:
            previous_still_duration = stamp-still_since
            still_since = 0.0

    node.create_subscription(Odometry, '/odom', odometry, qos_profile_sensor_data)
    node.create_subscription(DriveState, '/drive/state', update, qos_profile_sensor_data)
    enable = node.create_publisher(Bool, '/manual_control/tidybot/enable',
                                   QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))
    command = node.create_publisher(Twist, '/manual_control/cmd_vel', 1)

    def until(condition, timeout=5, publish=False):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            if publish:
                enable.publish(Bool(data=True))
                msg = Twist()
                msg.linear.x = 0.04
                command.publish(msg)
            rclpy.spin_once(node, timeout_sec=0.02)
            if state and state.hardware:
                raise RuntimeError('Acceptance cannot operate a physical gate')
            if state and condition(state):
                return
        raise RuntimeError(f'{scenario}: expected state missing: {state}')

    try:
        if scenario == 'active':
            until(lambda s: s.phase == 'ACTIVE' and s.owner == 'AUTO' and 0.071 <= s.output.linear.x <= 0.073)
        elif scenario == 'handoff':
            until(lambda s: s.phase == 'ACTIVE' and s.owner == 'AUTO')
            previous_still_duration = 0.0
            until(lambda s: s.phase == 'HANDOFF' and s.owner == 'LOCAL_MUX'
                  and 0.03 < s.output.linear.x < 0.05 and previous_still_duration >= 0.48, publish=True)
            enable.publish(Bool(data=False))
            until(lambda s: s.phase == 'HANDOFF' and s.standstill and not s.enabled)
            print('PASS handoff: local input received authority after measured standstill')
        elif scenario == 'restarted':
            until(lambda s: s.phase == 'IDLE' and s.standstill and not s.task_id and not s.enabled)
            deadline = time.monotonic()+1
            while time.monotonic() < deadline:
                rclpy.spin_once(node, timeout_sec=0.05)
                assert state.phase == 'IDLE' and not state.enabled, state
            print('PASS restart: old mission did not resume')
        elif scenario == 'disconnected':
            until(lambda s: s.phase == 'HOLD' and s.standstill and not s.enabled)
            assert 'expired' in state.reason, state
            print(f'PASS THOR disconnect: {state.reason}, measured standstill')
        else:
            raise ValueError(f'Unknown scenario: {scenario}')
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main(sys.argv[1])
