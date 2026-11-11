"""Convert JAAD human tracks to causal RGB clips with explicit weak-motion labels.

Future images are written only to review panels, never to training manifests.
No teacher model or training job is started by this module.
"""
import argparse
from collections import Counter
import csv
import hashlib
import html
import json
from pathlib import Path
import random
import subprocess
import xml.etree.ElementTree as ET

from .temporal import LABELS, box_motion, normalized_box


QUESTION = (
    'The target person is outlined in green in chronological frames ending at NOW. '
    "Predict that target's camera-relative motion one second after NOW. Return exactly "
    'one JSON object with keys motion, action, and explanation. motion must be one of '
    'TOWARD_CAMERA, AWAY_FROM_CAMERA, LATERAL_ACROSS_VIEW, STATIONARY, UNCERTAIN.'
)


def read_splits(root):
    splits = {name: (root / f'{name}.txt').read_text().split()
              for name in ('train', 'val', 'test')}
    seen = set()
    for name, videos in splits.items():
        if len(set(videos)) != len(videos) or seen.intersection(videos):
            raise ValueError(f'Duplicate or overlapping official split: {name}')
        seen.update(videos)
    return splits


def read_tracks(path):
    root = ET.parse(path).getroot()
    width = int(root.findtext('meta/task/original_size/width'))
    height = int(root.findtext('meta/task/original_size/height'))
    tracks = {}
    for track in root.findall('track'):
        if track.get('label') not in ('pedestrian', 'ped'):
            continue  # Group-of-people boxes do not identify one target.
        rows = {}
        identity = None
        for node in track.findall('box'):
            attrs = {a.get('name'): a.text for a in node.findall('attribute')}
            if attrs.get('id'):
                if identity and identity != attrs['id']:
                    raise ValueError(f'Track identity changes in {path}')
                identity = attrs['id']
            box = tuple(float(node.get(key)) for key in ('xtl', 'ytl', 'xbr', 'ybr'))
            if node.get('outside', '0') == '1' or box[2] <= box[0] or box[3] <= box[1]:
                continue
            rows[int(node.get('frame'))] = {'box': box, 'attributes': attrs,
                                          'occluded': node.get('occluded', '0') == '1'}
        if identity and rows:
            if identity in tracks:
                raise ValueError(f'Duplicate person ID in {path}: {identity}')
            tracks[identity] = rows
    return width, height, tracks


def candidates_for_track(rows, fps, width, height, *, steps=8, sample_hz=2.0,
                         stride_s=1.0, min_height=64):
    if fps <= 0 or sample_hz <= 0 or stride_s <= 0 or steps < 2:
        raise ValueError('Invalid sampling configuration')
    if not rows:
        return []
    history_span = (steps - 1) / sample_hz
    first, last = min(rows), max(rows)
    start = first + round(history_span * fps)
    result = []
    for now in range(start, last - round(fps) + 1, max(1, round(stride_s * fps))):
        indices = [now - round((steps - 1 - i) * fps / sample_hz) for i in range(steps)]
        future = now + round(fps)
        if len(set(indices)) != steps or indices[-1] != now:
            continue
        continuous = [rows.get(i) for i in range(indices[0], future + 1)]
        if any(row is None or row['attributes'].get('occlusion') == 'full'
               for row in continuous):
            continue
        if any(rows[i]['box'][3] - rows[i]['box'][1] < min_height for i in indices + [future]):
            continue
        motion = box_motion(normalized_box(rows[now]['box'], width, height),
                            normalized_box(rows[future]['box'], width, height))
        result.append({'now': now, 'future': future, 'indices': indices,
                       'motion': motion, 'now_attributes': rows[now]['attributes'],
                       'future_attributes': rows[future]['attributes']})
    return result


def answer_for(motion):
    explanation = {
        'TOWARD_CAMERA': 'The target is expected to become larger or approach the camera.',
        'AWAY_FROM_CAMERA': 'The target is expected to become smaller or move away.',
        'LATERAL_ACROSS_VIEW': 'The target is expected to move laterally across the view.',
        'STATIONARY': 'The target is expected to remain approximately stationary.',
        'UNCERTAIN': 'The available history does not establish a reliable direction.',
    }[motion]
    return json.dumps({'motion': motion,
                       'action': 'DANGER_WARNING' if motion in ('TOWARD_CAMERA', 'UNCERTAIN')
                       else 'CONTINUE', 'explanation': explanation}, separators=(',', ':'))


def decode_selected(video, indices, width=960, height=540):
    import cv2
    reader = cv2.VideoCapture(str(video))
    if not reader.isOpened():
        raise RuntimeError(f'Cannot open {video}')
    frames = {}
    try:
        for index in sorted(indices):
            reader.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = reader.read()
            if not ok:
                raise RuntimeError(f'Cannot decode {video} at frame {index}')
            frames[index] = cv2.resize(frame, (width, height))
    finally:
        reader.release()
    return frames


