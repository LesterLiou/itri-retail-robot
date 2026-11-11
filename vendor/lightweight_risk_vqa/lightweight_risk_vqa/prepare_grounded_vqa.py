"""Mix real referring masks with causal VQA; balance source and motion separately."""
import argparse
from collections import Counter
import json
from pathlib import Path

MOTIONS = {'TOWARD_CAMERA', 'AWAY_FROM_CAMERA', 'LATERAL_ACROSS_VIEW', 'STATIONARY', 'UNCERTAIN'}


def read_rows(path, segmentation=False):
    path = Path(path).resolve()
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        row['source_manifest'] = str(path)
        for key in ('frames', 'mask'):
            if not row.get(key):
                continue
            values = row[key] if key == 'frames' else [row[key]]
            resolved = [(path.parent / value).resolve() for value in values]
            for value in resolved:
                if not value.is_file():
                    raise FileNotFoundError(value)
            row[key] = [str(value) for value in resolved] if key == 'frames' else str(resolved[0])
        if segmentation:
            if len(row['frames']) != 1 or not row.get('mask') or row.get('answer'):
                raise ValueError('Expected single-image masks without VQA answers')
            row.pop('timestamps_s', None)
            row.update(time_unit='frames', frame_indices=[0], eval_group='segmentation',
                       sampling_group='segmentation', supervision='human_mask')
        else:
            if row.get('time_unit') != 'frames' or row.get('mask'):
                raise ValueError('Expected frame-based VQA without masks')
            motion = json.loads(row['answer'])['motion']
            if row['mix_group'] not in ('jaad', 'store') or motion not in MOTIONS:
                raise ValueError('Unknown source or motion')
            row.update(eval_group=row['mix_group'],
                       sampling_group=f"{row['mix_group']}:{motion}", supervision='weak_vqa')
        rows.append(row)
    if not rows:
        raise ValueError(f'Empty source: {path}')
    return rows


def prepare(mixed_root, mask_root, output):
    mixed_root, mask_root, output = Path(mixed_root), Path(mask_root), Path(output)
    if output.exists():
        raise FileExistsError(output)
    splits = {split: read_rows(mixed_root / f'{split}.jsonl') +
              read_rows(mask_root / f'{split}.jsonl', segmentation=True)
              for split in ('train', 'val')}
    seen_ids, seen_groups, seen_frames = set(), set(), set()
    for split, rows in splits.items():
        ids = {r['id'] for r in rows}
        groups = {r['group_id'] for r in rows}
        frames = {p for r in rows for p in r['frames']}
        if len(ids) != len(rows) or seen_ids & ids or seen_groups & groups or seen_frames & frames:
            raise ValueError(f'Duplicate IDs or cross-split recording/image leakage in {split}')
        seen_ids.update(ids)
        seen_groups.update(groups)
        seen_frames.update(frames)
    # Each VQA domain gets 35%, split equally across its observed motion classes.
    # Rare weak labels are replayed, not counted as new independent annotations.
    weights = {'segmentation': .3}
    training_groups = {r['sampling_group'] for r in splits['train']}
    for domain in ('jaad', 'store'):
        keys = sorted(k for k in training_groups if k.startswith(domain + ':'))
        if not keys:
            raise ValueError(f'Missing training domain: {domain}')
        weights.update({key: .35 / len(keys) for key in keys})
    output.mkdir(parents=True)
    for split, rows in splits.items():
        variants = {split: rows}
        for group in ('segmentation', 'jaad', 'store'):
            variants[f'{group}_{split}'] = [r for r in rows if r['eval_group'] == group]
        for name, selected in variants.items():
            (output / f'{name}.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in selected))
    report = {
        'counts': {split: dict(Counter(r['sampling_group'] for r in rows))
                   for split, rows in splits.items()},
        'sampling_weights': weights,
        'task_contract': 'Segmentation: original referring expression + human pixel mask. '
                         'VQA: historical frames ending at NOW, future-derived weak label only in answer.',
        'time_contract': 'Single static mask example becomes one frame step [0]; no fake motion is created.',
        'test_policy': 'No test inputs loaded or used for sampling/checkpoint selection.',
    }
    (output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    (output / 'sampling_weights.json').write_text(json.dumps(weights) + '\n')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mixed-root', type=Path, required=True)
    parser.add_argument('--mask-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(prepare(args.mixed_root, args.mask_root, args.output), indent=2))


if __name__ == '__main__':
    main()
