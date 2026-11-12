"""Check the domain-0 handoff and optionally report live readiness without publishing."""
import argparse
import json
import math
import time

from nav_msgs.msg import Odometry
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan
from std_msgs.msg import Bool

from retail_core.common import now, seconds
from retail_core.tidybot_drive import COMMAND_TTL, ODOM_TTL, valid_odom


OUTPUTS = ('/cmd_vel', '/tidybot/enable', '/tidybot/control_frame')
MANUAL_INPUTS = ('/manual_control/cmd_vel', '/manual_control/tidybot/enable')


def handoff_errors(node):
    for topic in OUTPUTS:
        drivers = node.get_subscriptions_info_by_topic(topic)
        if node.count_publishers(topic) or [driver.node_name for driver in drivers] != ['tidybot_base_node']:
            yield f'{topic}: require zero writers and only the existing base driver subscriber'
    for topic in MANUAL_INPUTS:
        if [publisher.node_name for publisher in node.get_publishers_info_by_topic(topic)] != ['tidybot_control_mux']:
            yield f'{topic}: expected one remapped upstream TidyBot mux'
    if [publisher.node_name for publisher in node.get_publishers_info_by_topic('/odom')] != ['tidybot_base_node']:
        yield '/odom: expected one existing base driver publisher'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', choices=('straight', 'map'))
    args = parser.parse_args()
    rclpy.init(args=[])
    node = Node('tidybot_navigation_preflight')
    try:
        samples = {}
        inputs = {'/odom': Odometry, '/scan': LaserScan, '/tidybot/auto_interlock': Bool}
        if args.audit:
            def remember(topic, msg):
                samples[topic] = (msg, time.monotonic())

            for topic, msg_type in inputs.items():
                node.create_subscription(msg_type, topic, lambda msg, name=topic: remember(name, msg),
                                         qos_profile_sensor_data)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.1)
        errors = list(handoff_errors(node))
        if args.audit:
            def fresh(topic, limit):
                if topic not in samples or node.count_publishers(topic) != 1:
                    return None
                msg, received = samples[topic]
                if time.monotonic() - received >= limit:
                    return None
                if hasattr(msg, 'header') and not 0 <= now(node) - seconds(msg.header.stamp) < limit:
                    return None
                return msg

            odom = fresh('/odom', ODOM_TTL)
            scan = fresh('/scan', ODOM_TTL)
            interlock = fresh('/tidybot/auto_interlock', COMMAND_TTL)
            checks = {
                '/odom': bool(odom and valid_odom(odom)),
                '/tidybot/auto_interlock': bool(interlock and interlock.data),
            }
            if args.audit == 'map':
                checks['/scan'] = bool(scan and scan.header.frame_id and any(
                    math.isfinite(value) and scan.range_min <= value <= scan.range_max for value in scan.ranges
                ))
            errors.extend(f'{topic}: required live feedback unavailable, invalid, or false'
                          for topic, good in checks.items() if not good)
            print(json.dumps(dict(
                mode=args.audit, handoff_and_local_inputs_ready=not errors, errors=errors,
                publishers={topic: [info.node_name for info in node.get_publishers_info_by_topic(topic)]
                            for topic in (*OUTPUTS, *MANUAL_INPUTS, *inputs)},
                scan_frame=scan.header.frame_id if scan else None,
            ), indent=2), flush=True)
            if errors:
                raise SystemExit(1)
        elif errors:
            raise SystemExit('\n'.join(errors))
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
