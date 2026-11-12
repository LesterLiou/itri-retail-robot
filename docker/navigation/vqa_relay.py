"""Carry real VQA from the large-data observation domain to the fake UDP domain."""
import os

import rclpy
from rclpy.context import Context
from rclpy.executors import ExternalShutdownException, SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rclpy.signals import SignalHandlerOptions

from retail_interfaces.msg import RiskAdvisory


def main():
    fake_domain = int(os.environ['NAV_DOMAIN_ID'])
    if fake_domain in (0, 84):
        raise SystemExit('The VQA relay destination must be an isolated fake domain')

    source_context, target_context = Context(), Context()
    rclpy.init(context=source_context, domain_id=84, signal_handler_options=SignalHandlerOptions.NO)
    source = Node('thor_vqa_relay_source', context=source_context)
    os.environ['FASTDDS_BUILTIN_TRANSPORTS'] = 'UDPv4'
    rclpy.init(context=target_context, domain_id=fake_domain, signal_handler_options=SignalHandlerOptions.NO)
    target = Node('thor_vqa_relay_target', context=target_context)
    topic = '/perception/vqa/tidybot'
    publisher = target.create_publisher(RiskAdvisory, topic, qos_profile_sensor_data)
    source.create_subscription(RiskAdvisory, topic, publisher.publish, qos_profile_sensor_data)
    executor = SingleThreadedExecutor(context=source_context)
    executor.add_node(source)
    try:
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        executor.shutdown()
        source.destroy_node()
        target.destroy_node()
        source_context.try_shutdown()
        target_context.try_shutdown()


if __name__ == '__main__':
    main()
