"""Export causal LSTM windows from recorded tracks; manual or explicit pseudo labels."""
import argparse
import bisect
import csv
import json
from pathlib import Path

import numpy as np

from .temporal import FEATURE_NAMES, FEATURE_VERSION, LABELS, box_motion, causal_sequence, normalized_box


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tracks', required=True, type=Path)
    parser.add_argument('--session', required=True, help='Unique recording/session; split datasets by this ID')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--labels', type=Path, help='CSV: session,camera,track_id,start_s,end_s,label')
    group.add_argument('--pseudo-labels', action='store_true', help='Future bbox-change labels, NOT human ground truth')
    parser.add_argument('--label-source', choices=('manual', 'llm_teacher_bbox_agreed'), default='manual',
                        help='Provenance for --labels CSV; never call teacher labels manual')
    parser.add_argument('--horizon-s', type=float, default=1.0)
    parser.add_argument('--sequence-length', type=int, default=16)
    parser.add_argument('--sample-hz', type=float, default=5.0)
    parser.add_argument('--max-gap-s', type=float, default=0.6)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    if args.horizon_s <= 0 or args.sample_hz <= 0 or args.sequence_length < 2 or args.max_gap_s <= 0:
        parser.error('Invalid horizon or temporal sampling settings')
    if args.pseudo_labels and args.label_source != 'manual':
        parser.error('--label-source applies only to --labels')
    if args.output.exists():
        parser.error('Output already exists; use a new dataset filename')
    annotations = []
    if args.labels:
        with args.labels.open(newline='') as stream:
            annotations = list(csv.DictReader(stream))
        for row in annotations:
            if row['label'] not in LABELS or float(row['end_s']) < float(row['start_s']):
                raise ValueError('Invalid label interval')
    observations = {}
    with args.tracks.open() as stream:
        for line in stream:
            report = json.loads(line)
            for camera, data in report['cameras'].items():
                for track in data['tracks']:
                    if not track.get('visible', False):
                        continue
                    key = (camera, str(track['track_id']))
                    sequence = observations.setdefault(key, [])
                    stamp = float(data['stamp_s'])
                    if sequence and stamp <= sequence[-1][0]:
                        raise ValueError('Non-increasing track timestamps; export each clock epoch as a separate session')
                    sequence.append((stamp, normalized_box(track['bbox_xyxy'], data['frame_width'], data['frame_height']),
                                     float(track['confidence'])))
    features, labels, stamps, track_ids = [], [], [], []
    for (camera, track_id), track in observations.items():
        times = [row[0] for row in track]
        last_export = float('-inf')
        for index, (stamp, box, _) in enumerate(track):
            if stamp - last_export < 1 / args.sample_hz - 1e-6:
                continue
            sequence = causal_sequence(track[:index + 1], stamp, args.sequence_length, args.sample_hz, args.max_gap_s)
            if sequence is None:
                continue
            if args.pseudo_labels:
                target = stamp + args.horizon_s
                future_index = bisect.bisect_left(times, target - 1e-6)
                if future_index >= len(track) or times[future_index] - target > args.max_gap_s:
                    continue
                if any(b - a > args.max_gap_s for a, b in zip(times[index:future_index], times[index + 1:future_index + 1])):
                    continue
                label = box_motion(box, track[future_index][1])
            else:
                matches = [r['label'] for r in annotations
                    if r['session'] == args.session and r['camera'] == camera and r['track_id'] == track_id
                    and float(r['start_s']) <= stamp <= float(r['end_s'])]
                if len(set(matches)) > 1:
                    raise ValueError('Conflicting manual label intervals')
                if not matches:
                    continue
                label = matches[0]
            features.append(sequence)
            labels.append(LABELS.index(label))
            stamps.append(stamp)
            track_ids.append(camera + ':' + track_id)
            last_export = stamp
    if not features:
        raise ValueError('No valid labelled windows; need continuous history and matching manual labels / future observations')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as stream:
        np.savez_compressed(stream, features=np.stack(features), labels=np.asarray(labels, dtype=np.int64),
            sessions=np.full(len(labels), args.session), stamps=np.asarray(stamps), track_ids=np.asarray(track_ids),
            feature_version=np.asarray(FEATURE_VERSION), feature_names=np.asarray(FEATURE_NAMES),
            label_names=np.asarray(LABELS), sample_hz=np.asarray(args.sample_hz),
            prediction_horizon_s=np.asarray(args.horizon_s),
            label_source=np.asarray('future_bbox_pseudo' if args.pseudo_labels else args.label_source))
    print(json.dumps({'output': str(args.output), 'windows': len(labels), 'session': args.session,
        'label_counts': {name: labels.count(i) for i, name in enumerate(LABELS)},
        'label_source': 'future_bbox_pseudo' if args.pseudo_labels else args.label_source}, indent=2))


if __name__ == '__main__':
    main()
