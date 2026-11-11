"""Replay offline per-track clip VQA answers on their original source video.

This is a visualization of completed inference, not a real-time benchmark.
"""
import argparse
import bisect
import json
from pathlib import Path


def read_camera_records(manifest, camera):
    records = {row['id']: row for row in (
        json.loads(line) for line in Path(manifest).read_text().splitlines() if line.strip())
        if row['camera'] == camera}
    if len({row['group_id'] for row in records.values()}) > 1:
        raise ValueError('Use a single recording manifest; track IDs may collide across recordings')
    return records


def main(argv=None):
    import cv2
    import numpy as np

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video', type=Path, required=True)
    parser.add_argument('--tracks', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--predictions', type=Path, required=True)
    parser.add_argument('--camera', choices=['left', 'right'], default='left')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--fps', type=float, default=10)
    parser.add_argument('--model-label', default='ConvLSTM + Qwen3-0.6B')
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error('Output exists; use a new filename')
    if args.fps <= 0:
        parser.error('FPS must be positive')
    try:
        records = read_camera_records(args.manifest, args.camera)
    except ValueError as exc:
        parser.error(str(exc))
    per_track = {}
    for line in args.predictions.read_text().splitlines():
        prediction = json.loads(line)
        if prediction.get('id') not in records:
            continue
        row = records[prediction['id']]
        per_track.setdefault(str(row['track_id']), []).append((row['now_s'], prediction))
    for values in per_track.values():
        values.sort(key=lambda value: value[0])
    prediction_stamps = {key: [value[0] for value in rows] for key, rows in per_track.items()}
    cameras = []
    for line in args.tracks.read_text().splitlines():
        report = json.loads(line)
        if args.camera in report['cameras']:
            cameras.append(report['cameras'][args.camera])
    cameras.sort(key=lambda camera: camera['stamp_s'])
    if not cameras or not records:
        parser.error('No matching camera data or VQA records')
    stamps = [camera['stamp_s'] for camera in cameras]
    reader = cv2.VideoCapture(str(args.video))
    source_fps = reader.get(cv2.CAP_PROP_FPS)
    if not reader.isOpened() or source_fps <= 0:
        parser.error('Cannot open video')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(args.output), cv2.VideoWriter_fourcc(*'mp4v'), args.fps, (960, 630))
    if not writer.isOpened():
        reader.release()
        raise RuntimeError('Cannot create video writer')
    index, written, next_stamp = 0, 0, 0.0
    try:
        while True:
            ok, original = reader.read()
            if not ok:
                break
            stamp = index / source_fps
            index += 1
            if stamp + 1e-6 < next_stamp:
                continue
            next_stamp += 1.0 / args.fps
            frame = cv2.resize(original, (960, 540))
            camera_index = bisect.bisect_right(stamps, stamp + 1e-6) - 1
            current = cameras[camera_index] if camera_index >= 0 else None
            count = 0
            if current and stamp - current['stamp_s'] <= .4:
                for track in current['tracks']:
                    if not track.get('visible'):
                        continue
                    identity = str(track['track_id'])
                    x1, y1, x2, y2 = track['bbox_xyxy']
                    x1, x2 = round(x1 * 960 / current['frame_width']), round(x2 * 960 / current['frame_width'])
                    y1, y2 = round(y1 * 540 / current['frame_height']), round(y2 * 540 / current['frame_height'])
                    candidates = per_track.get(identity, [])
                    position = bisect.bisect_right(prediction_stamps.get(identity, []), stamp + 1e-6) - 1
                    prediction = candidates[position][1] if position >= 0 else None
                    if position >= 0 and stamp - candidates[position][0] > 1.2:
                        prediction = None
                    color = (0, 255, 0) if prediction else (210, 180, 0)
                    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                    lines = [identity + ': collecting history']
                    if prediction:
                        count += 1
                        raw = prediction['prediction']
                        motion = raw.get('motion', 'INVALID') if isinstance(raw, dict) else 'INVALID_JSON'
                        action = raw.get('action', 'INVALID') if isinstance(raw, dict) else 'UNAVAILABLE'
                        lines = [f'{identity}: {motion}', f'{action} | {prediction["latency_ms"]:.0f}ms']
                    anchor_y = max(18, min(490, y1 - 35))
                    anchor_x = max(0, min(600, x1))
                    for n, label in enumerate(lines):
                        y = anchor_y + n * 19
                        cv2.rectangle(frame, (anchor_x, y - 16), (min(959, anchor_x + 355), y + 3), (0, 0, 0), -1)
                        cv2.putText(frame, label, (anchor_x + 3, y), cv2.FONT_HERSHEY_SIMPLEX, .48,
                                    (255, 255, 255), 1, cv2.LINE_AA)
            header = np.zeros((90, 960, 3), dtype=np.uint8)
            texts = [
                'OFFLINE VQA REPLAY | ' + args.model_label,
                f'{args.video.name} | {stamp:.1f}s | camera={args.camera} | active answers={count}',
                '8 past frames -> predict next 1s | YOLO boxes are input hints | not a live speed benchmark',
            ]
            for n, label in enumerate(texts):
                cv2.putText(header, label, (10, 23 + n * 27), cv2.FONT_HERSHEY_SIMPLEX, .53,
                            (255, 255, 255), 1, cv2.LINE_AA)
            writer.write(np.vstack([header, frame]))
            written += 1
    finally:
        reader.release()
        writer.release()
    if not written:
        raise RuntimeError('No video frames written')
    print(json.dumps({'output': str(args.output), 'frames': written,
                      'duration_s': written / args.fps, 'offline_replay': True}))


if __name__ == '__main__':
    main()
