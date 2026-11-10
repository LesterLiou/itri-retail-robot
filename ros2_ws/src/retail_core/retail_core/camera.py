"""Four ROS camera interfaces, with explicit synthetic or video sources."""
import cv2
import numpy as np
from cv_bridge import CvBridge
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image, CameraInfo
from std_msgs.msg import Bool
from std_srvs.srv import SetBool
from .common import run


CAMERAS = ('chest', 'left_shoulder', 'right_shoulder', 'left_wrist')


class CameraSource(Node):
    def __init__(self):
        super().__init__('camera_source')
        path = self.declare_parameter('video_path', '').value
        self.bridge = CvBridge()
        self.capture = cv2.VideoCapture(path) if path else None
        if self.capture is not None and not self.capture.isOpened():
            raise RuntimeError(f'Cannot open video: {path}')
        self.obstacle = False
        self.frame = 0
        self.pubs = {c:self.create_publisher(Image,f'/cameras/{c}/color/image_raw',qos_profile_sensor_data) for c in CAMERAS}
        self.infos = {c:self.create_publisher(CameraInfo,f'/cameras/{c}/color/camera_info',qos_profile_sensor_data) for c in CAMERAS}
        self.fixture = self.create_publisher(Bool,'/demo/obstacle',10)
        self.create_service(SetBool,'/demo/set_obstacle',self.set_obstacle)
        self.timer = self.create_timer(0.1,self.tick)

    def set_obstacle(self, req, res):
        self.obstacle = req.data
        res.success, res.message = True, 'synthetic scenario updated; not a real detector'
        return res

    def tick(self):
        self.frame += 1
        stamp = self.get_clock().now().to_msg()
        video = None
        if self.capture is not None:
            ok, video = self.capture.read()
            if not ok:
                # Explicit new playback epoch; adapter sees source timestamps continue but
                # worker state is reset on the ROS source_id epoch parameter/restart.
                self.get_logger().warn('Video complete: stopping stream; restart for a new epoch')
                self.destroy_timer(self.timer)
                return
        for camera in CAMERAS:
            if camera == 'chest' and video is not None:
                rgb = cv2.resize(video,(640,480))
            else:
                rgb = np.full((480,640,3),(45,40,30),dtype=np.uint8)
                for i in range(1,5):
                    cv2.line(rgb,(0,80*i),(640,80*i),(90,100,110),2)
                cv2.putText(rgb,f'SYNTHETIC / {camera}',(15,35),cv2.FONT_HERSHEY_SIMPLEX,0.65,(255,255,255),2)
                if self.obstacle:
                    cv2.rectangle(rgb,(230,130),(410,440),(40,40,230),-1)
                    cv2.putText(rgb,'SIM OBSTACLE',(215,115),cv2.FONT_HERSHEY_SIMPLEX,0.6,(80,80,255),2)
                cv2.putText(rgb,f'frame {self.frame}',(15,460),cv2.FONT_HERSHEY_SIMPLEX,0.5,(230,230,230),1)
            msg = self.bridge.cv2_to_imgmsg(rgb,encoding='bgr8')
            msg.header.stamp, msg.header.frame_id = stamp, f'{camera}_color_optical_frame'
            self.pubs[camera].publish(msg)
            info = CameraInfo()
            info.header = msg.header
            info.width, info.height = 640,480
            info.distortion_model, info.d = 'plumb_bob',[0.0]*5
            info.k = [500.0,0.0,320.0,0.0,500.0,240.0,0.0,0.0,1.0]
            info.r = [1.0,0.0,0.0,0.0,1.0,0.0,0.0,0.0,1.0]
            info.p = [500.0,0.0,320.0,0.0,0.0,500.0,240.0,0.0,0.0,0.0,1.0,0.0]
            self.infos[camera].publish(info)
        self.fixture.publish(Bool(data=self.obstacle))


def main():
    run(CameraSource)

