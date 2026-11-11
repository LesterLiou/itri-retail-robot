"""Export local COCO person masks and causal JAAD human-activity clips, without test data."""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import html
import json
import os
from pathlib import Path
import random

from .person_state import STATE_QUERY, activity_transition, state_answer
from .prepare_jaad_vqa import read_splits, read_tracks


def write_jsonl(path, rows):
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))


def state_record(video, person, indices, frames, annotation, reused=False):
    labels = [annotation[i]['attributes'].get('action') for i in indices]
    target = activity_transition(labels)
    if target is None:
        return None
    activity, transition = target
    return {'id': f'state_{video}_{person}_{indices[-1]:06d}', 'group_id': f'jaad_{video}',
        'frames': frames, 'frame_indices': list(range(len(frames))), 'time_unit': 'frames',
        'query': STATE_QUERY, 'answer': state_answer(activity, transition),
        'source_frame_indices': indices, 'video_id': video, 'track_id': person,
        'sampling_group': f'state:{activity}:{transition}', 'eval_group': 'state',
        'label_source': 'jaad_human_activity', 'state_task': True,
        'augmentation_safe_flip': True,
        'explanation_source': 'activity_template', 'action_source': 'observe_only_task_contract',
        'frames_reused': reused}


def export_masks(root, output):
    import numpy as np
    from PIL import Image
    from pycocotools.coco import COCO
    coco = COCO(str(root / 'source/instances.json'))
    person_ids = coco.getCatIds(catNms=['person'])
    if len(person_ids) != 1:
        raise ValueError('Expected exactly one COCO person category')
    result, assigned = {}, {}
    (output / 'masks').mkdir()
    for split in ('train', 'val'):
        unique = {}
        for row in map(json.loads, (root / f'{split}.jsonl').read_text().splitlines()):
            image_id = int(row['group_id'].removeprefix('coco_image_'))
            if image_id in assigned and assigned[image_id] != split:
                raise ValueError('COCO image leakage across splits')
            assigned[image_id] = split
            unique[image_id] = (root / row['frames'][0]).resolve()
        records = []
        for image_id, image in sorted(unique.items()):
            with Image.open(image) as handle:
                width, height = handle.size
            meta = coco.imgs[image_id]
            if (width, height) != (meta['width'], meta['height']):
                raise ValueError('COCO annotation/image dimensions differ')
            anns = coco.loadAnns(coco.getAnnIds(imgIds=[image_id], catIds=person_ids))
            mask = np.zeros((height, width), dtype=np.uint8)
            for ann in anns:
                mask |= coco.annToMask(ann).astype(np.uint8)
            destination = output / 'masks' / f'coco_{image_id}.png'
            Image.fromarray(mask * 255).save(destination)
            positive = bool(mask.any())
            records.append({'id': f'person_coco_{image_id}', 'group_id': f'coco_image_{image_id}',
                'frames': [str(image)], 'frame_indices': [0], 'time_unit': 'frames',
                'query': 'the people', 'mask': str(destination.resolve()), 'mask_encoding': 'binary_255',
                'no_target': not positive, 'label_source': 'coco_human_person_union',
                'augmentation_safe_flip': True,
                'annotation_ids': [a['id'] for a in anns], 'eval_group': 'segmentation',
                'sampling_group': 'mask:positive' if positive else 'mask:negative'})
        result[split] = records
    return result


def export_states(jaad, mixed, output, per_video, seed):
    import cv2
    rng = random.Random(seed)
    official = read_splits(jaad / 'split_ids/all_videos')
    result, sources = {}, []
    for split in ('train', 'val'):
        originals = [row for row in map(json.loads, (mixed / f'{split}.jsonl').read_text().splitlines())
                     if row['mix_group'] == 'jaad']
        by_video = defaultdict(list)
        for row in originals:
            if row['video_id'] not in official[split]:
                raise ValueError('Input disagrees with official JAAD split')
            by_video[row['video_id']].append(row)
        records = []
        for video in official[split]:
            xml = jaad / 'annotations' / f'{video}.xml'
            width, height, tracks = read_tracks(xml)
            used = set()
            for row in by_video[video]:
                indices, person = row['source_frame_indices'], row['track_id']
                annotation = tracks[person]
                record = state_record(video, person, indices, row['frames'], annotation, True)
                if record is not None:
                    records.append(record)
                    used.add((person, indices[-1]))
            path = jaad / 'JAAD_clips' / f'{video}.mp4'
            reader = cv2.VideoCapture(str(path))
            fps = reader.get(cv2.CAP_PROP_FPS)
            if not reader.isOpened() or fps <= 0:
                reader.release()
                raise ValueError(f'Cannot read {path}')
            candidates = defaultdict(list)
            for person, rows in sorted(tracks.items()):
                first, last = min(rows), max(rows)
                for now in range(first + round(3.5 * fps), last + 1, max(1, round(.5 * fps))):
                    if (person, now) in used:
                        continue
                    indices = [now - round((7 - j) * .5 * fps) for j in range(8)]
                    if any(i not in rows or rows[i]['attributes'].get('occlusion') == 'full'
                           or rows[i]['box'][3] - rows[i]['box'][1] < 64 for i in indices):
                        continue
                    target = activity_transition([rows[i]['attributes'].get('action') for i in indices])
                    if target is not None:
                        candidates[target].append((person, indices))
            # Spread the bounded export across activity/transition classes.
            for pool in candidates.values():
                rng.shuffle(pool)
            selected = []
            keys = sorted(candidates)
            while keys and len(selected) < per_video:
                for key in keys[:]:
                    if len(selected) >= per_video:
                        break
                    selected.append(candidates[key].pop())
                    if not candidates[key]:
                        keys.remove(key)
            wanted = {i for _, indices in selected for i in indices}
            decoded = {}
            # Decode once in order, avoiding thousands of random video seeks.
            if wanted:
                for index in range(max(wanted) + 1):
                    ok, frame = reader.read()
                    if not ok:
                        reader.release()
                        raise RuntimeError(f'Decode failed: {path}, frame {index}')
                    if index in wanted:
                        decoded[index] = cv2.resize(frame, (640, 360))
            reader.release()
            for person, indices in selected:
                destination = output / 'frames' / f'{video}_{person}_{indices[-1]:06d}'
                destination.mkdir(parents=True)
                paths = []
                for j, index in enumerate(indices):
                    frame = decoded[index].copy()
                    box = tracks[person][index]['box']
                    x1, y1, x2, y2 = [round(v * (640 / width if k % 2 == 0 else 360 / height))
                                      for k, v in enumerate(box)]
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 3)
                    image = destination / f'{j:02d}.jpg'
                    if not cv2.imwrite(str(image), frame, [cv2.IMWRITE_JPEG_QUALITY, 85]):
                        raise RuntimeError(f'Cannot write {image}')
                    paths.append(str(image.resolve()))
                records.append(state_record(video, person, indices, paths, tracks[person]))
            sources.append({'video': video, 'split': split, 'new_clips': len(selected),
                'annotation_sha256': hashlib.sha256(xml.read_bytes()).hexdigest()})
            print(json.dumps({'split': split, 'video': video, 'new_clips': len(selected)}), flush=True)
        result[split] = records
    return result, sources


