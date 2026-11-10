"""Filter fixed wrist observation candidates with MoveIt state validity and FK."""
import json
from geometry_msgs.msg import PoseStamped
from sensor_msgs.msg import JointState
from std_msgs.msg import String
from std_srvs.srv import Trigger
from moveit_msgs.srv import GetStateValidity,GetPositionFK
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from retail_interfaces.msg import SceneState,GateState
from .common import run,now,seconds


class ViewPlanner(Node):
    def __init__(self):
        super().__init__('view_planner')
        self.scene,self.gate,self.joints = None,None,None
        self.pending = None
        self.candidates = []
        self.target = None
        self.create_subscription(SceneState,'/perception/shelf/state',lambda m:setattr(self,'scene',m),10)
        self.create_subscription(GateState,'/base/gate_state',lambda m:setattr(self,'gate',m),10)
        self.create_subscription(JointState,'/joint_states',lambda m:setattr(self,'joints',m),qos_profile_sensor_data)
        self.validity = self.create_client(GetStateValidity,'/check_state_validity')
        self.fk = self.create_client(GetPositionFK,'/compute_fk')
        self.selected = self.create_publisher(JointState,'/views/selected_joints',10)
        self.pose = self.create_publisher(PoseStamped,'/views/selected_pose',10)
        self.status = self.create_publisher(String,'/views/status',10)
        self.create_service(Trigger,'/views/select',self.select)
        self.create_timer(0.05,self.tick)

    def ready(self):
        return bool(self.scene and self.scene.valid and self.scene.metric_valid and self.joints and
                    self.gate and self.gate.base_inhibited and self.gate.base_standstill and not self.gate.hold and
                    0<=now(self)-seconds(self.joints.header.stamp)<0.5 and
                    0<=now(self)-seconds(self.gate.header.stamp)<0.5)

    def select(self,req,res):
        res.success = bool(self.ready() and not self.pending and self.validity.service_is_ready() and self.fk.service_is_ready())
        res.message = 'candidate evaluation started' if res.success else 'scene/interlock/MoveIt unavailable'
        if res.success:
            self.revision = self.scene.revision
            self.candidates = [0.45,0.3,0.6]  # Simulation-only candidate library.
        return res

    def tick(self):
        if self.pending:
            kind,future = self.pending
            if not future.done():
                return
            self.pending = None
            if not self.ready() or self.scene.revision != self.revision:
                self.candidates.clear()
                return
            response = future.result()
            if kind == 'valid' and response.valid:
                req = GetPositionFK.Request()
                req.header.frame_id = 'base_link'
                req.fk_link_names = ['openarm_left_hand']
                req.robot_state.joint_state = self.target
                self.pending = ('fk',self.fk.call_async(req))
            elif kind == 'fk' and response.error_code.val == 1 and response.pose_stamped:
                self.target.header.stamp = self.get_clock().now().to_msg()
                self.selected.publish(self.target)
                self.pose.publish(response.pose_stamped[0])
                self.status.publish(String(data=json.dumps({'selected':True,'scene_revision':self.revision,
                                                            'source':'simulation_candidate_library','unknown_is_free':False})))
                self.candidates.clear()
            return
        if self.candidates and self.ready():
            value = self.candidates.pop(0)
            self.target = JointState()
            self.target.name = list(self.joints.name)
            self.target.position = list(self.joints.position)
            self.target.position[self.target.name.index('openarm_left_joint4')] = value
            req = GetStateValidity.Request()
            req.group_name = 'left_arm'
            req.robot_state.joint_state = self.target
            self.pending = ('valid',self.validity.call_async(req))


def main():
    run(ViewPlanner)

