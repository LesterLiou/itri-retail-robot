"""Kinematic CPU simulator. No physical driver is imported or loaded."""
import math
import time
from geometry_msgs.msg import Twist, TransformStamped
from nav_msgs.msg import Odometry
from sensor_msgs.msg import LaserScan
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from tf2_ros import TransformBroadcaster, StaticTransformBroadcaster
from .common import run
from .world import load_world
import numpy as np


class FakeBase(Node):
    def __init__(self):
        super().__init__('fake_tidybot')
        self.x = self.y = self.yaw = 0.0
        self.bounds=[(-2.0,4.0,-2.0,2.0),(2.9,3.2,-0.9,0.9)]
        world_file=self.declare_parameter('world_file','').value
        if world_file:
            world=load_world(world_file)
            self.x,self.y,self.yaw=world['initial_pose']
            self.bounds=[world['bounds']]+world['obstacles']
        self.cmd = Twist()
        self.received = 0.0
        self.last = time.monotonic()
        self.odom = self.create_publisher(Odometry, '/odom', 10)
        self.scan = self.create_publisher(LaserScan, '/scan', qos_profile_sensor_data)
        self.tf = TransformBroadcaster(self)
        self.static = StaticTransformBroadcaster(self)
        fixed = TransformStamped()
        fixed.header.frame_id, fixed.child_frame_id = 'map', 'odom'
        fixed.header.stamp = self.get_clock().now().to_msg()
        fixed.transform.rotation.w = 1.0
        self.static.sendTransform(fixed)
        self.create_subscription(Twist, '/base/cmd_vel', self.command, 1)
        self.create_timer(0.02, self.tick)
        self.create_timer(0.1, self.laser)

    def command(self, msg):
        self.cmd = msg
        self.received = time.monotonic()

    def tick(self):
        t = time.monotonic()
        dt, self.last = min(t-self.last, 0.05), t
        cmd = self.cmd if t-self.received < 0.25 else Twist()
        self.x += (math.cos(self.yaw)*cmd.linear.x-math.sin(self.yaw)*cmd.linear.y)*dt
        self.y += (math.sin(self.yaw)*cmd.linear.x+math.cos(self.yaw)*cmd.linear.y)*dt
        self.yaw += cmd.angular.z*dt
        msg = Odometry()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id, msg.child_frame_id = 'odom', 'base_link'
        msg.pose.pose.position.x, msg.pose.pose.position.y = self.x, self.y
        msg.pose.pose.orientation.z, msg.pose.pose.orientation.w = math.sin(self.yaw/2), math.cos(self.yaw/2)
        msg.twist.twist = cmd
        self.odom.publish(msg)
        tf = TransformStamped()
        tf.header, tf.child_frame_id = msg.header, msg.child_frame_id
        tf.transform.translation.x, tf.transform.translation.y = self.x, self.y
        tf.transform.rotation = msg.pose.pose.orientation
        self.tf.sendTransform(tf)

    def laser(self):
        msg = LaserScan()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = 'base_link'
        msg.angle_min, msg.angle_max = -math.pi, math.pi
        msg.angle_increment = 2*math.pi/359
        msg.range_min, msg.range_max = 0.05, 10.0
        msg.scan_time = 0.1
        # Known simulated room bounds, plus a shelf obstacle in world coordinates.
        bounds = self.bounds
        ranges = []
        for a in np.linspace(-math.pi, math.pi, 360):
            dx, dy = math.cos(a+self.yaw), math.sin(a+self.yaw)
            hits = []
            for xmin,xmax,ymin,ymax in bounds:
                if abs(dx)>1e-8:
                    for edge in (xmin,xmax):
                        d = (edge-self.x)/dx
                        if d>0 and ymin <= self.y+d*dy <= ymax: hits.append(d)
                if abs(dy)>1e-8:
                    for edge in (ymin,ymax):
                        d = (edge-self.y)/dy
                        if d>0 and xmin <= self.x+d*dx <= xmax: hits.append(d)
            ranges.append(float(min(hits)) if hits else float('inf'))
        msg.ranges = ranges
        self.scan.publish(msg)


def main():
    run(FakeBase)