def prepare_review(mixed, output):
    pending = []
    for split in ('train', 'val'):
        for row in map(json.loads, (mixed / f'{split}.jsonl').read_text().splitlines()):
            if row['mix_group'] != 'store':
                continue
            pending.append({'id': 'state_' + row['id'], 'group_id': row['group_id'],
                'frames': row['frames'], 'frame_indices': row['frame_indices'],
                'time_unit': 'frames', 'query': STATE_QUERY, 'split': split})
    write_jsonl(output / 'store_pending.jsonl', pending)
    cards = []
    for row in pending:
        images = ''.join(f'<img loading="lazy" width="240" src="{html.escape(os.path.relpath(p, output))}">' for p in row['frames'])
        cards.append(f'<article><h3>{html.escape(row["id"])} ({row["split"]})</h3>{images}</article>')
    (output / 'store_review.html').write_text('<!doctype html><meta charset="utf-8">'
        '<h1>Retail state review — historical frames ending at NOW</h1>'
        '<p>Inspect the green target across all frames. Fill store_review.csv; pending rows are excluded.</p>'
        + ''.join(cards))
    with (output / 'store_review.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['id', 'split', 'last_frame', 'current_activity',
            'observed_transition', 'explanation', 'review_status', 'reviewer', 'mask_path'])
        writer.writeheader()
        for row in pending:
            writer.writerow({'id': row['id'], 'split': row['split'], 'last_frame': row['frames'][-1],
                             'review_status': 'pending'})
    return len(pending)


def finalize(output, masks, states, sources, review_count):
    seen_groups, seen_frames = set(), set()
    report = {'counts': {}, 'state_counts': {}, 'new_clips': {}, 'store_pending': review_count,
              'test_loaded': False, 'mask_images_reused': True,
              'limitations': 'COCO images reused with person-specific supervision. JAAD human activity, '
              'template explanations, no retail activity or safety ground truth. Static COCO masks do not teach temporal segmentation.'}
    for split in ('train', 'val'):
        rows = masks[split] + states[split]
        groups = {r['group_id'] for r in rows}
        frames = {f for r in rows for f in r['frames']}
        if groups & seen_groups or frames & seen_frames or len({r['id'] for r in rows}) != len(rows):
            raise ValueError('Duplicate IDs or train/val source leakage')
        if not masks[split] or not states[split]:
            raise ValueError('Every split needs both tasks')
        seen_groups.update(groups)
        seen_frames.update(frames)
        for name, selected in ((split, rows), (f'segmentation_{split}', masks[split]),
                               (f'state_{split}', states[split])):
            write_jsonl(output / f'{name}.jsonl', selected)
        report['counts'][split] = dict(Counter(r['sampling_group'] for r in rows))
        report['state_counts'][split] = len(states[split])
        report['new_clips'][split] = sum(not r['frames_reused'] for r in states[split])
    groups = set(r['sampling_group'] for r in states['train'])
    weights = {key: .5 / len(groups) for key in groups}
    mask_groups = set(r['sampling_group'] for r in masks['train'])
    weights.update({key: .5 / len(mask_groups) for key in mask_groups})
    (output / 'sampling_weights.json').write_text(json.dumps(weights, indent=2) + '\n')
    (output / 'state_query.txt').write_text(STATE_QUERY + '\n')
    write_jsonl(output / 'sources.jsonl', sources)
    (output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mask-root', type=Path, required=True)
    parser.add_argument('--mixed-root', type=Path, required=True)
    parser.add_argument('--jaad-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--per-video', type=int, default=24)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args(argv)
    if args.output.exists() or args.per_video < 1:
        parser.error('Choose a new output directory and positive per-video limit')
    args.output.mkdir(parents=True)
    masks = export_masks(args.mask_root, args.output)
    states, sources = export_states(args.jaad_root, args.mixed_root, args.output, args.per_video, args.seed)
    report = finalize(args.output, masks, states, sources, prepare_review(args.mixed_root, args.output))
    (args.output / 'READY').write_text('Dataset export completed.\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
