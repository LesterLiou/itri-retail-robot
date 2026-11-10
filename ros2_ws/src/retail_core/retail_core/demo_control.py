"""Explicit CLI for the isolated simulation; it never publishes velocity."""
import argparse
import time
import rclpy
from rclpy.node import Node
from std_srvs.srv import Trigger,SetBool


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command',choices=['navigate','run','place','cancel','reset','obstacle','clear'])
    args,ros_args = parser.parse_known_args()
    rclpy.init(args=ros_args)
    node = Node('retail_demo_control')
    if args.command in ('obstacle','clear'):
        service,type_ = '/demo/set_obstacle',SetBool
        req = SetBool.Request(data=args.command=='obstacle')
    else:
        service,type_ = '/mission/'+args.command,Trigger
        req = Trigger.Request()
    client = node.create_client(type_,service)
    if not client.wait_for_service(timeout_sec=10):
        raise RuntimeError(f'{service} unavailable')
    future = client.call_async(req)
    rclpy.spin_until_future_complete(node,future,timeout_sec=10)
    if not future.done() or not future.result().success:
        raise RuntimeError(str(future.result()) if future.done() else 'service timed out')
    print(future.result().message)
    node.destroy_node()
    rclpy.try_shutdown()

