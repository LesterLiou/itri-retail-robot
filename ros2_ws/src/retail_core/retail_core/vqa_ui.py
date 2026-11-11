"""Original monitor layout, supplied entirely by ROS images and advisories."""
import json
import signal
import threading
import time
from types import SimpleNamespace

from .vqa_monitor_view import QApplication, Monitor, QTimer
import rclpy
from rclpy.node import Node
from rclpy.executors import ExternalShutdownException
from rclpy.signals import SignalHandlerOptions
from rclpy.qos import qos_profile_sensor_data
from cv_bridge import CvBridge
from sensor_msgs.msg import CompressedImage, Image
from nav_msgs.msg import Odometry
from std_msgs.msg import String
from retail_interfaces.msg import RiskAdvisory,GateState
from lightweight_risk_vqa.robot_vqa import ROBOT_QUERY
from .common import seconds, now


class RosPipeline(Node):
    def __init__(self):
        super().__init__('vqa_ui')
        camera = self.declare_parameter('camera','chest').value
        transport = self.declare_parameter('image_transport', 'raw').value
        if transport not in ('raw', 'compressed'):
            raise ValueError('image_transport must be raw or compressed')
        image_type, suffix = (CompressedImage, '/compressed') if transport == 'compressed' else (Image, '')
        self.args = SimpleNamespace(query=ROBOT_QUERY,no_segmentation=True,
            inference_mode='yolo-assisted',vqa_backend='embedded',max_result_age_s=2.0)
        self.lock = threading.Lock()
        self.paused = False
        self.image = self.image_stamp = self.risk = None
        self.details = {}
        self.last_raw = ''
        self.last_model = 'waiting'
        self.last_valid = None
        self.gate=None
        self.mission={}
        self.odom=None
        self.frozen = None
        self.bridge = CvBridge()
        self.create_subscription(image_type, f'/cameras/{camera}/color/image_raw{suffix}',
                                 self.receive_image, qos_profile_sensor_data)
        self.create_subscription(RiskAdvisory,f'/perception/vqa/{camera}',self.receive_risk,10)
        self.create_subscription(String,f'/perception/vqa/{camera}/details',self.receive_details,10)
        self.create_subscription(GateState,'/base/gate_state',lambda m:self.save('gate',m),10)
        self.create_subscription(Odometry,'/odom',lambda m:self.save('odom',m),10)
        self.create_subscription(String,'/mission/state',self.receive_mission,10)

    def save(self,name,value):
        with self.lock:setattr(self,name,value)

    def receive_mission(self,msg):
        try:self.save('mission',json.loads(msg.data))
        except ValueError:pass

    def receive_image(self,msg):
        try:
            if isinstance(msg, CompressedImage):
                image = self.bridge.compressed_imgmsg_to_cv2(msg, 'bgr8')
            else:
                image = self.bridge.imgmsg_to_cv2(msg, 'bgr8').copy()
        except Exception:
            return
        with self.lock:
            self.image,self.image_stamp = image,seconds(msg.header.stamp)

    def receive_risk(self,msg):
        with self.lock:
            self.risk = msg
            age=now(self)-seconds(msg.header.stamp)
            if msg.valid and 0<=age<=min(2.0,seconds(msg.valid_for)):
                self.last_valid=(msg.action,seconds(msg.header.stamp),msg.source_id)
            if msg.raw_response:
                self.last_raw,self.last_model=msg.raw_response,msg.model_id

    def receive_details(self,msg):
        try:
            data=json.loads(msg.data)
        except (ValueError,TypeError):
            return
        with self.lock:
            if 'inference_mode' in data:self.details = data

    def set_query(self,query):
        # Original checkpoint-compatible prompt is fixed until offline validation.
        pass

    def stop(self):
        pass  # Closing a monitor must not stop ROS perception or issue motion commands.

    def snapshot(self):
        with self.lock:
            image,stamp,risk,details = self.image,self.image_stamp,self.risk,dict(self.details)
            last_raw,last_model=self.last_raw,self.last_model
            last_valid=self.last_valid
            gate,mission,odom=self.gate,dict(self.mission),self.odom
        current = now(self)
        age = current-seconds(risk.header.stamp) if risk else float('inf')
        frame_age = current-stamp if stamp is not None else float('inf')
        valid = bool(risk and risk.valid and 0<=age<=min(2.0,seconds(risk.valid_for)) and 0<=frame_age<=2.0)
        action = risk.action if valid else 'DATA_LOSS'
        reason = risk.reason if risk else 'Waiting for ROS input'
        if not valid and not reason:reason='ROS image/result stale or invalid'
        decision = dict(action=action,reason_code=reason or 'validated ROS advisory',
            data_available=valid,decision_authority='embedded_vqa',motion='UNCERTAIN')
        matched = bool(risk and details.get('source_id')==risk.source_id and
            abs(float(details.get('stamp',-1))-seconds(risk.header.stamp))<1e-6)
        metrics = dict(details.get('metrics',{}))
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
        source='SYNTHETIC fixture' if risk and risk.synthetic else (risk.source_id.split(':',1)[0] if risk else 'waiting')
        mode=details.get('inference_mode','waiting')
        gate_fresh=bool(gate and 0<=current-seconds(gate.header.stamp)<0.5)
        permission=('HOLD' if gate.hold else ('ENABLED' if gate.enabled else 'DISABLED')) if gate_fresh else 'UNKNOWN'
        position=f' x={odom.pose.pose.position.x:.2f}, y={odom.pose.pose.position.y:.2f}' if odom else ''
        navigation=f'Nav2 {mission.get("phase","—")} / {permission}{position}'
        history=(dict(action=last_valid[0],age_sec=max(0,current-last_valid[1]),
            same_source=bool(risk and risk.source_id==last_valid[2])) if last_valid else None)
        return dict(status=('Display paused; ROS perception continues' if self.paused else f'ROS 2 — {mode} / {source}')+' | '+navigation,
            last_valid=history,
            health_reason=reason if not valid else 'fresh, schema-validated observation',
            capture_age_sec=age,
            decision=decision,frame=image,overlay=None,frame_index=stamp or 0,loop=epoch,
            frame_at=clock-frame_age,segmentation_evidence_at=clock-age if matched else None,
            segmentation_context=(epoch,ROBOT_QUERY) if matched else None,
            requested_query=ROBOT_QUERY,segmentation_query=ROBOT_QUERY,segmentation_index=None,
            vqa_index=seconds(risk.header.stamp) if risk else None,vqa_evidence_at=clock-age,
            metrics=metrics,vqa_latency_ms=details.get('inference_ms'),
            vqa_model=last_model,vqa_parsed=parsed,
            vqa_raw=last_raw,vqa_error=reason if not valid else '',
            pipeline_error='',vqa_yolo_hints=details.get('yolo_hints') if matched else None,
            segmentation_frame=None)


def main():
    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    closing=[False]
    def close_requested(signum,frame):closing[0]=True
    signal.signal(signal.SIGINT,close_requested)
    signal.signal(signal.SIGTERM,close_requested)
    app=QApplication([])
    node=RosPipeline()
    def spin():
        try:rclpy.spin(node)
        except ExternalShutdownException:pass
    thread=threading.Thread(target=spin,daemon=True)
    thread.start()
    window=Monitor(node)
    window.setWindowTitle('Light Foundation ConvLSTM + VQA — ROS 2 Monitor')
    window.show()
    lifecycle=QTimer()
    lifecycle.timeout.connect(lambda:app.quit() if closing[0] or not rclpy.ok() else None)
    lifecycle.start(100)
    try:
        app.exec_()
    finally:
        rclpy.try_shutdown()
        thread.join(timeout=3)
        node.destroy_node()