def write_review(output, split, records):
    pages = []
    for offset in range(0, len(records), 100):
        page = f'{split}_{offset // 100 + 1:03d}.html'
        pages.append(page)
        cards = []
        for record in records[offset:offset + 100]:
            target = html.escape(record['id'])
            panel = html.escape(record['review_panel'])
            motion = html.escape(json.loads(record['answer'])['motion'])
            cards.append(f'<article><h3>{target} — weak label: {motion}</h3>'
                         f'<img loading="lazy" src="../../{panel}" width="100%">'
                         '<p>First four panels are historical evidence. Last panel is FUTURE '
                         '(review only). Review against the source video; fill review_queue.csv.</p></article>')
        (output / 'review' / 'pages' / page).write_text(
            '<!doctype html><meta charset="utf-8"><title>JAAD review</title>'
            '<style>body{font:16px sans-serif;max-width:1400px;margin:auto}'
            'article{border:1px solid #ccc;padding:12px;margin:15px 0}</style>'
            '<a href="../../index.html">Index</a>' + ''.join(cards))
    return pages


def main(argv=None):
    import cv2
    import numpy as np
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jaad-root', type=Path, required=True)
    parser.add_argument('--videos', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--split-set', choices=['all_videos', 'default', 'high_visibility'],
                        default='all_videos')
    parser.add_argument('--steps', type=int, default=8)
    parser.add_argument('--sample-hz', type=float, default=2)
    parser.add_argument('--stride-s', type=float, default=1)
    parser.add_argument('--min-height', type=float, default=64)
    parser.add_argument('--per-video-limit', type=int, default=80)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args(argv)
    if (args.steps < 2 or min(args.sample_hz, args.stride_s, args.min_height,
                              args.per_video_limit) <= 0):
        parser.error('Invalid sampling or size limits')
    split_root = args.jaad_root / 'split_ids' / args.split_set
    splits = read_splits(split_root)
    if args.output.exists():
        parser.error('Output exists: choose a new dataset version')
    for videos in splits.values():
        for video in videos:
            if not (args.videos / f'{video}.mp4').is_file():
                parser.error(f'Missing video: {video}')
            if not (args.jaad_root / 'annotations' / f'{video}.xml').is_file():
                parser.error(f'Missing annotation: {video}')
    args.output.mkdir(parents=True)
    (args.output / 'review' / 'pages').mkdir(parents=True)
    (args.output / 'review' / 'panels').mkdir()
    (args.output / 'manifests').mkdir()
    rng = random.Random(args.seed)
    records_by_split = {}
    sources = []
    for split, videos in splits.items():
        records = []
        for video in videos:
            path = args.videos / f'{video}.mp4'
            reader = cv2.VideoCapture(str(path))
            fps = reader.get(cv2.CAP_PROP_FPS)
            decoded_width = round(reader.get(cv2.CAP_PROP_FRAME_WIDTH))
            decoded_height = round(reader.get(cv2.CAP_PROP_FRAME_HEIGHT))
            reader.release()
            if not np.isfinite(fps) or fps <= 0:
                raise ValueError(f'Invalid video FPS: {path}')
            xml_path = args.jaad_root / 'annotations' / f'{video}.xml'
            width, height, tracks = read_tracks(xml_path)
            if (decoded_width, decoded_height) != (width, height):
                raise ValueError(f'Annotation/video dimensions differ for {video}')
            candidates = []
            for person, rows in sorted(tracks.items()):
                for candidate in candidates_for_track(
                        rows, fps, width, height, steps=args.steps,
                        sample_hz=args.sample_hz, stride_s=args.stride_s,
                        min_height=args.min_height):
                    candidates.append(dict(candidate, person=person))
            eligible = len(candidates)
            if eligible > args.per_video_limit:
                candidates = rng.sample(candidates, args.per_video_limit)
            candidates.sort(key=lambda c: (c['now'], c['person']))
            sources.append({'video': video, 'split': split, 'fps': fps,
                            'video_path': str(path), 'annotation_path': str(xml_path),
                            'annotation_sha256': hashlib.sha256(xml_path.read_bytes()).hexdigest(),
                            'eligible_windows': eligible, 'exported_windows': len(candidates)})
            if not candidates:
                continue
            wanted = {i for c in candidates for i in c['indices'] + [c['future']]}
            frames = decode_selected(path, wanted)
            for candidate in candidates:
                person, now = candidate['person'], candidate['now']
                rows = tracks[person]
                identity = f'jaad_{video}_{person}_{now:06d}'
                destination = args.output / 'frames' / identity
                destination.mkdir(parents=True)
                paths = []
                marked_frames = []
                for j, index in enumerate(candidate['indices'] + [candidate['future']]):
                    frame = frames[index].copy()
                    x1, y1, x2, y2 = rows[index]['box']
                    coords = (round(x1 * 960 / width), round(y1 * 540 / height),
                              round(x2 * 960 / width), round(y2 * 540 / height))
                    cv2.rectangle(frame, coords[:2], coords[2:], (0, 255, 0), 3)
                    cv2.putText(frame, f'TARGET {person}', (max(0, coords[0]), max(20, coords[1] - 4)),
                                cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 255, 0), 2)
                    if j < args.steps:
                        image_path = destination / f'{j:02d}.jpg'
                        if not cv2.imwrite(str(image_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 85]):
                            raise RuntimeError(f'Cannot write {image_path}')
                        paths.append(str(image_path.resolve()))
                    marked_frames.append(frame)
                review_indices = sorted(set([0, args.steps // 3, 2 * args.steps // 3, args.steps - 1]))
                tiles = []
                for j in review_indices + [args.steps]:
                    tile = cv2.resize(marked_frames[j], (320, 180))
                    index = (candidate['indices'] + [candidate['future']])[j]
                    label = 'FUTURE REVIEW ONLY' if j == args.steps else f'HISTORY {index / fps:.2f}s'
                    cv2.rectangle(tile, (0, 0), (320, 24), (0, 0, 0), -1)
                    cv2.putText(tile, label, (4, 17), cv2.FONT_HERSHEY_SIMPLEX, .45,
                                (255, 255, 255), 1)
                    tiles.append(tile)
                panel = args.output / 'review' / 'panels' / f'{identity}.jpg'
                if not cv2.imwrite(str(panel), np.hstack(tiles)):
                    raise RuntimeError(f'Cannot write {panel}')
                records.append({
                    'id': identity, 'group_id': f'jaad_{video}', 'frames': paths,
                    'frame_indices': list(range(args.steps)), 'time_unit': 'frames',
                    'query': QUESTION, 'answer': answer_for(candidate['motion']),
                    'dataset': 'JAAD_2.0', 'split': split, 'video_id': video,
                    'track_id': person, 'now_s': now / fps, 'prediction_horizon_s': 1.0,
                    'source_frame_indices': candidate['indices'],
                    'source_timestamps_s': [i / fps for i in candidate['indices']],
                    'label_future_frame_index': candidate['future'], 'source_fps': fps,
                    'label_source': 'jaad_human_boxes_future_geometry',
                    'human_reviewed': False, 'action_source': 'direction_policy_template',
                    'explanation_source': 'direction_text_template',
                    'now_human_attributes': candidate['now_attributes'],
                    'future_human_attributes': candidate['future_attributes'],
                    'review_panel': str(panel.relative_to(args.output)),
                })
            print(json.dumps({'video': video, 'split': split, 'samples': len(candidates)}), flush=True)
        records_by_split[split] = records
        with (args.output / 'manifests' / f'{split}.jsonl').open('x') as stream:
            for record in records:
                stream.write(json.dumps(record) + '\n')
    if not all(records_by_split.values()):
        raise ValueError('At least one split is empty; do not train this export')
    with (args.output / 'sources.jsonl').open('x') as stream:
        for row in sources:
            stream.write(json.dumps(row) + '\n')
    with (args.output / 'review_queue.csv').open('x', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            'id', 'split', 'video_id', 'track_id', 'now_s', 'suggested_motion',
            'reviewed_motion', 'review_status', 'notes', 'panel'])
        writer.writeheader()
        for records in records_by_split.values():
            for row in records:
                writer.writerow({key: row[key] for key in ('id', 'split', 'video_id', 'track_id', 'now_s')} | {
                    'suggested_motion': json.loads(row['answer'])['motion'],
                    'reviewed_motion': '', 'review_status': 'pending', 'notes': '',
                    'panel': row['review_panel']})
    pages = {split: write_review(args.output, split, rows)
             for split, rows in records_by_split.items()}
    (args.output / 'index.html').write_text(
        '<!doctype html><meta charset="utf-8"><h1>JAAD temporal VQA — weak labels, review pending</h1>'
        '<p>Human boxes/IDs, derived apparent image-space motion; not human risk ground truth. '
        'Vehicle motion can change scale. Future images appear ONLY in review panels.</p>'
        + ''.join(f'<h2>{split}: {len(records_by_split[split])} samples</h2>' + ' '.join(
            f'<a href="review/pages/{page}">{page}</a>' for page in names)
                  for split, names in pages.items()))
    commit = subprocess.check_output(['git', '-C', str(args.jaad_root), 'rev-parse', 'HEAD'],
                                     text=True).strip()
    summary = {
        'status': 'weak_labels_ready_for_research_training; human review pending',
        'source': 'https://github.com/ykotseruba/JAAD', 'source_commit': commit,
        'videos_url': 'https://data.nvision2.eecs.yorku.ca/JAAD_dataset/data/JAAD_clips.zip',
        'video_license': 'CC BY 4.0 (see upstream README)', 'annotation_code_license': 'MIT',
        'split_set': args.split_set, 'steps': args.steps, 'sample_hz': args.sample_hz,
        'stride_s': args.stride_s, 'seed': args.seed, 'min_height': args.min_height,
        'per_video_limit': args.per_video_limit,
        'prediction_horizon_s': 1.0, 'future_frames_in_training_input': False,
        'time_unit': 'frames', 'human_reviewed': False,
        'splits': {split: {'samples': len(rows),
                           'videos_with_samples': len({r['video_id'] for r in rows}),
                           'motion_counts': dict(Counter(json.loads(r['answer'])['motion'] for r in rows))}
                   for split, rows in records_by_split.items()},
    }
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    main()
