"""Label future person motion from NOW/FUTURE video frames with an existing VLM.

The future frame is shown only to the offline teacher. Student features are
created separately by prepare_sequences from observations at or before NOW.
"""
import argparse
import bisect
from collections import Counter
import csv
import json
from pathlib import Path
import re

from .worker_client import LlavaWorkerClient

from .temporal import LABELS, box_motion, normalized_box

try:
    import cv2
except ImportError:  # let pure label/sequence tests run outside the ROS image environment
    cv2 = None


PROMPT = ("The image shows the SAME tracked person at NOW (left) and ONE SECOND LATER "
          "(right). The top row shows full camera views with the person in a green box; "
          "the bottom row shows equally scaled crops. Compare the person's apparent size "
          "and position. Relative to the camera, which motion occurred from NOW to LATER? "
          "Answer with exactly ONE token: TOWARD_CAMERA, AWAY_FROM_CAMERA, "
          "LATERAL_ACROSS_VIEW, STATIONARY, or UNCERTAIN. Use UNCERTAIN if occluded "
          "or the images do not establish a direction.")


def teacher_prompt():
    # The worker requires exactly one <image> token per supplied JPEG.
    return 'A chat between a curious user and an artificial intelligence assistant. ' \
           'The assistant gives helpful, detailed, and polite answers to the user. ' \
           '###USER: <image>\n' + PROMPT + '###ASSISTANT:'


def parse_label(raw):
    """Accept one exact class; ambiguous or explanatory output needs review."""
    answer = raw.strip().upper()
    answer = re.sub(r'^[A-Z]+\d+\s*:\s*', '', answer)
    return answer if answer in LABELS else None


def read_observations(path):
    result = {}
    with path.open() as stream:
        for line in stream:
            report = json.loads(line)
            for camera, data in report['cameras'].items():
                for track in data['tracks']:
                    if track.get('visible', False):
                        key = (camera, str(track['track_id']))
                        result.setdefault(key, []).append({
                            'stamp_s': float(data['stamp_s']),
                            'box': tuple(map(float, track['bbox_xyxy'])),
                            'width': int(data['frame_width']),
                            'height': int(data['frame_height']),
                        })
    for key, observations in result.items():
        observations.sort(key=lambda row: row['stamp_s'])
        if any(b['stamp_s'] <= a['stamp_s'] for a, b in zip(observations, observations[1:])):
            raise ValueError(f'Non-increasing timestamps for {key}; split clock epochs')
    return result


def find_pair(observations, now_s, future_s, max_gap_s):
    stamps = [row['stamp_s'] for row in observations]
    now_index = bisect.bisect_left(stamps, now_s - 1e-6)
    later_index = bisect.bisect_left(stamps, future_s - 1e-6)
    if (now_index >= len(stamps) or abs(stamps[now_index] - now_s) > 1e-4
            or later_index >= len(stamps) or stamps[later_index] - future_s > max_gap_s):
        return None
    if any(b - a > max_gap_s for a, b in zip(stamps[now_index:later_index],
                                                stamps[now_index + 1:later_index + 1])):
        return None
    return observations[now_index], observations[later_index]


def read_frame(reader, stamp_s, width, height):
    reader.set(cv2.CAP_PROP_POS_MSEC, stamp_s * 1000)
    ok, frame = reader.read()
    if not ok:
        raise RuntimeError(f'Cannot decode video frame at {stamp_s:.3f}s')
    return cv2.resize(frame, (width, height))


def _crop(frame, box, side):
    height, width = frame.shape[:2]
    center_x, center_y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    crop_w, crop_h = side
    x1 = max(0, min(width - crop_w, round(center_x - crop_w / 2)))
    y1 = max(0, min(height - crop_h, round(center_y - crop_h / 2)))
    return cv2.resize(frame[y1:y1 + crop_h, x1:x1 + crop_w], (224, 224))


