"""Human COCO grounding tasks, causal temporal tasks and raw retail review clips."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random

COUNT_QUERY = 'How many people are visible in this image? Return only JSON with person_count as an integer and person_present as true or false.'
CAPTION_QUERY = 'Describe the visible scene in one short sentence.'
PRESENCE_QUERY = 'Are any people visible in this image? Answer only yes or no.'


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def write_rows(path, rows):
    Path(path).write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows))


def check_groups(splits):
    seen_groups, seen_frames = set(), set()
    for split, rows in splits.items():
        groups = {r['group_id'] for r in rows}
        frames = {p for r in rows for p in r['frames']}
        if groups & seen_groups or frames & seen_frames:
            raise ValueError(f'Source leakage in {split}')
        seen_groups |= groups
        seen_frames |= frames


def prepare_coco(base, instances, captions, output, train_limit=1600, val_limit=160):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    annotations = json.loads(Path(instances).read_text())
    counts, crowd = Counter(), set()
    for ann in annotations['annotations']:
        if ann['category_id'] == 1:
            counts[ann['image_id']] += 1
            if ann.get('iscrowd'):
                crowd.add(ann['image_id'])
    caption_map = defaultdict(list)
    for path in captions:
        for ann in json.loads(Path(path).read_text())['annotations']:
            caption_map[ann['image_id']].append((ann['id'], ann['caption'].strip()))
    splits = {}
    source_files = [Path(instances), *map(Path, captions)]
    for split, limit in (('train', train_limit), ('val', val_limit)):
        buckets = defaultdict(list)
        for source in read_rows(Path(base) / f'segmentation_{split}.jsonl'):
            image_id = int(Path(source['frames'][0]).stem)
            if image_id in crowd or image_id not in caption_map:
                continue
            buckets[min(counts[image_id], 3)].append((image_id, source))
        rng = random.Random(42)
        for values in buckets.values():
            rng.shuffle(values)
        selected = []
        while len(selected) < limit and any(buckets.values()):
            for key in sorted(buckets):
                if buckets[key] and len(selected) < limit:
                    selected.append(buckets[key].pop())
        rows = []
        for image_id, source in selected:
            n = counts[image_id]
            common = {'group_id': source['group_id'], 'frames': source['frames'],
                      'time_unit': 'frames', 'frame_indices': [0], 'coco_image_id': image_id,
                      'person_count': n, 'label_source': 'coco_human_annotations',
                      'human_reviewed': True, 'augmentation_safe_flip': False}
            reference_captions = [text for _, text in sorted(caption_map[image_id])]
            variants = [
                ('count', COUNT_QUERY, json.dumps({'person_count': n, 'person_present': n > 0}, separators=(',', ':'))),
                ('presence', PRESENCE_QUERY, 'yes' if n else 'no'),
                ('caption', CAPTION_QUERY, reference_captions[0]),
            ]
            for task, query, answer in variants:
                rows.append({**common, 'id': f'ground_{image_id}_{task}', 'query': query,
                             'answer': answer, 'task': task, 'eval_group': task,
                             'sampling_group': f'{task}:{min(n,3)}',
                             'reference_captions': reference_captions if task == 'caption' else []})
        splits[split] = rows
    check_groups(splits)
    output.mkdir(parents=True)
    for split, rows in splits.items():
        write_rows(output / f'{split}.jsonl', rows)
        for task in ('count', 'presence', 'caption'):
            write_rows(output / f'{task}_{split}.jsonl', [r for r in rows if r['task'] == task])
    report = {'counts': {s: dict(Counter(r['task'] for r in rows)) for s, rows in splits.items()},
              'count_distribution': {s: dict(Counter(r['person_count'] for r in rows if r['task']=='count')) for s, rows in splits.items()},
              'source_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files},
              'note': 'Official human captions/counts; no crowd scenes. Existing image split retained; no new independent images implied.'}
    (output / 'summary.json').write_text(json.dumps(report, indent=2))
    (output / 'READY').write_text('Human COCO grounding tasks ready.\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--instances', type=Path, required=True)
    parser.add_argument('--captions', type=Path, nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--train-limit', type=int, default=1600)
    parser.add_argument('--val-limit', type=int, default=160)
    args = parser.parse_args()
    print(json.dumps(prepare_coco(args.base,args.instances,args.captions,args.output,args.train_limit,args.val_limit),indent=2))


if __name__ == '__main__':
    main()
