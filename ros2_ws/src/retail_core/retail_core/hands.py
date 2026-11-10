"""Reuse the upstream RGB hand detector; keep unknown and empty distinct."""
from concurrent.futures import ThreadPoolExecutor
import time
import uuid
import cv2
from cv_bridge import CvBridge
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image
from std_msgs.msg import Bool
from vision_msgs.msg import Detection2DArray, Detection2D, ObjectHypothesisWithPose
from visualization_msgs.msg import Marker
from retail_interfaces.msg import ObstacleState
from .common import run, now, seconds, duration, text_marker


class HandAdapter(Node):
    def __init__(self):
        super().__init__('hand_adapter')
        self.backend = self.declare_parameter('backend','mediapipe').value
        self.input_source = self.declare_parameter('input_source','ros').value
        if self.backend not in ('fixture','mediapipe'):
            raise ValueError('Select fixture or mediapipe')
        self.bridge, self.latest, self.result = CvBridge(),None,None
        self.pending = None
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.detector = None
        self.error = ''
        self.source = self.input_source+'/'+self.backend+'/'+uuid.uuid4().hex
        self.fixture_present,self.fixture_at = False,0.0
        self.state = self.create_publisher(ObstacleState,'/perception/hand/state',10)
        self.boxes = self.create_publisher(Detection2DArray,'/perception/hand/detections',10)
        self.image = self.create_publisher(Image,'/perception/hand/annotated',qos_profile_sensor_data)
        self.markers = self.create_publisher(Marker,'/retail/status_markers',10)
        self.create_subscription(Image,'/cameras/chest/color/image_raw',self.receive,qos_profile_sensor_data)
        self.create_subscription(Bool,'/demo/obstacle',self.fixture,10)
        self.create_timer(0.1,self.tick)

    def receive(self,msg):
        self.latest = msg

    def fixture(self,msg):
        self.fixture_present,self.fixture_at = msg.data,time.monotonic()

    def infer(self,msg):
        if self.detector is None:
            from realtime_safety.edgetam_tracker.hand_semantic_gate import MediaPipeHandDetector,MediaPipeHandDetectorConfig
            self.detector = MediaPipeHandDetector(MediaPipeHandDetectorConfig(model_complexity=1))
            self.detector.load()
        rgb = self.bridge.imgmsg_to_cv2(msg,'rgb8')
        return self.detector.infer_rgb(rgb)

    def publish(self,msg,detections,error=''):
        state = ObstacleState()
        if msg: state.header = msg.header
        state.valid_for = duration(0.5)
        state.synthetic = self.backend == 'fixture' or self.input_source=='synthetic'
        state.source_id = self.source
        state.valid = bool(msg and not error and 0<=now(self)-seconds(msg.header.stamp)<=0.5)
        state.occupied = bool(detections) if detections is not None else False
        state.reason = error or ('hand present: 2D only; pause' if state.occupied else 'valid RGB observation; no hand detected')
        if not state.valid:
            state.reason = error or 'hand observation unavailable/expired'
        self.state.publish(state)
        self.markers.publish(text_marker(self,'HAND: '+('UNKNOWN' if not state.valid else 'HOLD' if state.occupied else 'no detection')+
                                        f' [{self.backend}]',30,2.5,(1.0,0.2,0.2) if state.occupied or not state.valid else (0.2,1.0,0.3)))
        if not msg or detections is None:
            return
        boxes = Detection2DArray()
        boxes.header = msg.header
        bgr = self.bridge.imgmsg_to_cv2(msg,'bgr8').copy()
        for det in detections:
            x1,y1,x2,y2 = [float(x) for x in det.bbox_xyxy]
            d = Detection2D()
            d.header = msg.header
            d.bbox.center.position.x,d.bbox.center.position.y = (x1+x2)/2,(y1+y2)/2
            d.bbox.size_x,d.bbox.size_y = x2-x1,y2-y1
            h = ObjectHypothesisWithPose()
            h.hypothesis.class_id,h.hypothesis.score = 'hand',float(det.confidence)
            d.results.append(h)
            boxes.detections.append(d)
            cv2.rectangle(bgr,(int(x1),int(y1)),(int(x2),int(y2)),(0,0,255),2)
        self.boxes.publish(boxes)
        image = self.bridge.cv2_to_imgmsg(bgr,'bgr8')
        image.header = msg.header
        self.image.publish(image)

    def tick(self):
        msg = self.latest
        if self.backend == 'fixture':
            if not msg or time.monotonic()-self.fixture_at>0.5:
                self.publish(msg,None,'fixture camera unavailable')
            else:
                from types import SimpleNamespace
                detections = [SimpleNamespace(bbox_xyxy=[230,130,410,440],confidence=1.0)] if self.fixture_present else []
                self.publish(msg,detections)
            return
        if self.pending and self.pending[1].done():
            frame,future = self.pending
            try:
                self.result = (frame,future.result(),'')
            except Exception as exc:
                self.result = (frame,None,str(exc))
                self.detector = None
            self.pending = None
        if self.result:
            self.publish(*self.result)
        else:
            self.publish(msg,None,'detector loading/unavailable')
        if not self.pending and msg and 0<=now(self)-seconds(msg.header.stamp)<0.5:
            if not self.result or seconds(msg.header.stamp)>seconds(self.result[0].header.stamp):
                self.pending = (msg,self.pool.submit(self.infer,msg))

    def destroy_node(self):
        self.pool.shutdown(wait=False,cancel_futures=True)
        return super().destroy_node()


def main():
    run(HandAdapter)

