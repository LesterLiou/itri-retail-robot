"""Image-space robot-facing policy; no extra neural model or metric distance claim."""
from collections import deque
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path

from .availability import live_decision


@dataclass(frozen=True)
class CameraPolicyConfig:
    stop_roi: tuple = (.30, .72, .70, 1.0)
    corridor: tuple = (.20, .80)
    stop_min_height: float = .35
    stop_confirmations: int = 2
    min_samples: int = 3
    min_history_s: float = .8
    history_s: float = 4.0
    max_gap_s: float = 2.5
    approach_growth: float = .08
    approach_rate: float = .08
    lateral_rate: float = .06
    min_component_area: float = .002
    min_box_height: float = .10
    match_iou: float = .15
    release_s: float = 1.0

    def __post_init__(self):
        if (len(self.stop_roi) != 4 or
                not 0 <= self.stop_roi[0] < self.stop_roi[2] <= 1 or
                not 0 <= self.stop_roi[1] < self.stop_roi[3] <= 1):
            raise ValueError('stop_roi must be normalized [x1,y1,x2,y2]')
        if len(self.corridor) != 2 or not 0 <= self.corridor[0] < self.corridor[1] <= 1:
            raise ValueError('corridor must be normalized [left,right]')
        for key, value in asdict(self).items():
            if key in {'stop_roi', 'corridor'}:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f'{key} must be finite and positive')
        for key in ('stop_confirmations', 'min_samples'):
            value = getattr(self, key)
            if not isinstance(value, int) or value < 2:
                raise ValueError(f'{key} must be an integer >= 2')
        for key in ('stop_min_height', 'min_component_area', 'min_box_height', 'match_iou'):
            if getattr(self, key) > 1:
                raise ValueError(f'{key} must be <= 1')
        if self.min_history_s > self.history_s:
            raise ValueError('min_history_s must not exceed history_s')

    @classmethod
    def load(cls, path=None):
        return cls(**json.loads(Path(path).read_text())) if path else cls()


def mask_to_boxes(mask, min_component_area=.002):
    """Separate connected mask regions, never union every person into one bbox.

    Components are hypotheses, not instance segmentation or confident person IDs.
    The small working image bounds CPU cost independently of source resolution.
    """
    import cv2
    import numpy as np
    h, w = mask.shape
    scale = min(1.0, 320 / max(h, w))
    small = cv2.resize(mask.astype(np.uint8), (max(1, round(w * scale)), max(1, round(h * scale))),
                       interpolation=cv2.INTER_NEAREST)
    count, _, stats, _ = cv2.connectedComponentsWithStats(small, connectivity=8)
    sh, sw = small.shape
    return [[float(x / sw), float(y / sh), float((x + bw) / sw), float((y + bh) / sh)]
            for x, y, bw, bh, area in stats[1:count]
            if area / (sh * sw) >= min_component_area]


class PersonBoxDetector:
    """Optional local YOLO person boxes; no ByteTrack, downloads or VQA changes."""
    def __init__(self, weights, device='cuda:0', image_size=416):
        weights = Path(weights)
        if not weights.is_file():
            raise ValueError(f'Local YOLO weights missing: {weights}')
        from ultralytics import YOLO
        self.model = YOLO(str(weights), task='detect')
        names = self.model.names
        self.person_classes = [key for key, name in names.items() if name.lower() == 'person']
        if not self.person_classes:
            raise ValueError('YOLO weights must contain a person class')
        self.device, self.image_size = device, image_size

    def detect(self, frame):
        result = self.model.predict(frame, device=self.device, imgsz=self.image_size,
                                    classes=self.person_classes, conf=.25, iou=.45,
                                    max_det=50, verbose=False)[0]
        return result.boxes.xyxyn.cpu().tolist() if result.boxes is not None else []


