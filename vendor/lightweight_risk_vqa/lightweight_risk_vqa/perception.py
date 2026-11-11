"""Shared YOLO detector + independent ByteTrack identity states for two cameras."""
import inspect
import os
from pathlib import Path
import time
from types import SimpleNamespace

from .temporal import TemporalEstimator


class LightweightPerception:
    def __init__(self, model_path, device='cpu', image_size=640, confidence=0.1,
                 track_high_threshold=0.35, track_buffer=15, inference_hz=5.0,
                 max_gap_s=1.0, temporal_mode='heuristic', lstm_checkpoint='',
                 sequence_length=16, sample_hz=5.0, temporal_max_gap_s=0.6,
                 temporal_confidence=0.6, cpu_threads=4, allow_download=False):
        if not allow_download and not Path(model_path).is_file():
            raise FileNotFoundError(f'Detector weights missing: {model_path}; supply a local model or allow_download')
        if not 0 <= confidence <= track_high_threshold <= 1:
            raise ValueError('Need 0 <= detector confidence <= track high threshold <= 1')
        if image_size <= 0 or inference_hz <= 0 or track_buffer < 1 or max_gap_s <= 0 or cpu_threads < 1:
            raise ValueError('Invalid detector/tracker configuration')
        os.environ.setdefault('YOLO_AUTOINSTALL', 'false')
        os.environ.setdefault('YOLO_CONFIG_DIR', '/tmp/vlmotion-ultralytics')
        import torch
        from ultralytics import YOLO
        from ultralytics.trackers.byte_tracker import BYTETracker
        torch.set_num_threads(cpu_threads)
        self.model = YOLO(model_path)
        if self.model.names.get(0) != 'person':
            raise ValueError('This adapter expects COCO person class 0')
        self.model_path, self.device, self.image_size = str(model_path), device, image_size
        self.confidence, self.max_gap_s = confidence, max_gap_s
        self.tracker_type = BYTETracker
        self.tracker_args = SimpleNamespace(track_high_thresh=track_high_threshold,
            track_low_thresh=confidence, new_track_thresh=track_high_threshold,
            track_buffer=track_buffer, match_thresh=0.8, fuse_score=True)
        self.inference_hz = inference_hz
        self.trackers = {side: self._make_tracker() for side in ('left', 'right')}
        self.last_stamps = {}
        self.ids = {'left': {}, 'right': {}}
        self.next_id = {'left': 1, 'right': 1}
        self.ages = {}
        self.temporal = TemporalEstimator(temporal_mode, lstm_checkpoint, sequence_length,
            sample_hz, temporal_max_gap_s, temporal_confidence)

    def _make_tracker(self):
        # Ultralytics versions differ in whether frame_rate is accepted.
        kwargs = {'frame_rate': self.inference_hz} if 'frame_rate' in inspect.signature(self.tracker_type).parameters else {}
        return self.tracker_type(self.tracker_args, **kwargs)

    def process(self, side, stamp, frame):
        if side not in self.trackers:
            raise ValueError('camera must be left or right')
        previous = self.last_stamps.get(side)
        if previous is not None and stamp == previous:
            return None  # Repeated images never count as new temporal evidence.
        if previous is not None and (stamp < previous or stamp - previous > self.max_gap_s):
            self.trackers[side] = self._make_tracker()
            self.ids[side].clear()
            # Never recycle exported IDs after time jumps / camera reconnects.
            prefix = 'L' if side == 'left' else 'R'
            for key in list(self.temporal.history):
                if key.startswith(prefix):
                    del self.temporal.history[key]
        self.last_stamps[side] = float(stamp)
        start = time.perf_counter()
        result = self.model.predict(frame, device=self.device, imgsz=self.image_size,
            conf=self.confidence, classes=[0], verbose=False)[0]
        detected = time.perf_counter()
        boxes = result.boxes.cpu().numpy()
        tracked = self.trackers[side].update(boxes, frame)
        associated = time.perf_counter()
        height, width = frame.shape[:2]
        tracks = []
        for row in tracked:
            x1, y1, x2, y2, raw_id, score = map(float, row[:6])
            x1, x2 = max(0.0, x1), min(float(width), x2)
            y1, y2 = max(0.0, y1), min(float(height), y2)
            if x2 <= x1 or y2 <= y1:
                continue
            raw_id = int(raw_id)
            if raw_id not in self.ids[side]:
                self.ids[side][raw_id] = ('L' if side == 'left' else 'R') + str(self.next_id[side])
                self.next_id[side] += 1
            track_id = self.ids[side][raw_id]
            self.ages[track_id] = self.ages.get(track_id, 0) + 1
            box = [round(x, 3) for x in (x1, y1, x2, y2)]
            prediction = self.temporal.update(track_id, stamp, box, score, width, height)
            tracks.append({'track_id': track_id, 'bbox_xyxy': box,
                'footpoint_uv': [(x1 + x2) / 2, y2], 'confidence': score,
                'visible': True, 'missed_frames': 0, 'age_frames': self.ages[track_id],
                'inside_approximate_stop_zone': False, 'stop_zone_foot_overlap_ratio': 0.0,
                'temporal_prediction': prediction})
        finished = time.perf_counter()
        self.temporal.prune(stamp)
        # Bound exported ID bookkeeping to active/lost ByteTrack pools.
        active_raw = {int(t.track_id) for t in self.trackers[side].tracked_stracks + self.trackers[side].lost_stracks}
        self.ids[side] = {key: value for key, value in self.ids[side].items() if key in active_raw}
        retained = set(self.ids['left'].values()) | set(self.ids['right'].values())
        self.ages = {key: value for key, value in self.ages.items() if key in retained}
        return {'stamp_s': float(stamp), 'frame_width': width, 'frame_height': height,
            'raw_detection_count': len(boxes), 'deduplicated_detection_count': len(boxes),
            'duplicates_suppressed': 0, 'walkway_detection_enabled': False,
            'walkway_polygon_uv': [], 'approximate_stop_zone_enabled': False,
            'approximate_stop_zone_metric': False, 'approximate_stop_polygon_uv': [],
            'tracks': tracks, 'timing_ms': {'detection': (detected - start) * 1000,
                'association': (associated - detected) * 1000,
                'temporal': (finished - associated) * 1000,
                'total': (finished - start) * 1000}}


def track_report(cameras, model_path):
    return {'schema_version': '1.0', 'availability': 'READY', 'backend': 'yolo_bytetrack',
        'person_detection_enabled': True, 'tracking_enabled': True,
        'model_id': str(model_path), 'model_revision': None,
        'footpoint_source': 'bbox_bottom_center', 'mask_available': False,
        'cameras': cameras,
        'inference_ms': sum(c['timing_ms']['total'] for c in cameras.values())}


def draw_tracks(frame, camera):
    import cv2
    output = frame.copy()
    for item in camera['tracks']:
        x1, y1, x2, y2 = map(int, item['bbox_xyxy'])
        cv2.rectangle(output, (x1, y1), (x2, y2), (0, 220, 0), 2)
        motion = item['temporal_prediction']['future_motion']
        cv2.putText(output, f"{item['track_id']} {motion}", (x1, max(20, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 0), 1, cv2.LINE_AA)
    return output
