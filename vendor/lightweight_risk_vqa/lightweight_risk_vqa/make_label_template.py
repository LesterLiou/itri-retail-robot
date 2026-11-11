"""List causal NOW windows that need human future-motion labels."""
import argparse
import bisect
import csv
import json
from pathlib import Path

from .temporal import causal_sequence, normalized_box


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tracks', required=True, type=Path)
    parser.add_argument('--session', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--horizon-s', type=float, default=1.0)
    parser.add_argument('--stride-s', type=float, default=1.0)
    parser.add_argument('--sequence-length', type=int, default=16)
    parser.add_argument('--sample-hz', type=float, default=5.0)
    parser.add_argument('--max-gap-s', type=float, default=0.6)
    args = parser.parse_args(argv)
    if min(args.horizon_s, args.stride_s, args.sample_hz, args.max_gap_s) <= 0 or args.sequence_length < 2:
        parser.error('Invalid horizon, stride or sequence sampling')
    if not args.tracks.is_file():
        parser.error(
            f'Tracks file does not exist: {args.tracks}. '
            'Run video_benchmark or set record_tracks_path first; '
            'then pass the generated tracks.jsonl.'
        )
    if args.output.exists():
        parser.error('Output already exists; choose a new CSV filename')

    observations = {}
    with args.tracks.open() as stream:
        for line in stream:
            report = json.loads(line)
            for camera, data in report['cameras'].items():
                for track in data['tracks']:
                    if not track.get('visible', False):
                        continue
                    key = (camera, str(track['track_id']))
                    history = observations.setdefault(key, [])
                    stamp = float(data['stamp_s'])
                    if history and stamp <= history[-1][0]:
                        raise ValueError('Non-increasing track timestamps; split clock epochs into sessions')
                    history.append((stamp, normalized_box(track['bbox_xyxy'],
                        data['frame_width'], data['frame_height']), float(track['confidence'])))

    rows = []
    for (camera, track_id), observations_for_track in sorted(observations.items()):
        times = [entry[0] for entry in observations_for_track]
        last = float('-inf')
        for index, (stamp, _, _) in enumerate(observations_for_track):
            if stamp - last < args.stride_s - 1e-6:
                continue
            sequence = causal_sequence(observations_for_track[:index + 1], stamp,
                args.sequence_length, args.sample_hz, args.max_gap_s)
            if sequence is None:
                continue
            target = stamp + args.horizon_s
            future_index = bisect.bisect_left(times, target - 1e-6)
            if future_index >= len(times) or times[future_index] - target > args.max_gap_s:
                continue
            if any(b - a > args.max_gap_s for a, b in zip(
                    times[index:future_index], times[index + 1:future_index + 1])):
                continue
            rows.append({'session': args.session, 'camera': camera, 'track_id': track_id,
                'start_s': repr(stamp), 'end_s': repr(stamp), 'label': '',
                'review_until_s': repr(target)})
            last = stamp
    if not rows:
        raise ValueError('No review windows with continuous past and future coverage')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({'output': str(args.output), 'rows_to_label': len(rows),
                      'session': args.session}, indent=2))


if __name__ == '__main__':
    main()
