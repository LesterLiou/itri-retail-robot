"""Pure policy, used by the local gate and its failure-path tests."""
from dataclasses import dataclass
import math


@dataclass
class Observation:
    stamp: float
    action: str
    valid: bool
    synthetic: bool
    ttl: float
    source: str = ''


class GatePolicy:
    def __init__(self, cameras=('chest',), allow_synthetic=False):
        self.cameras = tuple(cameras)
        self.allow_synthetic = allow_synthetic
        self.observations = {}
        self.enabled = False
        self.inhibited = False
        self.hold = False
        self.reason = 'DISABLED: explicit enable required'

    def observe(self, camera, observation):
        old = self.observations.get(camera)
        if old and (observation.stamp < old.stamp or observation.source != old.source):
            self.stop('observation source restarted or time moved backwards')
        self.observations[camera] = observation

    def health(self, at):
        warning = False
        for camera in self.cameras:
            obs = self.observations.get(camera)
            if obs is None:
                return False, f'{camera}: no observation', False
            age = at - obs.stamp
            if not math.isfinite(age) or not (0 <= age <= min(obs.ttl, 2.0)):
                return False, f'{camera}: expired/future observation', False
            if not obs.valid or (obs.synthetic and not self.allow_synthetic):
                return False, f'{camera}: invalid source', False
            if obs.action not in ('CONTINUE', 'NO_DETECT', 'DANGER_WARNING'):
                return False, f'{camera}: {obs.action}', False
            warning |= obs.action == 'DANGER_WARNING'
        return True, 'valid observations', warning

    def stop(self, reason):
        self.hold = True
        self.enabled = False
        self.reason = reason

    def reset(self, at, stopped, arms_stowed, feedback_fresh, conflict):
        healthy, reason, _ = self.health(at)
        if not (healthy and stopped and arms_stowed and feedback_fresh and not conflict):
            return False, 'reset refused: ' + reason + '/feedback/interlock'
        self.hold = False
        self.enabled = False  # Reset never resumes a queued goal or command.
        self.reason = 'reset complete; explicit new enable/goal required'
        return True, self.reason

    def permit(self, at, arms_stowed, feedback_fresh, conflict):
        healthy, reason, warning = self.health(at)
        if conflict:
            self.stop('multiple control writers')
        elif self.enabled and not (healthy and arms_stowed and feedback_fresh):
            self.stop(reason if not healthy else 'arm/odom state invalid or not stowed')
        allowed = self.enabled and not self.hold and not self.inhibited and healthy and arms_stowed and feedback_fresh and not conflict
        return allowed, warning

