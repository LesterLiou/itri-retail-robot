"""Build causal image-sequence/answer JSONL for Light Foundation temporal VQA."""
import argparse
import bisect
import csv
import json
from pathlib import Path

import cv2

from .temporal import LABELS, box_motion, normalized_box


QUESTION = (
    'The target person is outlined in green in chronological frames ending at NOW. '
    'Predict that target\'s camera-relative motion one second after NOW. Return exactly '
    'one JSON object with keys motion, action, and explanation. motion must be one of '
    'TOWARD_CAMERA, AWAY_FROM_CAMERA, LATERAL_ACROSS_VIEW, STATIONARY, UNCERTAIN.'
)


def read_tracks(path):
    tracks = {}
    with path.open() as stream:
        for line in stream:
            report = json.loads(line)
            for camera, data in report['cameras'].items():
                for track in data['tracks']:
                    if not track.get('visible', False):
                        continue
                    row = {'stamp_s': float(data['stamp_s']),
                           'box': tuple(map(float, track['bbox_xyxy'])),
                           'width': int(data['frame_width']),
                           'height': int(data['frame_height'])}
                    tracks.setdefault((camera, str(track['track_id'])), []).append(row)
    for key, rows in tracks.items():
        rows.sort(key=lambda row: row['stamp_s'])
        if any(b['stamp_s'] <= a['stamp_s'] for a, b in zip(rows, rows[1:])):
            raise ValueError(f'Non-increasing timestamps for {key}')
    return tracks


def causal_rows(rows, now_s, steps, sample_hz, max_gap_s):
    stamps = [row['stamp_s'] for row in rows]
    selected = []
    for grid in [now_s - (steps - 1 - index) / sample_hz for index in range(steps)]:
        position = bisect.bisect_right(stamps, grid + 1e-7) - 1
        if position < 0 or grid - stamps[position] > max_gap_s:
            return None
        selected.append(rows[position])
    return selected


def future_row(rows, target_s, max_gap_s):
    stamps = [row['stamp_s'] for row in rows]
    position = bisect.bisect_left(stamps, target_s - 1e-7)
    if position >= len(rows) or stamps[position] - target_s > max_gap_s:
        return None
    return rows[position]


