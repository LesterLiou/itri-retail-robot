import math
import pytest
from geometry_msgs.msg import PoseStamped
from retail_core.navigation import validate_goal


def goal():
    msg=PoseStamped();msg.header.frame_id='map';msg.header.stamp.sec=10
    msg.pose.position.x=1.0;msg.pose.orientation.w=1.0
    return msg


def test_valid_fresh_planar_map_goal():
    validate_goal(goal(),10.5)


@pytest.mark.parametrize('case',['old','future','frame','nan','height','quaternion','tilt'])
def test_invalid_navigation_goal_is_rejected(case):
    msg=goal()
    if case=='old':msg.header.stamp.sec=7
    elif case=='future':msg.header.stamp.sec=11
    elif case=='frame':msg.header.frame_id='odom'
    elif case=='nan':msg.pose.position.x=math.nan
    elif case=='height':msg.pose.position.z=0.1
    elif case=='quaternion':msg.pose.orientation.w=0.0
    elif case=='tilt':msg.pose.orientation.x=0.1
    with pytest.raises(ValueError):validate_goal(msg,10.5)
