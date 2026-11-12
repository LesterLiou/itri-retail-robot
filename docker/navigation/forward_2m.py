"""Submit one guarded straight goal and wait for measured standstill."""
import signal
import sys
import time

import rclpy
from rclpy.context import Context
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.signals import SignalHandlerOptions
from std_srvs.srv import SetBool, Trigger

from retail_core.common import now, seconds
from retail_interfaces.msg import DriveState


def main():
    local, remote = Context(), Context()
    rclpy.init(args=[], context=local, domain_id=0, signal_handler_options=SignalHandlerOptions.NO)
    rclpy.init(args=[], context=remote, domain_id=84, signal_handler_options=SignalHandlerOptions.NO)
    base = Node('forward_2m_local', context=local)
    thor = Node('forward_2m_mission', context=remote)
    base_executor = SingleThreadedExecutor(context=local)
    thor_executor = SingleThreadedExecutor(context=remote)
    base_executor.add_node(base)
    thor_executor.add_node(thor)
    arm = base.create_client(SetBool, '/drive/arm_auto')
    start = thor.create_client(Trigger, '/mission/straight_2m')
    state = None
    arm_attempted = False
    interrupted = False

    def receive(message):
        nonlocal state
        state = message

    def interrupt(_signal, _frame):
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)
    base.create_subscription(DriveState, '/drive/state', receive, 1)

    def wait(condition, timeout, failure, *, cleanup=False):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if interrupted and not cleanup:
                raise RuntimeError('Interrupted by operator')
            base_executor.spin_once(timeout_sec=0.02)
            thor_executor.spin_once(timeout_sec=0.02)
            if condition():
                return
        raise RuntimeError(failure)

    def fresh_state():
        return state is not None and 0 <= now(base) - seconds(state.header.stamp) < 0.35

    def stopped():
        return fresh_state() and state.standstill and not state.enabled

    try:
        wait(lambda: arm.service_is_ready() and start.service_is_ready() and fresh_state(),
             10, 'TidyBot gate, THOR mission service or fresh drive state unavailable')
        if not state.hardware or state.phase not in ('IDLE', 'DONE') or not stopped():
            raise RuntimeError(f'TidyBot is not in hardware standby: {state.phase}: {state.reason}')
        boot_id = state.boot_id
        arm_attempted = True
        armed = arm.call_async(SetBool.Request(data=True))
        wait(armed.done, 3, 'Local arming request timed out')
        if not armed.result().success:
            arm_attempted = False
            raise RuntimeError(armed.result().message)
        if interrupted:
            raise RuntimeError('Interrupted before goal submission')
        request = start.call_async(Trigger.Request())
        wait(request.done, 3, 'THOR straight goal request timed out')
        result = request.result()
        if not result.success:
            raise RuntimeError(result.message)
        task_id = result.message
        print(f'Accepted by THOR: {task_id}', flush=True)
        next_print = 0.0
        last_state_at = time.monotonic()

        def finished():
            nonlocal next_print, last_state_at
            at = time.monotonic()
            if not fresh_state():
                if at - last_state_at >= 1.0:
                    raise RuntimeError('Lost fresh drive state')
                return False
            last_state_at = at
            if state.boot_id != boot_id:
                raise RuntimeError('Drive gate restarted')
            if state.task_id != task_id:
                return False
            if at >= next_print:
                print(f'{state.phase} {state.distance:.2f} m '
                      f'{state.output.linear.x:.3f} m/s | {state.reason}', flush=True)
                next_print = at + 1.0
            if state.phase in ('HOLD', 'REJECTED', 'HANDOFF'):
                raise RuntimeError(f'{state.phase}: {state.reason}')
            return state.phase == 'DONE' and stopped()

        wait(finished, 96, 'Goal did not finish within the drive session')
        print(f'Standstill verified at {state.distance:.2f} m', flush=True)
        return 0
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1
    finally:
        if arm_attempted:
            try:
                disarm = arm.call_async(SetBool.Request(data=False))
                wait(disarm.done, 3, 'Local disarm could not be confirmed', cleanup=True)
                if not disarm.result().success:
                    raise RuntimeError(disarm.result().message)
                wait(stopped, 4, 'Could not confirm measured standstill', cleanup=True)
            except Exception as exc:
                print(f'Stop verification: {exc}', file=sys.stderr, flush=True)
        for executor, node, context in ((base_executor, base, local), (thor_executor, thor, remote)):
            executor.shutdown()
            node.destroy_node()
            context.try_shutdown()


if __name__ == '__main__':
    sys.exit(main())
