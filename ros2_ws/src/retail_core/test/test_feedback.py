from sensor_msgs.msg import JointState
from retail_core.common import finite_joints


def test_complete_finite_feedback():
    assert finite_joints(JointState(name=['joint1','joint2'],position=[0.0,1.0]),['joint1','joint2'])


def test_missing_duplicate_or_misaligned_joint_feedback_is_invalid():
    for message in (JointState(name=['joint1'],position=[]),
                    JointState(name=['joint1','joint1'],position=[0.0,0.0]),
                    JointState(name=['joint2'],position=[0.0])):
        assert not finite_joints(message,['joint1'])


def test_nonfinite_joint_feedback_is_invalid():
    for value in (float('nan'),float('inf'),float('-inf')):
        assert not finite_joints(JointState(name=['joint1'],position=[value]),['joint1'])
