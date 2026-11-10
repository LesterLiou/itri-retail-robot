"""One explicit task owner; all arm execution goes through MoveIt."""
from collections import deque
import json
import math
import time
import uuid
from functools import partial

from rclpy.node import Node
from rclpy.action import ActionClient
from rclpy.qos import qos_profile_sensor_data
from std_srvs.srv import Trigger,SetBool
from std_msgs.msg import String
from geometry_msgs.msg import Pose
from nav2_msgs.action import NavigateToPose
from sensor_msgs.msg import JointState
from moveit_msgs.action import MoveGroup
from moveit_msgs.msg import Constraints,JointConstraint,PlanningScene,AttachedCollisionObject,CollisionObject
from moveit_msgs.srv import GetPositionFK,ApplyPlanningScene
from shape_msgs.msg import SolidPrimitive
from retail_interfaces.msg import GateState,SceneState,ObstacleState
from visualization_msgs.msg import Marker
from lifecycle_msgs.srv import GetState
from .common import run,now,seconds,text_marker


class MissionManager(Node):
    def __init__(self):
        super().__init__('mission_manager')
        self.runtime = self.declare_parameter('runtime','simulation').value
        if self.runtime != 'simulation':
            raise RuntimeError('This task profile is validated only for isolated fake hardware')
        self.phase,self.reason,self.mission_id = 'IDLE','', ''
        self.generation = 0
        self.queue,self.pending,self.handles = deque(),None,[]
        self.gate,self.scene,self.hand,self.joints,self.view = None,None,None,None,None
        self.bound_revision = 0
        self.minimum_revision = 0
        self.wait_started = 0.0
        self.arm_active = False
        self.nav_ready,self.nav_query,self.nav_queried_at = False,None,0.0
        self.nav_lifecycle = self.create_client(GetState,'/bt_navigator/get_state')
        self.create_subscription(GateState,'/base/gate_state',lambda m:setattr(self,'gate',m),10)
        self.create_subscription(SceneState,'/perception/shelf/state',lambda m:setattr(self,'scene',m),10)
        self.create_subscription(ObstacleState,'/perception/hand/state',lambda m:setattr(self,'hand',m),10)
        self.create_subscription(JointState,'/joint_states',lambda m:setattr(self,'joints',m),qos_profile_sensor_data)
        self.create_subscription(JointState,'/views/selected_joints',lambda m:setattr(self,'view',m),10)
        self.nav = ActionClient(self,NavigateToPose,'/navigate_to_pose')
        self.move = ActionClient(self,MoveGroup,'/move_action')
        self.fk = self.create_client(GetPositionFK,'/compute_fk')
        self.apply_scene = self.create_client(ApplyPlanningScene,'/apply_planning_scene')
        self.service_clients = {}
        for service,type_ in (('/base_gate/select_navigation',Trigger),('/base_gate/enable',SetBool),
                              ('/base_gate/inhibit',SetBool),('/base_gate/reset_hold',Trigger),
                              ('/base_gate/stop',Trigger),('/scene/scan',Trigger),
                              ('/arm_gate/authorize',SetBool),('/arm_gate/reset_hold',Trigger),
                              ('/scene/commit_wrist_view',Trigger),('/views/select',Trigger)):
            self.service_clients[service] = self.create_client(type_,service)
        self.state = self.create_publisher(String,'/mission/state',10)
        self.markers = self.create_publisher(Marker,'/retail/status_markers',10)
        for command in ('navigate','run','place'):
            self.create_service(Trigger,f'/mission/{command}',partial(self.start,command))
        self.create_service(Trigger,'/mission/cancel',self.cancel)
        self.create_service(Trigger,'/mission/reset',self.reset)
        self.create_timer(0.05,self.tick)
        self.create_timer(0.2,self.publish_state)

    def publish_state(self):
        self.state.publish(String(data=json.dumps(dict(mission_id=self.mission_id,phase=self.phase,
                               reason=self.reason,scene_revision=self.bound_revision,runtime=self.runtime))))
        self.markers.publish(text_marker(self,f'MISSION: {self.phase}\n{self.reason[:75]}',50,3.05))

    def start(self,command,req,res):
        if self.phase not in ('IDLE','DONE'):
            res.success,res.message = False,'task active/held; cancel or explicitly reset'
            return res
        self.mission_id = uuid.uuid4().hex
        self.generation += 1
        self.reason,self.handles,self.view = '',[],None
        self.queue.clear()
        if command != 'place':
            self.queue.extend([
                ('wait','WAIT_NAV_READY','nav_ready'),
                ('service','SELECT_NAV','/base_gate/select_navigation',None),
                ('service','ENABLE_NAV','/base_gate/enable',True),
                ('nav','NAVIGATE',(1.6,0.0)),
            ])
            if command == 'run':
                # This is a fixed-pose fake docking baseline, not ArUco docking.
                self.queue.append(('nav','DOCK_FIXED_POSE',(2.1,0.0)))
        if command in ('run','place'):
            self.queue.extend([
                ('service','INHIBIT_BASE','/base_gate/inhibit',True),
                ('wait','VERIFY_STANDSTILL','standstill'),
                ('service','SCAN_SHELF','/scene/scan',None),
                ('wait','WAIT_SCENE','scene'),
                ('service','SELECT_WRIST_VIEW','/views/select',None),
                ('wait','WAIT_WRIST_VIEW','view'),
                ('service','AUTHORIZE_ARM','/arm_gate/authorize',True),
                ('move','SUPPLEMENT_VIEW','left_arm','view'),
                ('service','DISABLE_ARM_FOR_SCENE_UPDATE','/arm_gate/authorize',False),
                ('service','COMMIT_WRIST_OBSERVATION','/scene/commit_wrist_view',None),
                ('wait','WAIT_NEW_SCENE','scene'),
                ('service','AUTHORIZE_NEW_SCENE','/arm_gate/authorize',True),
                ('move','RETRACT_LEFT','left_arm',[0.0]*7),
                ('attach','ATTACH_KNOWN_SNACK'),
                ('move','PRE_PLACE','right_arm',[0.0,0.0,0.0,0.3,0.0,0.0,0.0]),
                ('move','PLACE','right_arm',[0.0,0.0,0.0,0.55,0.0,0.0,0.0]),
                ('move','RELEASE','right_gripper',[0.035]),
                ('detach','DETACH_SNACK'),
                ('move','RETRACT_RIGHT','right_arm',[0.0]*7),
                ('wait','VERIFY_STOW','stow'),
                ('service','DISABLE_ARM','/arm_gate/authorize',False),
                ('service','RELEASE_BASE_INHIBIT','/base_gate/inhibit',False),
            ])
        else:
            self.queue.append(('service','DISABLE_NAV','/base_gate/enable',False))
        self.phase = 'STARTING'
        self.wait_started = time.monotonic()
        res.success,res.message = True,f'accepted {command}: {self.mission_id}; simulation only'
        return res

    def stop(self,reason):
        self.generation += 1
        self.phase,self.reason,self.arm_active = 'HOLD',reason,False
        self.queue.clear()
        for handle in self.handles:
            if handle.accepted:
                handle.cancel_goal_async()
        self.handles.clear()
        self.pending = None  # Late results are discarded, never resubmitted.
        client = self.service_clients['/base_gate/stop']
        if client.service_is_ready():
            client.call_async(Trigger.Request())
        arm_client = self.service_clients['/arm_gate/authorize']
        if arm_client.service_is_ready():
            arm_client.call_async(SetBool.Request(data=False))

    def guarded_acceptance(self,future):
        generation = self.generation
        def cancel_late(response):
            try:
                handle = response.result()
                if handle.accepted and generation!=self.generation:
                    handle.cancel_goal_async()
            except Exception:
                pass  # Failed acceptance cannot produce an execution goal.
        future.add_done_callback(cancel_late)
        return future

    def cancel(self,req,res):
        self.stop('operator cancel: awaiting measured stop; no automatic resume')
        res.success,res.message = True,self.reason
        return res

    def reset(self,req,res):
        if self.phase not in ('HOLD','FAULT','DONE','IDLE'):
            res.success,res.message = False,'cancel active mission first'
            return res
        if not self.gate or not self.gate.base_standstill or not self.gate.arms_stowed:
            res.success,res.message = False,'verified standstill and stowed feedback required'
            return res
        self.queue = deque([('service','RESET_GATE','/base_gate/reset_hold',None)])
        self.pending = None
        self.phase = 'RESETTING'
        self.wait_started = time.monotonic()
        res.success,res.message = True,'explicit reset requested; new goal required afterwards'
        return res

    def gate_fresh(self):
        return bool(self.gate and 0<=now(self)-seconds(self.gate.header.stamp)<0.5)

    def arm_permitted(self):
        t = now(self)
        return bool(self.gate_fresh() and self.gate.base_inhibited and self.gate.base_standstill and not self.gate.hold and
                    self.hand and self.hand.valid and not self.hand.occupied and
                    0<=t-seconds(self.hand.header.stamp)<=min(seconds(self.hand.valid_for),0.5) and
                    self.scene and self.scene.valid and self.scene.metric_valid and
                    0<=t-seconds(self.scene.header.stamp)<=seconds(self.scene.valid_for) and
                    self.joints and 0<=t-seconds(self.joints.header.stamp)<0.5)

    def send_move(self,group,target):
        if not self.arm_permitted():
            self.stop('arm permission missing: base/hand/scene/joint feedback')
            return
        if group.startswith('right_') and (not math.isfinite(self.scene.coverage) or self.scene.coverage<0.95):
            self.stop('placement ROI surface coverage insufficient; unknown is not clear')
            return
        self.bound_revision = self.scene.revision
        goal = MoveGroup.Goal()
        goal.request.group_name = group
        goal.request.pipeline_id = 'ompl'
        goal.request.planner_id = 'RRTConnectkConfigDefault'
        goal.request.num_planning_attempts = 1
        goal.request.allowed_planning_time = 3.0
        goal.request.max_velocity_scaling_factor = 0.2
        goal.request.max_acceleration_scaling_factor = 0.2
        goal.request.start_state.is_diff = True
        goal.request.start_state.joint_state = self.joints
        if target == 'view':
            values = dict(zip(self.view.name,self.view.position))
            names = [f'openarm_left_joint{i}' for i in range(1,8)]
            target = [values[n] for n in names]
        elif group.endswith('gripper'):
            names = ['openarm_right_finger_joint1']
        else:
            side = group.split('_')[0]
            names = [f'openarm_{side}_joint{i}' for i in range(1,8)]
        constraint = Constraints()
        for name,value in zip(names,target):
            tolerance = 0.001 if group.endswith('gripper') else 0.02
            constraint.joint_constraints.append(JointConstraint(joint_name=name,position=float(value),
                                                               tolerance_above=tolerance,tolerance_below=tolerance,weight=1.0))
        goal.request.goal_constraints = [constraint]
        goal.planning_options.plan_only = False
        self.arm_active = True
        self.pending = ('move_accept',self.guarded_acceptance(self.move.send_goal_async(goal)))

    def attach(self):
        msg = PlanningScene()
        msg.is_diff,msg.robot_state.is_diff = True,True
        attached = AttachedCollisionObject()
        attached.link_name = 'openarm_right_hand'
        attached.touch_links = ['openarm_right_hand','openarm_right_link7',
                                'openarm_right_left_finger','openarm_right_right_finger']
        obj = attached.object
        obj.id,obj.header.frame_id,obj.operation = 'known_snack','openarm_right_hand',CollisionObject.ADD
        obj.primitives = [SolidPrimitive(type=SolidPrimitive.BOX,dimensions=[0.04,0.03,0.06])]
        pose = Pose()
        pose.position.z,pose.orientation.w = 0.08,1.0
        obj.primitive_poses = [pose]
        msg.robot_state.attached_collision_objects = [attached]
        return msg

    def tick(self):
        if self.nav_query and self.nav_query.done():
            try:
                self.nav_ready = self.nav_query.result().current_state.id==3
            except Exception:
                self.nav_ready = False
            self.nav_query = None
        if not self.nav_query and time.monotonic()-self.nav_queried_at>0.5 and self.nav_lifecycle.service_is_ready():
            self.nav_queried_at = time.monotonic()
            self.nav_query = self.nav_lifecycle.call_async(GetState.Request())
        if self.phase in ('IDLE','DONE','HOLD','FAULT'):
            return
        if self.arm_active:
            if not self.arm_permitted() or self.scene.revision != self.bound_revision:
                self.stop('arm execution interrupted: observation/interlock/scene changed')
                return
        elif self.phase in ('NAVIGATE','DOCK_FIXED_POSE'):
            if not self.gate_fresh() or self.gate.hold:
                self.stop('navigation interlock lost; goal cancelled')
                return
        if self.pending:
            kind,future = self.pending
            if not future.done():
                return
            self.pending = None
            try:
                response = future.result()
                if kind.endswith('_accept'):
                    if not response.accepted:
                        self.stop(f'{kind}: goal rejected')
                        return
                    self.handles.append(response)
                    self.pending = (kind.replace('_accept','_result'),response.get_result_async())
                    return
                if kind in ('service','apply') and not response.success:
                    self.stop(response.message if kind=='service' else 'planning-scene update rejected')
                    return
                if kind in ('nav_result','move_result'):
                    if response.status != 4 or (kind=='move_result' and response.result.error_code.val != 1):
                        code = response.result.error_code.val if kind=='move_result' else response.status
                        self.stop(f'{kind} failed: status={response.status}, code={code}')
                        return
                    self.arm_active = False
                if kind == 'detach':
                    if response.error_code.val != 1 or not response.pose_stamped:
                        self.stop('release FK failed')
                        return
                    msg = PlanningScene()
                    msg.is_diff,msg.robot_state.is_diff = True,True
                    removed = AttachedCollisionObject()
                    removed.object.id,removed.object.operation = 'known_snack',CollisionObject.REMOVE
                    msg.robot_state.attached_collision_objects = [removed]
                    obj = CollisionObject()
                    obj.id,obj.header,obj.operation = 'placed_snack',response.pose_stamped[0].header,CollisionObject.ADD
                    pose = response.pose_stamped[0].pose
                    # Preserve the attached object's hand-relative offset on detach.
                    q = pose.orientation
                    pose.position.x += 0.08*2*(q.x*q.z+q.w*q.y)
                    pose.position.y += 0.08*2*(q.y*q.z-q.w*q.x)
                    pose.position.z += 0.08*(1-2*(q.x*q.x+q.y*q.y))
                    obj.primitives = [SolidPrimitive(type=SolidPrimitive.BOX,dimensions=[0.04,0.03,0.06])]
                    obj.primitive_poses = [pose]
                    msg.world.collision_objects = [obj]
                    self.pending = ('apply',self.apply_scene.call_async(ApplyPlanningScene.Request(scene=msg)))
                    return
            except Exception as exc:
                self.stop(str(exc))
                return
            self.wait_started = time.monotonic()
        if not self.queue:
            self.phase,self.reason = 'DONE','simulation sequence completed; base remains disabled'
            return
        step = self.queue[0]
        kind,self.phase = step[0],step[1]
        if time.monotonic()-self.wait_started>12.0 and kind in ('wait','service'):
            self.stop(f'{self.phase}: deadline exceeded')
            return
        if kind == 'wait':
            what = step[2]
            ready = self.nav_ready if what=='nav_ready' else (self.gate_fresh() and self.gate.base_standstill) if what=='standstill' else (
                     self.gate_fresh() and self.gate.arms_stowed) if what=='stow' else (
                     self.scene and self.scene.valid and self.scene.metric_valid and self.scene.revision>=self.minimum_revision) if what=='scene' else (
                     self.view is not None and seconds(self.view.header.stamp)>0)
            if ready:
                self.queue.popleft()
                self.wait_started = time.monotonic()
            return
        if kind == 'service':
            service,value = step[2:]
            client = self.service_clients[service]
            if not client.service_is_ready(): return
            request = Trigger.Request() if value is None else SetBool.Request(data=value)
            if service in ('/scene/scan','/scene/commit_wrist_view'):
                self.minimum_revision = (self.scene.revision if self.scene else 0)+1
            self.pending = ('service',client.call_async(request))
        elif kind == 'nav':
            if not self.nav.server_is_ready(): return
            goal = NavigateToPose.Goal()
            goal.pose.header.frame_id = 'map'
            goal.pose.header.stamp = self.get_clock().now().to_msg()
            goal.pose.pose.position.x,goal.pose.pose.position.y = step[2]
            goal.pose.pose.orientation.w = 1.0
            self.pending = ('nav_accept',self.guarded_acceptance(self.nav.send_goal_async(goal)))
        elif kind == 'move':
            if not self.move.server_is_ready(): return
            self.send_move(step[2],step[3])
            if self.phase == 'HOLD': return
        elif kind == 'attach':
            if not self.arm_permitted(): self.stop('attach requires verified manipulation permit'); return
            if not self.apply_scene.service_is_ready(): self.stop('planning-scene service unavailable'); return
            self.pending = ('apply',self.apply_scene.call_async(ApplyPlanningScene.Request(scene=self.attach())))
        elif kind == 'detach':
            if not self.arm_permitted() or not self.fk.service_is_ready(): self.stop('release requires permit and FK'); return
            request = GetPositionFK.Request()
            request.header.frame_id = 'base_link'
            request.fk_link_names = ['openarm_right_hand']
            request.robot_state.joint_state = self.joints
            self.pending = ('detach',self.fk.call_async(request))
        self.queue.popleft()
        self.wait_started = time.monotonic()


def main():
    run(MissionManager)

