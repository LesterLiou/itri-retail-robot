import math
import rclpy
from rclpy.executors import ExternalShutdownException
from visualization_msgs.msg import Marker
from builtin_interfaces.msg import Duration


def seconds(stamp):
    return stamp.sec + stamp.nanosec * 1e-9


def duration(value):
    return Duration(sec=int(value), nanosec=int((value % 1) * 1e9))


def now(node):
    return node.get_clock().now().nanoseconds * 1e-9


def finite_joints(message, required):
    if message is None or len(message.name)!=len(message.position):
        return False
    values = dict(zip(message.name,message.position))
    return len(values)==len(message.name) and all(name in values and math.isfinite(values[name]) for name in required)


def text_marker(node, label, marker_id=0, z=1.9, color=(0.2, 0.9, 1.0)):
    m = Marker()
    m.header.frame_id = 'base_link'
    m.header.stamp = node.get_clock().now().to_msg()
    m.ns, m.id, m.type, m.action = 'retail_status', marker_id, Marker.TEXT_VIEW_FACING, Marker.ADD
    m.pose.position.z = z
    m.pose.orientation.w = 1.0
    m.scale.z = 0.12
    m.color.r, m.color.g, m.color.b, m.color.a = *color, 1.0
    m.text = label
    return m


def run(node_type):
    rclpy.init()
    node = node_type()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()

