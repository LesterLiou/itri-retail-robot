"""MASt3R worker transport. Uncalibrated clouds are visualization data only."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import cv2
from cv_bridge import CvBridge
from urllib.request import Request,urlopen
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image,PointCloud2
from sensor_msgs_py.point_cloud2 import create_cloud_xyz32
from std_msgs.msg import Header
from retail_interfaces.msg import SceneState
from .common import run,now,seconds,duration


class ReconstructionAdapter(Node):
    def __init__(self):
        super().__init__('reconstruction_adapter')
        self.url = self.declare_parameter('worker_url','').value
        self.latest,self.pending,self.last_stamp = None,None,-1.0
        self.reason = 'MASt3R worker not configured; no reconstruction'
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.bridge = CvBridge()
        self.cloud = self.create_publisher(PointCloud2,'/reconstruction/raw_cloud',10)
        self.state = self.create_publisher(SceneState,'/reconstruction/state',10)
        self.create_subscription(Image,'/cameras/chest/color/image_raw',lambda m:setattr(self,'latest',m),qos_profile_sensor_data)
        self.create_timer(0.2,self.tick)

    def request(self,msg):
        image = self.bridge.imgmsg_to_cv2(msg,'bgr8')
        ok,jpeg = cv2.imencode('.jpg',image)
        if not ok: raise ValueError('image encoding failed')
        data = json.dumps({'stamp':seconds(msg.header.stamp),'jpeg':base64.b64encode(jpeg).decode()}).encode()
        with urlopen(Request(self.url,data=data,headers={'Content-Type':'application/json'}),timeout=8.0) as response:
            return json.loads(response.read(8_000_000))

    def tick(self):
        if self.pending and self.pending[1].done():
            msg,future = self.pending
            try:
                result = future.result()
                points = result['points']
                if not points or len(points)>30000:
                    raise ValueError('invalid/oversized cloud')
                self.cloud.publish(create_cloud_xyz32(Header(stamp=msg.header.stamp,frame_id='mast3r_map'),points))
                self.reason = 'raw MASt3R submap only: metric alignment and independent calibration not verified'
            except Exception as exc:
                self.reason = str(exc)
            self.pending = None
        msg = SceneState()
        if self.latest: msg.header = self.latest.header
        msg.valid_for = duration(2.0)
        msg.source_id = 'mast3r_slam'
        # Raw learned geometry cannot grant a planning-scene clearance.
        msg.valid,msg.metric_valid = False,False
        msg.reason = self.reason
        self.state.publish(msg)
        if self.url and not self.pending and self.latest and 0<=now(self)-seconds(self.latest.header.stamp)<2.0:
            stamp = seconds(self.latest.header.stamp)
            if stamp>self.last_stamp:
                self.last_stamp = stamp
                frame = self.latest
                self.pending = (frame,self.pool.submit(self.request,frame))

    def destroy_node(self):
        self.pool.shutdown(wait=False,cancel_futures=True)
        return super().destroy_node()


def main():
    run(ReconstructionAdapter)