def _iou(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0.0


class CameraPolicy:
    """Short bbox histories with IoU association and conservative unknown handling.

    update accepts normalized per-person xyxy boxes from masks or a future detector.
    Timestamps are monotonic capture times, never inference completion times.
    """
    def __init__(self, config=None):
        self.config = config or CameraPolicyConfig()
        self.reset()

    def reset(self):
        self.tracks = {}
        self.next_id = 1
        self.last_at = None
        self.held = None
        self.stop_occupancy = 0

    def update(self, boxes, timestamp, person_target=True):
        c = self.config
        if not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
            self.reset()
            return self._report('DATA_LOSS', 'invalid_geometry_timestamp', [], timestamp=None)
        if self.last_at is not None and (timestamp <= self.last_at or timestamp - self.last_at > c.max_gap_s):
            self.reset()
        self.last_at = timestamp
        if not person_target:
            self.reset()
            return self._report('OBSERVE', 'segmentation_target_is_not_people', [], timestamp)
        detections = []
        for box in boxes:
            if (len(box) != 4 or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in box)
                    or not 0 <= box[0] < box[2] <= 1 or not 0 <= box[1] < box[3] <= 1):
                raise ValueError('Expected normalized xyxy person boxes')
            if box[3] - box[1] >= c.min_box_height:
                detections.append(list(box))
        # Only consecutive detections associate. A disappearance never contributes
        # a stale stop count or an artificial large jump to a new person.
        previous = self.tracks
        candidates = sorted(((_iou(track['box'], box), tid, index)
                             for tid, track in previous.items()
                             for index, box in enumerate(detections)), reverse=True)
        ambiguous = {i for i in range(len(detections))
                     if sum(_iou(t['box'], detections[i]) >= c.match_iou for t in previous.values()) > 1}
        ambiguous_old = {tid for tid, t in previous.items()
                         if sum(_iou(t['box'], box) >= c.match_iou for box in detections) > 1}
        matches, used = {}, set()
        for score, tid, index in candidates:
            if (score >= c.match_iou and index not in matches and tid not in used
                    and index not in ambiguous and tid not in ambiguous_old):
                matches[index] = tid
                used.add(tid)
        current, reports = {}, []
        for index, box in enumerate(detections):
            tid = matches.get(index)
            if tid is None:
                tid, self.next_id = self.next_id, self.next_id + 1
                track = {'history': deque(), 'stop_count': 0}
            else:
                track = previous[tid]
                ratio = (box[3] - box[1]) / (track['box'][3] - track['box'][1])
                if not .55 <= ratio <= 1.8:
                    track = {'history': deque(), 'stop_count': 0}
            track['box'] = box
            history = track['history']
            history.append((timestamp, box))
            while history and timestamp - history[0][0] > c.history_s:
                history.popleft()
            foot_x, foot_y = (box[0] + box[2]) / 2, box[3]
            in_stop = (c.stop_roi[0] <= foot_x <= c.stop_roi[2]
                       and c.stop_roi[1] <= foot_y <= c.stop_roi[3]
                       and box[3] - box[1] >= c.stop_min_height)
            track['stop_count'] = track['stop_count'] + 1 if in_stop else 0
            motion, growth, rate, x_rate = 'UNCERTAIN', None, None, None
            if len(history) >= c.min_samples and timestamp - history[0][0] >= c.min_history_s:
                start_at, start_box = history[0]
                span = timestamp - start_at
                heights = [b[3] - b[1] for _, b in history]
                growth = heights[-1] / heights[0] - 1
                rate = math.log(heights[-1] / heights[0]) / span
                x_rate = (foot_x - (start_box[0] + start_box[2]) / 2) / span
                rising = sum(b >= a * .99 for a, b in zip(heights, heights[1:])) / (len(heights) - 1)
                falling = sum(b <= a * 1.01 for a, b in zip(heights, heights[1:])) / (len(heights) - 1)
                if growth >= c.approach_growth and rate >= c.approach_rate and rising >= .66:
                    motion = 'TOWARD_CAMERA'
                elif growth <= -c.approach_growth and rate <= -c.approach_rate and falling >= .66:
                    motion = 'AWAY_FROM_CAMERA'
                elif abs(growth) < c.approach_growth:
                    motion = 'LATERAL_ACROSS_VIEW' if abs(x_rate) >= c.lateral_rate else 'STATIONARY'
            corridor = c.corridor[0] <= foot_x <= c.corridor[1]
            reports.append({'id': tid, 'bbox_xyxy_normalized': box, 'motion': motion,
                            'height_growth': growth, 'log_height_rate_per_s': rate,
                            'horizontal_rate_per_s': x_rate, 'samples': len(history),
                            'in_corridor': corridor, 'in_stop_roi': in_stop,
                            'stop_count': track['stop_count']})
            current[tid] = track
        self.tracks = current
        # Stop concerns occupied space, so overlapping/merged person IDs must
        # not prevent confirmation of a repeatedly occupied near-camera ROI.
        self.stop_occupancy = self.stop_occupancy + 1 if any(t['in_stop_roi'] for t in reports) else 0
        if self.stop_occupancy >= c.stop_confirmations:
            action, reason = 'STOP', 'person_in_stop_roi_confirmed'
        elif any(t['motion'] == 'TOWARD_CAMERA' and t['in_corridor'] for t in reports):
            action, reason = 'DANGER_WARNING', 'person_approaching_camera_in_corridor'
        elif not reports:
            action, reason = 'OBSERVE', 'no_reliable_person_box'
        elif any(t['in_stop_roi'] or t['motion'] == 'UNCERTAIN' for t in reports):
            action, reason = 'OBSERVE', 'person_geometry_needs_more_evidence'
        else:
            action, reason = 'CONTINUE', 'visible_people_outside_stop_and_no_approach_in_corridor'
        # Brief release hysteresis prevents a single missing/noisy mask clearing
        # a confirmed stop/warning. Health gating still overrides this immediately.
        priority = {'OBSERVE': 0, 'CONTINUE': 0, 'DANGER_WARNING': 1, 'STOP': 2}
        if self.held and timestamp - self.held['at'] < c.release_s and priority[action] < priority[self.held['action']]:
            action, reason = self.held['action'], 'short_release_hold:' + self.held['reason']
        elif priority[action] > 0:
            self.held = {'action': action, 'reason': reason, 'at': timestamp}
        return self._report(action, reason, reports, timestamp)

    def _report(self, action, reason, tracks, timestamp):
        return {'action': action, 'reason_code': reason, 'tracks': tracks, 'evidence_at': timestamp,
                'stop_occupancy_samples': self.stop_occupancy,
                'stop_roi': list(self.config.stop_roi), 'corridor': list(self.config.corridor),
                'coordinate_mode': 'normalized_image_proxy', 'metric_distance': False}


def camera_live_decision(state, now, max_frame_age_s=2.0, max_result_age_s=8.0, max_geometry_age_s=2.5):
    health = live_decision(state, now, max_frame_age_s, max_result_age_s)
    if not health['data_available']:
        return health
    geometry = state.get('camera_geometry')
    stamp = geometry.get('evidence_at') if isinstance(geometry, dict) else None
    valid = (isinstance(stamp, (int, float)) and math.isfinite(stamp)
             and 0 <= now - stamp <= max_geometry_age_s
             and state.get('segmentation_context') == (state.get('loop'), state.get('requested_query')))
    if not valid or geometry.get('action') == 'DATA_LOSS':
        return {'action': 'DATA_LOSS', 'reason_code': 'camera_geometry_missing_or_stale',
                'data_available': False, 'decision_authority': 'runtime_data_health'}
    return {'action': geometry['action'], 'reason_code': geometry['reason_code'], 'data_available': True,
            'decision_authority': 'camera_image_geometry', 'evidence': geometry}
