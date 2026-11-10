"""Known shelf baseline or explicitly validated external metric scene."""
import numpy as np
from geometry_msgs.msg import Pose
from moveit_msgs.msg import PlanningScene, CollisionObject
from moveit_msgs.srv import ApplyPlanningScene
from shape_msgs.msg import SolidPrimitive
from sensor_msgs.msg import PointCloud2, JointState
from sensor_msgs_py.point_cloud2 import create_cloud_xyz32
from std_msgs.msg import Header
from std_srvs.srv import Trigger
from rclpy.node import Node
from rclpy.qos import QoSProfile,DurabilityPolicy
from retail_interfaces.msg import SceneState,GateState
from visualization_msgs.msg import Marker
from .common import run,now,seconds,duration,text_marker


class SceneManager(Node):
    def __init__(self):
        super().__init__('scene_manager')
        self.backend = self.declare_parameter('backend','external').value
        if self.backend not in ('known_fixture','external'):
            raise ValueError('Select known_fixture or external')
        self.revision,self.captured,self.complete = 0,0.0,False
        self.coverage = 0.0
        self.gate,self.joints,self.external = None,None,None
        self.applied_revision,self.apply_pending = -1,None
        self.apply_scene = self.create_client(ApplyPlanningScene,'/apply_planning_scene')
        self.state_pub = self.create_publisher(SceneState,'/perception/shelf/state',10)
        qos = QoSProfile(depth=1,durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.cloud_pub = self.create_publisher(PointCloud2,'/perception/shelf/cloud',qos)
        self.markers = self.create_publisher(Marker,'/retail/status_markers',10)
        self.create_subscription(GateState,'/base/gate_state',lambda m:setattr(self,'gate',m),10)
        self.create_subscription(JointState,'/joint_states',lambda m:setattr(self,'joints',m),10)
        self.create_subscription(SceneState,'/reconstruction/state',lambda m:setattr(self,'external',m),10)
        self.create_service(Trigger,'/scene/scan',self.scan)
        self.create_service(Trigger,'/scene/commit_wrist_view',self.commit)
        self.create_timer(0.2,self.tick)
        self.create_timer(2.0,self.publish_known_world)

    def locked(self):
        return bool(self.gate and 0<=now(self)-seconds(self.gate.header.stamp)<0.5 and
                    self.gate.base_inhibited and self.gate.base_standstill and not self.gate.hold)

    def scan(self,req,res):
        if not self.locked():
            res.success,res.message = False,'base inhibition/standstill required'
            return res
        if self.backend == 'external':
            good = bool(self.external and self.external.valid and self.external.metric_valid and
                        0<=now(self)-seconds(self.external.header.stamp)<=seconds(self.external.valid_for))
            if not good:
                res.success,res.message = False,'no current validated metric reconstruction'
                return res
        self.revision += 1
        self.captured = now(self)
        self.complete = False
        self.publish_known_world()
        res.success,res.message = True,f'scene revision {self.revision}; source={self.backend}'
        return res

    def commit(self,req,res):
        if self.backend != 'known_fixture':
            res.success,res.message = False,'external fusion must publish a new validated reconstruction revision'
            return res
        values = dict(zip(self.joints.name,self.joints.position)) if self.joints else {}
        fresh = self.joints and 0<=now(self)-seconds(self.joints.header.stamp)<0.5
        # Only simulate a wrist observation after actual fake joint feedback matches its target.
        at_view = abs(values.get('openarm_left_joint4',99.0)-0.45)<0.04
        res.success = bool(fresh and self.locked() and at_view and self.revision)
        if res.success:
            self.complete = True
            self.revision += 1
            self.captured = now(self)
            self.publish_known_world()
        res.message = f'known-fixture wrist observation revision {self.revision}' if res.success else 'wrist not at verified fake observation pose'
        return res

    def publish_known_world(self):
        if self.backend != 'known_fixture':
            return
        scene = PlanningScene()
        scene.is_diff = True
        boxes = [('shelf_back',[0.08,1.3,1.6],[3.05,0.0,0.9])]
        boxes += [(f'shelf_level_{i}',[0.45,1.3,0.04],[2.87,0.0,z]) for i,z in enumerate((0.45,1.0,1.55))]
        for name,size,xyz in boxes:
            obj = CollisionObject()
            obj.id,obj.header.frame_id,obj.operation = name,'map',CollisionObject.ADD
            obj.header.stamp = self.get_clock().now().to_msg()
            box = SolidPrimitive(type=SolidPrimitive.BOX,dimensions=[float(x) for x in size])
            pose = Pose()
            pose.position.x,pose.position.y,pose.position.z = xyz
            pose.orientation.w = 1.0
            obj.primitives,obj.primitive_poses = [box],[pose]
            scene.world.collision_objects.append(obj)
        if self.apply_scene.service_is_ready() and self.apply_pending is None:
            self.apply_pending = (self.revision,self.apply_scene.call_async(ApplyPlanningScene.Request(scene=scene)))
        pts = [[3.0,float(y),float(z)] for y in np.linspace(-0.6,0.6,31) for z in np.linspace(0.45,1.5,25)
               if self.complete or y>=-0.25]
        for z in (0.45,1.0,1.55):
            pts.extend([[float(x),float(y),z] for x in np.linspace(2.65,3.0,12) for y in np.linspace(-0.6,0.6,31)
                        if self.complete or y>=-0.25])
        expected = [[3.0,float(y),float(z)] for y in np.linspace(-0.6,0.6,31) for z in np.linspace(0.45,1.5,25)]
        for z in (0.45,1.0,1.55):
            expected.extend([[float(x),float(y),z] for x in np.linspace(2.65,3.0,12) for y in np.linspace(-0.6,0.6,31)])
        observed_cells = {tuple(x) for x in np.floor(np.asarray(pts)/0.04).astype(int)}
        expected_cells = {tuple(x) for x in np.floor(np.asarray(expected)/0.04).astype(int)}
        self.coverage = len(observed_cells & expected_cells)/len(expected_cells)
        header = Header(frame_id='map',stamp=self.get_clock().now().to_msg())
        self.cloud_pub.publish(create_cloud_xyz32(header,pts))

    def tick(self):
        if self.apply_pending and self.apply_pending[1].done():
            revision,future = self.apply_pending
            if future.result().success:
                self.applied_revision = revision
            self.apply_pending = None
        state = SceneState()
        state.header.frame_id = 'map'
        if self.revision:
            state.header.stamp.sec = int(self.captured)
            state.header.stamp.nanosec = int((self.captured%1)*1e9)
        state.valid_for = duration(20.0)
        state.revision,state.source_id = self.revision,self.backend
        state.synthetic = self.backend == 'known_fixture'
        state.metric_valid = self.backend == 'known_fixture' or bool(self.external and self.external.metric_valid)
        state.valid = bool(self.revision and self.applied_revision==self.revision and self.locked() and now(self)-self.captured<=20.0)
        if self.backend == 'external':
            state.valid &= bool(self.external and self.external.valid and 0<=now(self)-seconds(self.external.header.stamp)<seconds(self.external.valid_for))
        state.coverage = self.coverage if state.synthetic else (self.external.coverage if self.external else 0.0)
        state.reason = 'known geometry fixture; no learned reconstruction' if state.synthetic else 'external reconstruction and calibration required'
        self.state_pub.publish(state)
        self.markers.publish(text_marker(self,f'SCENE r{self.revision}: {self.backend} / coverage {state.coverage:.0%}',40,2.75))


def main():
    run(SceneManager)

