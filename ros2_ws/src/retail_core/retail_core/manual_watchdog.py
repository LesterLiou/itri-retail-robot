"""Independent, fail-closed lease for the fake manual MoveIt control owner."""
import re
import time
from rclpy.node import Node
from std_msgs.msg import String
from std_srvs.srv import SetBool,Trigger
from action_msgs.msg import GoalStatusArray
from action_msgs.srv import CancelGoal
from retail_interfaces.srv import ManualLease
from .common import run


class ManualWatchdog(Node):
    def __init__(self):
        super().__init__('manual_watchdog')
        self.owner=None
        self.received=0.0
        self.held=False
        self.arm_state=''
        self.arm_received=0.0
        self.planning_active=False
        self.closing=False
        self.cancel_future=None
        self.close_started=0.0
        self.create_service(ManualLease,'/manual/lease',self.lease)
        self.create_subscription(String,'/manual/heartbeat',self.heartbeat,1)
        self.create_subscription(String,'/arm/gate_state',self.arm,10)
        self.create_subscription(GoalStatusArray,'/move_action/_action/status',
            lambda m:setattr(self,'planning_active',any(s.status in (1,2,3) for s in m.status_list)),10)
        self.authorize=self.create_client(SetBool,'/arm_gate/authorize')
        self.stop_base=self.create_client(Trigger,'/base_gate/stop')
        self.cancel_move=self.create_client(CancelGoal,'/move_action/_action/cancel_goal')
        self.pending_arm=self.pending_base=False
        self.create_timer(0.05,self.tick)

    def arm(self,msg):
        self.arm_state,self.arm_received=msg.data,time.monotonic()

    def stopped(self):
        return time.monotonic()-self.arm_received<0.5 and 'stopped=True' in self.arm_state and 'active=False' in self.arm_state

    def lease(self,req,res):
        if not re.fullmatch('[a-f0-9]{32}',req.owner_id):
            res.message='Invalid owner ID';return res
        if req.acquire:
            if self.held or self.owner is not None or self.closing or self.pending_arm or self.pending_base or not self.stopped():
                res.message='Lease refused: existing owner, HOLD, or missing standstill';return res
            self.owner,self.received=req.owner_id,time.monotonic()
            res.success,res.message=True,'exclusive fake MoveIt owner'
        elif self.owner!=req.owner_id:
            res.message='Owner mismatch'
        else:
            self.owner=None
            self.pending_arm=True
            self.closing=True
            self.close_started=time.monotonic()
            self.cancel_future=None
            res.success,res.message=True,'release requested; new goals blocked until cancellation and measured stop'
        return res

    def heartbeat(self,msg):
        if msg.data==self.owner:self.received=time.monotonic()

    def tick(self):
        if self.closing:
            if self.cancel_future is None and self.cancel_move.service_is_ready():
                # Zero goal ID/time requests cancellation of all old manual goals.
                self.cancel_future=self.cancel_move.call_async(CancelGoal.Request())
            if self.cancel_future and self.cancel_future.done() and self.cancel_future.exception() is None and self.stopped() and not self.planning_active:
                self.closing=False
            elif time.monotonic()-self.close_started>1.0:
                self.held=True
                self.pending_arm=self.pending_base=True
        if self.owner is not None and time.monotonic()-self.received>0.5:
            self.owner=None
            self.held=True
            self.pending_arm=self.pending_base=True
            self.closing=True
            self.close_started=time.monotonic()
            self.cancel_future=None
            self.get_logger().error('Manual owner lost: HOLD; no old goal will be resumed')
        if self.pending_arm and self.authorize.service_is_ready():
            self.authorize.call_async(SetBool.Request(data=False));self.pending_arm=False
        if self.pending_base and self.stop_base.service_is_ready():
            self.stop_base.call_async(Trigger.Request());self.pending_base=False


def main():run(ManualWatchdog)
