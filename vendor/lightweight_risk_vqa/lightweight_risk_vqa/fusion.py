"""Attach time-aligned temporal evidence; optionally replace only NEXT motion."""
from collections import deque
from copy import deepcopy
import math

from .temporal import LABELS


class PredictionHistory:
    def __init__(self, retention_s=180.0, tolerance_s=0.6):
        self.records = {side: deque(maxlen=4096) for side in ('left', 'right')}
        self.retention_s, self.tolerance_s = retention_s, tolerance_s

    def add(self, report):
        for side, camera in report.get('cameras', {}).items():
            if side not in self.records:
                continue
            stamp = float(camera['stamp_s'])
            records = self.records[side]
            if records and stamp < records[-1][0]:
                records.clear()
            predictions = {t['track_id']: deepcopy(t['temporal_prediction'])
                           for t in camera['tracks'] if t.get('visible', False)}
            records.append((stamp, predictions))
            while records and stamp - records[0][0] > self.retention_s:
                records.popleft()

    def find(self, side, track_id, stamp):
        if side not in self.records or not isinstance(stamp, (float, int)) or not math.isfinite(stamp):
            return None
        for observed, predictions in reversed(self.records[side]):
            if observed <= stamp + 1e-6:
                if stamp - observed > self.tolerance_s:
                    return None
                return deepcopy(predictions.get(track_id))
        return None


def fuse_report(report, history, mode='diagnostic'):
    if mode not in {'diagnostic', 'lstm_future'}:
        raise ValueError('fusion_mode must be diagnostic or lstm_future')
    result = deepcopy(report)
    metadata = result.setdefault('metadata', {})
    stamps = metadata.get('snapshot_stamps_s', {})
    eligible = result.get('availability') == 'READY' and not result.get('validation_errors')
    applied = 0
    for item in result.get('assessments', []):
        track_id = str(item.get('track_id', ''))
        side = item.get('camera') or ('left' if track_id.startswith('L') else 'right')
        prediction = history.find(side, track_id, stamps.get(side))
        item['temporal_prediction'] = prediction or {'source': 'unavailable', 'reliable': False}
        if mode != 'lstm_future' or not eligible:
            continue
        future = item.setdefault('future_prediction', {})
        future['pre_temporal_future_motion'] = future.get('future_motion', 'UNCERTAIN')
        future.setdefault('vlm_future_motion', future.get('future_motion', 'UNCERTAIN'))
        valid = (prediction and prediction.get('source') == 'lstm' and prediction.get('reliable')
                 and prediction.get('future_motion') in LABELS)
        future['future_motion'] = prediction['future_motion'] if valid else 'UNCERTAIN'
        future['motion_source'] = 'lstm' if valid else 'lstm_unavailable'
        applied += bool(valid)
    metadata['temporal_fusion'] = {'mode': mode, 'applied_count': applied,
        'alignment': 'latest_at_or_before_each_camera_snapshot',
        'llm_prompt_changed': metadata.get('configured_storyboard_mode') == 'current_crop'}
    return result
