"""ROS Image -> bounded HTTP worker -> typed, timestamped advisory and RViz image."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import time
import uuid
from urllib.request import Request, urlopen

import cv2
from cv_bridge import CvBridge
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image
from std_msgs.msg import Bool,String
from visualization_msgs.msg import Marker
from retail_interfaces.msg import RiskAdvisory
from .common import run, now, seconds, duration, text_marker


class VQAAdapter(Node):
    def __init__(self):
        super().__init__('vqa_adapter')
        self.cameras = self.declare_parameter('cameras',['chest']).value
        self.backend = self.declare_parameter('backend','http').value
        self.input_source = self.declare_parameter('input_source','ros').value
        self.inference_mode = self.declare_parameter('inference_mode','vqa-only').value
        if self.inference_mode not in ('vqa-only','yolo-assisted'):
            raise ValueError('Unknown VQA inference mode')
        if self.backend not in ('http','fixture'):
            raise ValueError('backend must be http or explicitly synthetic fixture')
        self.url = self.declare_parameter('worker_url','http://vqa-worker:8090/infer').value
        self.epoch = uuid.uuid4().hex
        self.bridge = CvBridge()
        self.latest, self.results, self.processed = {}, {}, {}
        self.pending = None
        self.worker_pool = ThreadPoolExecutor(max_workers=1)
        self.obstacle = False
        self.obstacle_at = 0.0
        self.index = 0
        self.outputs = {c:self.create_publisher(RiskAdvisory,f'/perception/vqa/{c}',10) for c in self.cameras}
        self.details = {c:self.create_publisher(String,f'/perception/vqa/{c}/details',10) for c in self.cameras}
        self.images = {c:self.create_publisher(Image,f'/perception/vqa/{c}/annotated',qos_profile_sensor_data) for c in self.cameras}
        self.markers = self.create_publisher(Marker,'/retail/status_markers',10)
        for c in self.cameras:
            self.create_subscription(Image,f'/cameras/{c}/color/image_raw',lambda msg,c=c:self.receive(c,msg),qos_profile_sensor_data)
        self.create_subscription(Bool,'/demo/obstacle',self.fixture_state,10)
        self.create_timer(0.1,self.tick)

    def fixture_state(self,msg):
        self.obstacle, self.obstacle_at = msg.data,time.monotonic()

    def receive(self,camera,msg):
        old = self.latest.get(camera)
        if old and seconds(msg.header.stamp) == seconds(old.header.stamp):
            return
        if old and seconds(msg.header.stamp) <= seconds(old.header.stamp):
            self.results.pop(camera,None)
            self.processed.pop(camera,None)
        self.latest[camera] = msg  # Depth-one storage; never queue old frames.

    @staticmethod
    def request(url,camera,msg,image,epoch,inference_mode='vqa-only'):
        ok,jpeg = cv2.imencode('.jpg',image,[cv2.IMWRITE_JPEG_QUALITY,95])
        if not ok:
            raise ValueError('JPEG encoding failed')
        payload = json.dumps({'camera_id':camera,'stamp':seconds(msg.header.stamp),
                              'source_epoch':epoch,'inference_mode':inference_mode,'jpeg':base64.b64encode(jpeg).decode()}).encode()
        req = Request(url,data=payload,headers={'Content-Type':'application/json'})
        with urlopen(req,timeout=8.0) as response:
            result = json.loads(response.read(2_000_000))
        if result.get('camera_id')!=camera or abs(float(result.get('stamp',-1))-seconds(msg.header.stamp))>1e-6:
            raise ValueError('worker response does not match camera/capture stamp')
        if result.get('action') not in ('STOP','DANGER_WARNING','CONTINUE','OBSERVE','DATA_LOSS'):
            raise ValueError('unexpected model action')
        return result

    def publish(self,camera,msg,result):
        advisory = RiskAdvisory()
        if msg:
            advisory.header = msg.header
        else:
            advisory.header.frame_id = f'{camera}_color_optical_frame'
        advisory.inference_stamp = self.get_clock().now().to_msg()
        advisory.valid_for = duration(2.0)
        advisory.camera_id = camera
        advisory.action = str(result.get('action','DATA_LOSS'))
        advisory.raw_action = str(result.get('raw_action',advisory.action))
        advisory.valid = bool(result.get('valid',False))
        advisory.synthetic = self.backend == 'fixture' or self.input_source=='synthetic'
        advisory.source_id = f'{self.input_source}:{self.epoch}/{result.get("worker_id","fixture")}'
        advisory.model_id = str(result.get('model_id','synthetic_fixture' if advisory.synthetic else 'unavailable'))
        advisory.prompt_id = str(result.get('prompt_id','none'))
        advisory.observation = str(result.get('observation',''))
        advisory.explanation = str(result.get('explanation',''))
        advisory.raw_response = str(result.get('raw',''))
        advisory.reason = str(result.get('reason',''))
        # Never turn an expired successful result into a fresh clear observation.
        if msg is None or not (0 <= now(self)-seconds(msg.header.stamp) <= 2.0):
            advisory.action, advisory.valid, advisory.reason = 'DATA_LOSS',False,'image/result expired'
        self.outputs[camera].publish(advisory)
        details = dict(result,stamp=seconds(msg.header.stamp) if msg else 0.0,
                       camera_id=camera,source_id=advisory.source_id)
        self.details[camera].publish(String(data=json.dumps(details,allow_nan=False)))
        color = (0.1,0.9,0.3) if advisory.action in ('CONTINUE','NO_DETECT') else (1.0,0.3,0.1)
        self.markers.publish(text_marker(self,f'{camera}: {advisory.action} [{self.backend}]\n{advisory.observation[:75]}',
                                        self.cameras.index(camera),1.8+0.18*self.cameras.index(camera),color))
        if msg:
            try:
                image = self.bridge.imgmsg_to_cv2(msg,'bgr8').copy()
                cv2.rectangle(image,(0,0),(image.shape[1],85),(20,20,20),-1)
                cv2.putText(image,f'{camera}: {advisory.action} / {self.backend}',(10,28),cv2.FONT_HERSHEY_SIMPLEX,0.62,(40,210,255),2)
                cv2.putText(image,advisory.observation[:76],(10,56),cv2.FONT_HERSHEY_SIMPLEX,0.4,(240,240,240),1)
                debug = self.bridge.cv2_to_imgmsg(image,'bgr8')
                debug.header = msg.header
                self.images[camera].publish(debug)
            except Exception as exc:
                self.get_logger().error(f'Image conversion: {exc}')

    def tick(self):
        if self.pending and self.pending[2].done():
            camera,msg,future = self.pending
            try:
                result = future.result()
            except Exception as exc:
                result = {'valid':False,'action':'DATA_LOSS','reason':str(exc)}
            self.results[camera] = (msg,result)
            self.publish(camera,msg,result)
            self.pending = None
        for camera in self.cameras:
            msg = self.latest.get(camera)
            if not msg or not (0 <= now(self)-seconds(msg.header.stamp) <= 2.0):
                self.publish(camera,msg,{'valid':False,'action':'DATA_LOSS','reason':'camera unavailable'})
            elif self.backend == 'http' and camera in self.results:
                evidence,result = self.results[camera]
                if now(self)-seconds(evidence.header.stamp)>2.0:
                    self.publish(camera,evidence,result)
        if self.backend == 'fixture':
            for camera,msg in self.latest.items():
                healthy = time.monotonic()-self.obstacle_at<0.5
                self.publish(camera,msg,{'action':'STOP' if self.obstacle else 'NO_DETECT',
                                        'valid':healthy,'observation':'SYNTHETIC obstacle present' if self.obstacle else 'SYNTHETIC empty aisle',
                                        'reason':'fixture labels, no model inference'})
            return
        if self.pending:
            return
        for _ in self.cameras:
            camera = self.cameras[self.index % len(self.cameras)]
            self.index += 1
            msg = self.latest.get(camera)
            if msg and seconds(msg.header.stamp) != self.processed.get(camera) and 0<=now(self)-seconds(msg.header.stamp)<2.0:
                self.processed[camera] = seconds(msg.header.stamp)
                try:
                    image = self.bridge.imgmsg_to_cv2(msg,'bgr8')
                    future = self.worker_pool.submit(self.request,self.url,camera,msg,image,self.epoch,self.inference_mode)
                    self.pending = (camera,msg,future)
                except Exception as exc:
                    self.publish(camera,msg,{'valid':False,'reason':str(exc)})
                break

    def destroy_node(self):
        self.worker_pool.shutdown(wait=False,cancel_futures=True)
        return super().destroy_node()


def main():
    run(VQAAdapter)

