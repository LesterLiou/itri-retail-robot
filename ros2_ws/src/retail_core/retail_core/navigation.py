"""Validate fresh planar map goals before the task manager takes ownership."""
import math
from .common import seconds


def validate_goal(goal,at):
    p,q=goal.pose.position,goal.pose.orientation
    if goal.header.frame_id!='map':raise ValueError('Goal frame must be map')
    values=(p.x,p.y,p.z,q.x,q.y,q.z,q.w)
    if not all(math.isfinite(v) for v in values):raise ValueError('Goal contains non-finite values')
    if not 0<=at-seconds(goal.header.stamp)<=2.0:raise ValueError('Goal timestamp expired or in the future')
    if abs(p.z)>0.001 or abs(q.x)>1e-6 or abs(q.y)>1e-6:raise ValueError('Planar goal required')
    if abs(q.z*q.z+q.w*q.w-1.0)>0.001:raise ValueError('Invalid goal quaternion')

