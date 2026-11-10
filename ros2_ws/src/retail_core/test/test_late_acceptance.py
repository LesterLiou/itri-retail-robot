from types import SimpleNamespace
from rclpy.task import Future
from retail_core.mission import MissionManager


def test_acceptance_after_cancel_and_reset_cancels_the_old_goal():
    owner=SimpleNamespace(generation=7)
    cancelled=[]
    future=Future()
    MissionManager.guarded_acceptance(owner,future)
    owner.generation=8  # Cancellation persists across a later reset/new mission.
    future.set_result(SimpleNamespace(accepted=True,cancel_goal_async=lambda:cancelled.append(True)))
    assert cancelled==[True]


def test_current_or_rejected_goal_is_not_cancelled_as_a_late_goal():
    for accepted in (True,False):
        owner=SimpleNamespace(generation=7)
        cancelled=[]
        future=Future()
        MissionManager.guarded_acceptance(owner,future)
        future.set_result(SimpleNamespace(accepted=accepted,cancel_goal_async=lambda:cancelled.append(True)))
        assert not cancelled
