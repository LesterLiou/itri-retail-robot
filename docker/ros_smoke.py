"""Exchange one ROS 2 message between two processes inside the smoke container."""

import subprocess
import sys
import time

import rclpy
from rclpy.executors import ExternalShutdownException
from std_msgs.msg import String


TOPIC = "/itri_docker_smoke"
PAYLOAD = "itri-docker-ready"
TIMEOUT_SECONDS = 15


def run_node(role):
    rclpy.init(args=[])
    node = rclpy.create_node(f"itri_smoke_{role}")
    try:
        deadline = time.monotonic() + TIMEOUT_SECONDS
        if role == "pub":
            publisher = node.create_publisher(String, TOPIC, 10)
            message = String(data=PAYLOAD)
            while time.monotonic() < deadline:
                publisher.publish(message)
                rclpy.spin_once(node, timeout_sec=0.1)
        else:
            received = []
            subscription = node.create_subscription(String, TOPIC, received.append, 10)
            while time.monotonic() < deadline:
                rclpy.spin_once(node, timeout_sec=0.1)
                if any(message.data == PAYLOAD for message in received):
                    return 0
                received.clear()
            node.destroy_subscription(subscription)
            print("ROS 2 smoke timed out waiting for the expected message", file=sys.stderr)
            return 1
        return 0
    except ExternalShutdownException:
        # SIGTERM from the parent is the normal publisher teardown path.
        return 0
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


def main():
    if len(sys.argv) == 2 and sys.argv[1] in {"pub", "sub"}:
        return run_node(sys.argv[1])

    worker = [sys.executable, __file__]
    with subprocess.Popen([*worker, "pub"]) as publisher:
        try:
            subscriber = subprocess.run([*worker, "sub"], timeout=TIMEOUT_SECONDS + 5)
        except subprocess.TimeoutExpired:
            print("ROS 2 smoke subscriber failed to exit", file=sys.stderr)
            return 1
        finally:
            if publisher.poll() is None:
                publisher.terminate()
            try:
                publisher.wait(timeout=5)
            except subprocess.TimeoutExpired:
                publisher.kill()
                publisher.wait()
                print("ROS 2 smoke publisher failed to exit", file=sys.stderr)
                return 1
        if subscriber.returncode:
            return 1
        if publisher.returncode:
            print(f"ROS 2 smoke publisher exited with {publisher.returncode}", file=sys.stderr)
            return 1

    print("PASS: ROS 2 message received between two container processes", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
