"""Bounded offline two-video smoke/latency run using the exact ROS perception engine."""
import argparse
import json
from pathlib import Path
import time

import cv2
import numpy as np

from .perception import LightweightPerception, draw_tracks, track_report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--left-video', required=True)
    parser.add_argument('--right-video', required=True)
    parser.add_argument('--model-path', default='/workspace/yolov8n.pt')
    parser.add_argument('--device', default='cpu')
    parser.add_argument('--frames', type=int, default=100, help='Paired samples, each containing both views')
    parser.add_argument('--sample-hz', type=float, default=5.0)
    parser.add_argument('--start-s', type=float, default=0.0)
    parser.add_argument('--temporal-mode', choices=['heuristic', 'lstm', 'disabled'], default='heuristic')
    parser.add_argument('--lstm-checkpoint', default='')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--save-preview', action='store_true')
    args = parser.parse_args(argv)
    if args.frames < 1 or args.sample_hz <= 0 or args.start_s < 0:
        parser.error('frames/sample-hz must be positive and start-s nonnegative')
    args.output.mkdir(parents=True, exist_ok=False)
    engine = LightweightPerception(args.model_path, args.device, temporal_mode=args.temporal_mode,
        lstm_checkpoint=args.lstm_checkpoint, sample_hz=args.sample_hz, inference_hz=args.sample_hz)
    readers = {side: cv2.VideoCapture(path) for side, path in (
        ('left', args.left_video), ('right', args.right_video))}
    writer = None
    timings, ids, counts, decode_ms = [], set(), [], []
    started = time.perf_counter()
    try:
        if not all(reader.isOpened() for reader in readers.values()):
            raise FileNotFoundError('Cannot open both input videos')
        with (args.output / 'tracks.jsonl').open('w') as stream:
            for index in range(args.frames):
                stamp = args.start_s + index / args.sample_hz
                cameras, previews = {}, []
                decoded_at = time.perf_counter()
                frames = {}
                for side, reader in readers.items():
                    reader.set(cv2.CAP_PROP_POS_MSEC, stamp * 1000)
                    ok, frame = reader.read()
                    if not ok:
                        break
                    frames[side] = cv2.resize(frame, (960, 540))
                if len(frames) != 2:
                    break
                decode_ms.append((time.perf_counter() - decoded_at) * 1000)
                for side, frame in frames.items():
                    camera = engine.process(side, stamp, frame)
                    cameras[side] = camera
                    timings.append(camera['timing_ms'])
                    ids.update(t['track_id'] for t in camera['tracks'])
                    counts.append(len(camera['tracks']))
                    if args.save_preview:
                        previews.append(draw_tracks(frame, camera))
                stream.write(json.dumps(track_report(cameras, args.model_path)) + '\n')
                if args.save_preview:
                    if writer is None:
                        writer = cv2.VideoWriter(str(args.output / 'preview.mp4'),
                            cv2.VideoWriter_fourcc(*'mp4v'), args.sample_hz, (1920, 540))
                        if not writer.isOpened():
                            raise RuntimeError('Cannot create preview video')
                    writer.write(np.hstack(previews))
        summary = {'paired_samples': len(decode_ms), 'camera_samples': len(timings),
            'model': args.model_path, 'device': args.device, 'temporal_mode': args.temporal_mode,
            'unique_track_ids': sorted(ids), 'mean_visible_tracks_per_camera': float(np.mean(counts)) if counts else 0,
            'wall_s': time.perf_counter() - started, 'warmup_camera_samples_excluded': 2,
            'includes_llm': False, 'includes_ros': False, 'stages_ms': {}}
        measured = timings[2:]
        for stage in ('detection', 'association', 'temporal', 'total'):
            values = [t[stage] for t in measured]
            summary['stages_ms'][stage] = {
                'p50': float(np.percentile(values, 50)) if values else None,
                'p95': float(np.percentile(values, 95)) if values else None}
        summary['decode_seek_resize_p50_ms'] = float(np.median(decode_ms)) if decode_ms else None
        (args.output / 'summary.json').write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary, indent=2))
        if not timings:
            raise RuntimeError('No video samples processed')
    finally:
        for reader in readers.values():
            reader.release()
        if writer:
            writer.release()


if __name__ == '__main__':
    main()