def answer_for(label, source):
    action = 'DANGER_WARNING' if label in {'TOWARD_CAMERA', 'UNCERTAIN'} else 'CONTINUE'
    explanation = {
        'TOWARD_CAMERA': 'The target is expected to become larger or approach the camera.',
        'AWAY_FROM_CAMERA': 'The target is expected to become smaller or move away.',
        'LATERAL_ACROSS_VIEW': 'The target is expected to move laterally across the view.',
        'STATIONARY': 'The target is expected to remain approximately stationary.',
        'UNCERTAIN': 'The available history does not establish a reliable direction.',
    }[label]
    # Keep provenance in record metadata. The supervised answer must match the
    # three-key schema in QUESTION exactly; teaching provenance as prose makes
    # the generator invent URLs or unrelated source names at inference time.
    return json.dumps({'motion': label, 'action': action, 'explanation': explanation},
                      separators=(',', ':'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tracks', required=True, type=Path)
    parser.add_argument('--session', required=True)
    parser.add_argument('--left-video', required=True, type=Path)
    parser.add_argument('--right-video', required=True, type=Path)
    labels = parser.add_mutually_exclusive_group(required=True)
    labels.add_argument('--labels', type=Path,
                        help='Reviewed CSV from the labeling workflow')
    labels.add_argument('--pseudo-labels', action='store_true',
                        help='Use future bbox geometry; pipeline testing only')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--steps', type=int, default=8)
    parser.add_argument('--sample-hz', type=float, default=2.0)
    parser.add_argument('--horizon-s', type=float, default=1.0)
    parser.add_argument('--stride-s', type=float, default=1.0)
    parser.add_argument('--max-gap-s', type=float, default=0.6)
    parser.add_argument('--include-uncertain', action='store_true')
    parser.add_argument('--max-samples', type=int, default=0)
    args = parser.parse_args(argv)
    if (args.steps < 2 or min(args.sample_hz, args.horizon_s, args.stride_s,
                              args.max_gap_s) <= 0 or args.max_samples < 0):
        parser.error('Invalid sequence, horizon, stride, gap, or sample limit')
    for path in (args.tracks, args.left_video, args.right_video):
        if not path.is_file():
            parser.error(f'Input does not exist: {path}')
    if args.labels and not args.labels.is_file():
        parser.error(f'Labels do not exist: {args.labels}')
    if args.output.exists():
        parser.error('Output directory already exists')
    annotations = []
    if args.labels:
        with args.labels.open(newline='') as stream:
            annotations = list(csv.DictReader(stream))
        if any(row['label'] not in LABELS for row in annotations):
            parser.error('Labels CSV contains an empty or unsupported class')
    tracks = read_tracks(args.tracks)
    readers = {camera: cv2.VideoCapture(str(path)) for camera, path in
               (('left', args.left_video), ('right', args.right_video))}
    args.output.mkdir(parents=True)
    frames_root = args.output / 'frames'
    frames_root.mkdir()
    records, counts = [], {label: 0 for label in LABELS}
    try:
        if not all(reader.isOpened() for reader in readers.values()):
            raise RuntimeError('Cannot open both input videos')
        for (camera, track_id), rows in sorted(tracks.items()):
            last = float('-inf')
            for now in rows:
                if now['stamp_s'] - last < args.stride_s - 1e-6:
                    continue
                history = causal_rows(rows, now['stamp_s'], args.steps,
                                      args.sample_hz, args.max_gap_s)
                if history is None:
                    continue
                if args.pseudo_labels:
                    future = future_row(rows, now['stamp_s'] + args.horizon_s, args.max_gap_s)
                    if future is None:
                        continue
                    label = box_motion(normalized_box(now['box'], now['width'], now['height']),
                                       normalized_box(future['box'], future['width'], future['height']))
                    source = 'future_bbox_pseudo'
                else:
                    matches = [row['label'] for row in annotations
                               if row['session'] == args.session
                               and row['camera'] == camera and row['track_id'] == track_id
                               and float(row['start_s']) <= now['stamp_s'] <= float(row['end_s'])]
                    if not matches:
                        continue
                    if len(set(matches)) != 1:
                        raise ValueError('Conflicting reviewed labels')
                    label, source = matches[0], 'reviewed_or_teacher_csv'
                if label == 'UNCERTAIN' and not args.include_uncertain:
                    continue
                sample_id = f'{args.session}_{camera}_{track_id}_{len(records):05d}'
                sample_dir = frames_root / sample_id
                sample_dir.mkdir()
                paths = []
                for index, observation in enumerate(history):
                    reader = readers[camera]
                    reader.set(cv2.CAP_PROP_POS_MSEC, observation['stamp_s'] * 1000)
                    ok, frame = reader.read()
                    if not ok:
                        raise RuntimeError(f'Cannot decode {camera} at {observation["stamp_s"]}')
                    frame = cv2.resize(frame, (observation['width'], observation['height']))
                    x1, y1, x2, y2 = map(round, observation['box'])
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 3)
                    cv2.putText(frame, f'TARGET {track_id}', (max(0, x1), max(22, y1 - 5)),
                                cv2.FONT_HERSHEY_SIMPLEX, .65, (0, 255, 0), 2)
                    destination = sample_dir / f'{index:02d}.jpg'
                    if not cv2.imwrite(str(destination), frame,
                                       [cv2.IMWRITE_JPEG_QUALITY, 88]):
                        raise RuntimeError(f'Cannot write {destination}')
                    paths.append(str(destination.relative_to(args.output)))
                records.append({'id': sample_id, 'group_id': args.session,
                                'frames': paths, 'frame_indices': list(range(args.steps)),
                                'time_unit': 'frames', 'query': QUESTION,
                                'answer': answer_for(label, source), 'camera': camera,
                                'track_id': track_id, 'now_s': now['stamp_s'],
                                'prediction_horizon_s': args.horizon_s,
                                'label_source': source})
                counts[label] += 1
                last = now['stamp_s']
                if args.max_samples and len(records) >= args.max_samples:
                    break
            if args.max_samples and len(records) >= args.max_samples:
                break
    except Exception:
        # Preserve partial output for diagnosis; no manifest means it cannot be trained accidentally.
        raise
    finally:
        for reader in readers.values():
            reader.release()
    if not records:
        raise ValueError('No causal labelled clips could be exported')
    manifest = args.output / 'samples.jsonl'
    with manifest.open('x') as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
    summary = {'session': args.session, 'samples': len(records), 'label_counts': counts,
               'label_source': 'future_bbox_pseudo' if args.pseudo_labels else 'reviewed_or_teacher_csv',
               'causal_input': True, 'future_frames_in_input': False,
               'manifest': str(manifest)}
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