def make_panel(now_frame, later_frame, now_box, later_box):
    """Full views plus crops with identical source-pixel scale in both columns."""
    if now_frame.shape != later_frame.shape:
        raise ValueError('Frame dimensions changed within one camera')
    height, width = now_frame.shape[:2]
    crop_w = min(width, max(32, round(max(now_box[2] - now_box[0],
                                          later_box[2] - later_box[0]) * 1.7)))
    crop_h = min(height, max(32, round(max(now_box[3] - now_box[1],
                                           later_box[3] - later_box[1]) * 1.4)))
    tops, bottoms = [], []
    for frame, box, title in ((now_frame, now_box, 'NOW'),
                              (later_frame, later_box, 'ONE SECOND LATER')):
        marked = frame.copy()
        x1, y1, x2, y2 = [round(v) for v in box]
        cv2.rectangle(marked, (x1, y1), (x2, y2), (0, 255, 0), 3)
        top = cv2.resize(marked, (448, 252))
        cv2.rectangle(top, (0, 0), (448, 27), (0, 0, 0), -1)
        cv2.putText(top, title, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                    (255, 255, 255), 2)
        tops.append(top)
        bottoms.append(cv2.resize(_crop(frame, box, (crop_w, crop_h)), (448, 252)))
    return cv2.vconcat([cv2.hconcat(tops), cv2.hconcat(bottoms)])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--template', required=True, type=Path,
                        help='Blank candidate CSV from make_label_template')
    parser.add_argument('--tracks', required=True, type=Path)
    parser.add_argument('--left-video', required=True, type=Path)
    parser.add_argument('--right-video', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='Accepted labels CSV')
    parser.add_argument('--audit', required=True, type=Path, help='Every attempt, including rejected answers')
    parser.add_argument('--panels', required=True, type=Path, help='Reviewable teacher input JPEGs')
    parser.add_argument('--controller-url', default='http://127.0.0.1:11000')
    parser.add_argument('--model-name', default='', help='Empty selects first registered worker')
    parser.add_argument('--max-gap-s', type=float, default=0.6)
    parser.add_argument('--max-requests', type=int, default=0, help='0 means all candidates')
    args = parser.parse_args(argv)
    if cv2 is None:
        parser.error('OpenCV is required to decode teacher video frames')
    if args.max_gap_s <= 0 or args.max_requests < 0:
        parser.error('Invalid max-gap-s or max-requests')
    for path in (args.template, args.tracks, args.left_video, args.right_video):
        if not path.is_file():
            parser.error(f'Input does not exist: {path}')
    if any(path.exists() for path in (args.output, args.audit, args.panels)):
        parser.error('Output already exists; choose new paths')
    with args.template.open(newline='') as stream:
        candidates = list(csv.DictReader(stream))
    if not candidates:
        parser.error('Template has no candidates')
    if any(row.get('label') or abs(float(row['end_s']) - float(row['start_s'])) > 1e-4
           or abs(float(row['review_until_s']) - float(row['start_s']) - 1.0) > 0.1
           for row in candidates):
        parser.error('Template must contain blank NOW labels with a one-second future horizon')
    if len({row['session'] for row in candidates}) != 1:
        parser.error('One teacher run must contain one recording session')
    if args.max_requests:
        candidates = candidates[:args.max_requests]
    observations = read_observations(args.tracks)
    client = LlavaWorkerClient(args.controller_url, model_name=args.model_name,
                               request_timeout_s=45, max_new_tokens=64, request_retry_count=0)
    client._resolve_model()  # fail before creating files if the worker is unavailable
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.panels.mkdir(parents=True, exist_ok=False)
    readers = {camera: cv2.VideoCapture(str(path)) for camera, path in
               (('left', args.left_video), ('right', args.right_video))}
    accepted = 0
    accepted_classes = Counter()
    try:
        if not all(reader.isOpened() for reader in readers.values()):
            raise RuntimeError('Cannot open both videos')
        with args.output.open('x', newline='') as csv_stream, args.audit.open('x') as audit_stream:
            writer = csv.DictWriter(csv_stream, fieldnames=list(candidates[0]))
            writer.writeheader()
            for index, candidate in enumerate(candidates):
                entry = {'candidate': candidate, 'model': client.model_name}
                try:
                    camera, track_id = candidate['camera'], candidate['track_id']
                    now_s = float(candidate['start_s'])
                    later_s = float(candidate['review_until_s'])
                    pair = find_pair(observations.get((camera, track_id), []),
                                     now_s, later_s, args.max_gap_s)
                    if pair is None:
                        raise ValueError('NOW/future track pair missing or discontinuous')
                    now, later = pair
                    now_frame = read_frame(readers[camera], now['stamp_s'], now['width'], now['height'])
                    later_frame = read_frame(readers[camera], later['stamp_s'], later['width'], later['height'])
                    panel = make_panel(now_frame, later_frame, now['box'], later['box'])
                    panel_path = args.panels / f'{index:04d}_{camera}_{track_id}.jpg'
                    ok, jpeg = cv2.imencode('.jpg', panel, [cv2.IMWRITE_JPEG_QUALITY, 88])
                    if not ok:
                        raise RuntimeError('Cannot encode teacher panel')
                    panel_path.write_bytes(jpeg.tobytes())
                    entry['panel'] = str(panel_path)
                    raw, metadata = client.generate(teacher_prompt(), jpeg.tobytes(),
                                                    max_new_tokens=64)
                    teacher = parse_label(raw)
                    weak = box_motion(normalized_box(now['box'], now['width'], now['height']),
                                      normalized_box(later['box'], later['width'], later['height']))
                    entry.update(raw_response=raw, teacher_label=teacher, bbox_weak_label=weak,
                                 now_stamp_s=now['stamp_s'], later_stamp_s=later['stamp_s'],
                                 round_trip_ms=metadata['round_trip_ms'])
                    # A teacher answer alone is not ground truth. Keep only motion labels
                    # supported by independent tracked-box geometry; review the rest.
                    if teacher is not None and teacher == weak and teacher != 'UNCERTAIN':
                        row = dict(candidate, label=teacher)
                        writer.writerow(row)
                        csv_stream.flush()
                        entry['status'] = 'accepted'
                        accepted += 1
                        accepted_classes[teacher] += 1
                    else:
                        entry['status'] = 'needs_review'
                except Exception as exc:
                    entry.update(status='error', error=str(exc))
                audit_stream.write(json.dumps(entry, ensure_ascii=False) + '\n')
                audit_stream.flush()
                print(json.dumps({'index': index + 1, 'total': len(candidates),
                                  'status': entry['status'], 'teacher': entry.get('teacher_label'),
                                  'bbox': entry.get('bbox_weak_label')}, ensure_ascii=False), flush=True)
    finally:
        for reader in readers.values():
            reader.release()
    print(json.dumps({'model': client.model_name, 'candidates': len(candidates),
                      'accepted': accepted, 'needs_review_or_error': len(candidates) - accepted,
                      'accepted_classes': dict(accepted_classes),
                      'labels_csv': str(args.output), 'audit_jsonl': str(args.audit),
                      'panels': str(args.panels)}, indent=2))
    if not accepted:
        raise RuntimeError('No teacher labels passed the agreement gate; review audit and panels')


if __name__ == '__main__':
    main()
