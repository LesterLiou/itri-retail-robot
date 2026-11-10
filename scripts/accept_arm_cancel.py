#!/usr/bin/env python3
"""Cancel a running MoveIt operation and verify actual fake joint standstill."""
import time
import rclpy
from sensor_msgs.msg import JointState
from rclpy.qos import qos_profile_sensor_data
from std_srvs.srv import SetBool
from accept_sim import Acceptance


def main():
    rclpy.init()
    node=Acceptance()
    joint=[None]
    node.create_subscription(JointState,'/joint_states',lambda m:joint.__setitem__(0,m),qos_profile_sensor_data)
    try:
        node.until(lambda:node.gate and node.gate.base_standstill and node.gate.arms_stowed and joint[0])
        node.call('/mission/place')
        def left_moving():
            if not joint[0]: return False
            positions=dict(zip(joint[0].name,joint[0].position))
            return node.state.get('phase')=='SUPPLEMENT_VIEW' and abs(positions.get('openarm_left_joint4',0))>0.12
        node.until(left_moving,timeout=30)
        node.call('/mission/cancel')
        node.until(lambda:node.state.get('phase')=='HOLD' and node.gate.hold and node.stopped())
        # Allow the existing stop chain to settle; then independently compare samples.
        deadline=time.monotonic()+1.0
        while time.monotonic()<deadline: rclpy.spin_once(node,timeout_sec=.02)
        positions=dict(zip(joint[0].name,joint[0].position))
        assert abs(positions['openarm_left_joint4'])>0.08,'test must cancel away from stow'
        deadline=time.monotonic()+1.0
        while time.monotonic()<deadline:
            rclpy.spin_once(node,timeout_sec=.02)
            actual=dict(zip(joint[0].name,joint[0].position))
            assert max(abs(actual[n]-v) for n,v in positions.items())<0.01,'controller did not hold measured joints'
            assert abs(actual['openarm_right_joint4'])<0.08,'queued placement was released'
        node.call('/mission/reset',success=False)
        print('PASS arm cancel: measured joint hold, no queued placement, non-stowed reset rejected',flush=True)
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__=='__main__':
    main()

