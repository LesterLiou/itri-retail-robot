"""Causal, fixed-rate person-box sequences. No Torch import in heuristic mode."""
from collections import deque
import math

import numpy as np

LABELS = ('TOWARD_CAMERA', 'AWAY_FROM_CAMERA', 'LATERAL_ACROSS_VIEW', 'STATIONARY', 'UNCERTAIN')
FEATURE_NAMES = ('cx', 'cy', 'width', 'height', 'vx', 'vy', 'dlog_width', 'dlog_height', 'score', 'dt')
FEATURE_VERSION = 'normalized_bbox_v1'


def normalized_box(box, width, height):
    x1, y1, x2, y2 = map(float, box)
    values = ((x1 + x2) / (2 * width), (y1 + y2) / (2 * height),
              (x2 - x1) / width, (y2 - y1) / height)
    if not all(math.isfinite(v) for v in values) or min(values[2:]) <= 0:
        raise ValueError('Invalid bbox geometry')
    return np.asarray(values, dtype=np.float32)


def box_motion(first, last, scale_threshold=0.12, lateral_threshold=0.35):
    """Apparent image-space motion; this is not calibrated depth or action recognition."""
    scale = float(last[3] / first[3] - 1.0)
    lateral = float((last[0] - first[0]) / max(1e-6, (first[2] + last[2]) / 2))
    if scale >= scale_threshold:
        return 'TOWARD_CAMERA'
    if scale <= -scale_threshold:
        return 'AWAY_FROM_CAMERA'
    if abs(lateral) >= lateral_threshold:
        return 'LATERAL_ACROSS_VIEW'
    if abs(scale) <= 0.04 and abs(lateral) <= 0.12:
        return 'STATIONARY'
    return 'UNCERTAIN'


def causal_sequence(observations, end_s, steps=16, sample_hz=5.0, max_gap_s=0.6):
    """Sample only observations at/before each grid time; never interpolate from future."""
    if steps < 2 or sample_hz <= 0 or max_gap_s <= 0:
        raise ValueError('Invalid temporal sampling configuration')
    observations = sorted(observations, key=lambda o: o[0])
    grid = end_s - np.arange(steps - 1, -1, -1) / sample_hz
    selected = []
    index = 0
    for stamp in grid:
        while index + 1 < len(observations) and observations[index + 1][0] <= stamp + 1e-7:
            index += 1
        if not observations or observations[index][0] > stamp + 1e-7:
            return None
        obs = observations[index]
        if stamp - obs[0] > max_gap_s:
            return None
        selected.append(obs)
    features = []
    for i, (_, box, score) in enumerate(selected):
        velocity = np.zeros(4, dtype=np.float32)
        if i:
            previous = selected[i - 1][1]
            velocity[:2] = (box[:2] - previous[:2]) * sample_hz
            velocity[2:] = np.log(box[2:] / previous[2:]) * sample_hz
        features.append([*box, *np.clip(velocity, -10, 10), score, 1.0 / sample_hz])
    result = np.asarray(features, dtype=np.float32)
    return result if np.isfinite(result).all() else None


class TemporalEstimator:
    def __init__(self, mode='heuristic', checkpoint='', steps=16, sample_hz=5.0,
                 max_gap_s=0.6, confidence_threshold=0.6):
        if mode not in {'heuristic', 'lstm', 'disabled'}:
            raise ValueError('temporal_mode must be heuristic, lstm or disabled')
        if steps < 2 or sample_hz <= 0 or max_gap_s <= 0:
            raise ValueError('Invalid temporal sampling configuration')
        if not 0 <= confidence_threshold <= 1:
            raise ValueError('Invalid confidence threshold')
        self.mode, self.steps, self.sample_hz = mode, steps, sample_hz
        self.max_gap_s, self.threshold = max_gap_s, confidence_threshold
        self.history = {}
        self.predictor = None
        if mode == 'lstm':
            from .lstm_model import TrainedPredictor
            self.predictor = TrainedPredictor(checkpoint, steps, sample_hz)

    def reset(self):
        self.history.clear()

    def update(self, track_id, stamp, box, score, width, height):
        history = self.history.setdefault(track_id, deque(maxlen=512))
        normalized = normalized_box(box, width, height)
        if history and (stamp <= history[-1][0] or stamp - history[-1][0] > self.max_gap_s):
            history.clear()
        history.append((float(stamp), normalized, float(score)))
        sequence = causal_sequence(history, stamp, self.steps, self.sample_hz, self.max_gap_s)
        result = {'source': self.mode, 'stamp_s': float(stamp), 'future_motion': 'UNCERTAIN',
                  'reliable': False, 'confidence': None, 'sequence_ready': sequence is not None,
                  'prediction_horizon_s': None, 'metric_depth': False}
        if self.mode == 'disabled' or sequence is None:
            return result
        if self.mode == 'heuristic':
            result.update(future_motion=box_motion(sequence[0, :4], sequence[-1, :4]),
                          reliable=True, assumption='observed_motion_persistence')
        else:
            label, confidence = self.predictor.predict(sequence)
            reliable = confidence >= self.threshold
            result.update(future_motion=label if reliable else 'UNCERTAIN',
                          confidence=confidence, reliable=reliable,
                          prediction_horizon_s=self.predictor.horizon_s)
        return result

    def prune(self, stamp, retention_s=5.0):
        self.history = {key: values for key, values in self.history.items()
                        if values and stamp - values[-1][0] <= retention_s}
