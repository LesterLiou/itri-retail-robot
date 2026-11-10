"""Original monitor layout, supplied entirely by ROS images and advisories."""
import json
import threading
import time
from types import SimpleNamespace

from .vqa_monitor_view import QApplication, Monitor
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from cv_bridge import CvBridge
from sensor_msgs.msg import Image
from std_msgs.msg import String
from retail_interfaces.msg import RiskAdvisory
from lightweight_risk_vqa.robot_vqa import ROBOT_QUERY
from .common import seconds, now


class RosPipeline(Node):
    def __init__(self):
        super().__init__('vqa_ui')
        camera = self.declare_parameter('camera','chest').value
        self.args = SimpleNamespace(query=ROBOT_QUERY,no_segmentation=True,
            inference_mode='yolo-assisted',vqa_backend='embedded',max_result_age_s=2.0)
        self.lock = threading.Lock()
        self.paused = False
        self.image = self.image_stamp = self.risk = None
        self.details = {}
        self.frozen = None
        self.bridge = CvBridge()
        self.create_subscription(Image,f'/cameras/{camera}/color/image_raw',self.receive_image,qos_profile_sensor_data)
        self.create_subscription(RiskAdvisory,f'/perception/vqa/{camera}',self.receive_risk,10)
        self.create_subscription(String,f'/perception/vqa/{camera}/details',self.receive_details,10)

    def receive_image(self,msg):
        try:
            image = self.bridge.imgmsg_to_cv2(msg,'bgr8').copy()
        except Exception:
            return
        with self.lock:
            self.image,self.image_stamp = image,seconds(msg.header.stamp)

    def receive_risk(self,msg):
        with self.lock:
            self.risk = msg

    def receive_details(self,msg):
        try:
            data=json.loads(msg.data)
        except (ValueError,TypeError):
            return
        with self.lock:
            self.details = data

    def set_query(self,query):
        # Original checkpoint-compatible prompt is fixed until offline validation.
        pass

    def stop(self):
        pass  # Closing a monitor must not stop ROS perception or issue motion commands.

    def snapshot(self):
        with self.lock:
            image,stamp,risk,details = self.image,self.image_stamp,self.risk,dict(self.details)
        current = now(self)
        age = current-seconds(risk.header.stamp) if risk else float('inf')
        frame_age = current-stamp if stamp is not None else float('inf')
        valid = bool(risk and risk.valid and 0<=age<=min(2.0,seconds(risk.valid_for)) and 0<=frame_age<=2.0)
        action = risk.action if valid else 'DATA_LOSS'
        reason = risk.reason if risk else 'Waiting for ROS input'
        if not valid and not reason:reason='ROS image/result stale or invalid'
        decision = dict(action=action,reason_code=reason or 'validated ROS advisory',
            data_available=valid,decision_authority='embedded_vqa',motion='UNCERTAIN')
        matched = bool(risk and abs(float(details.get('stamp',-1))-seconds(risk.header.stamp))<1e-6)
        metrics = dict(details.get('metrics',{})) if matched else {}
        parsed = None
        if risk and risk.raw_response:
            try:parsed=json.loads(risk.raw_response)
            except ValueError:pass
        if self.paused:
            if self.frozen is None:self.frozen=image.copy() if image is not None else None
            image=self.frozen
        else:self.frozen=None
        epoch = risk.source_id if risk else 'waiting'
        clock = time.monotonic()
        return dict(status='ROS 2 monitor — display paused; perception continues' if self.paused else 'ROS 2 images → YOLO-assisted Qwen → timestamped advisory',
            decision=decision,frame=image,overlay=None,frame_index=stamp or 0,loop=epoch,
            frame_at=clock-frame_age,segmentation_evidence_at=clock-age if matched else None,
            segmentation_context=(epoch,ROBOT_QUERY) if matched else None,
            requested_query=ROBOT_QUERY,segmentation_query=ROBOT_QUERY,segmentation_index=None,
            vqa_index=seconds(risk.header.stamp) if risk else None,vqa_evidence_at=clock-age,
            metrics=metrics,vqa_latency_ms=details.get('inference_ms') if matched else None,
            vqa_model=risk.model_id if risk else 'waiting',vqa_parsed=parsed,
            vqa_raw=risk.raw_response if risk else '',vqa_error=reason if not valid else '',
            pipeline_error='',vqa_yolo_hints=details.get('yolo_hints') if matched else None,
            segmentation_frame=None)


def main():
    rclpy.init()
    app=QApplication([])
    node=RosPipeline()
    thread=threading.Thread(target=rclpy.spin,args=(node,),daemon=True)
    thread.start()
    window=Monitor(node)
    window.setWindowTitle('Light Foundation ConvLSTM + VQA — ROS 2 Monitor')
    window.show()
    try:
        app.exec_()
    finally:
        rclpy.try_shutdown()
        thread.join(timeout=3)
        node.destroy_node()
