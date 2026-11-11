"""Prepare JAAD/store domain-tagged manifests without duplicating sample IDs."""
import argparse
from collections import Counter
import json
from pathlib import Path


def read_manifest(path, domain):
    path = Path(path).resolve()
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get('time_unit') != 'frames' or len(row['frames']) != 8:
            raise ValueError('This experiment requires the existing 8-step frame-based contract')
        if row['frame_indices'] != list(range(8)):
            raise ValueError('Expected sampled step indices 0..7, not arbitrary source FPS indices')
        if not row.get('answer') or row.get('mask'):
            raise ValueError('Expected VQA-only rows, not segmentation records')
        frames = []
        for value in row['frames']:
            frame = Path(value)
            frame = frame if frame.is_absolute() else path.parent / frame
            if not frame.is_file():
                raise FileNotFoundError(frame)
            frames.append(str(frame.resolve()))
        rows.append(dict(row, frames=frames, mix_group=domain, source_manifest=str(path)))
    if not rows:
        raise ValueError(f'Empty manifest: {path}')
    return rows


def prepare(jaad_root, store_root, store_test, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(f'Output exists: {output}')
    jaad_root, store_root = Path(jaad_root), Path(store_root)
    groups = {
        'train': read_manifest(jaad_root / 'train.jsonl', 'jaad')
                 + read_manifest(store_root / 'train.jsonl', 'store'),
        'val': read_manifest(jaad_root / 'val.jsonl', 'jaad')
               + read_manifest(store_root / 'val.jsonl', 'store'),
        'test': read_manifest(jaad_root / 'test.jsonl', 'jaad')
                + read_manifest(store_test, 'store'),
    }
    seen_ids, seen_groups, seen_paths = set(), set(), set()
    report = {}
    for split, rows in groups.items():
        ids = {row['id'] for row in rows}
        recording_groups = {row['group_id'] for row in rows}
        paths = {value for row in rows for value in row['frames']}
        if len(ids) != len(rows):
            raise ValueError(f'Duplicate sample ID in {split}')
        if seen_ids & ids or seen_groups & recording_groups or seen_paths & paths:
            raise ValueError(f'Cross-split sample/group/frame leakage: {split}')
        seen_ids.update(ids)
        seen_groups.update(recording_groups)
        seen_paths.update(paths)
        report[split] = {domain: {
            'samples': sum(row['mix_group'] == domain for row in rows),
            'motion_counts': dict(Counter(json.loads(row['answer'])['motion'] for row in rows
                                           if row['mix_group'] == domain)),
        } for domain in ('jaad', 'store')}
    output.mkdir(parents=True)
    for split, rows in groups.items():
        variants = {split: rows}
        if split != 'train':
            variants.update({f'{domain}_{split}': [r for r in rows if r['mix_group'] == domain]
                             for domain in ('jaad', 'store')})
        for name, selected in variants.items():
            with (output / f'{name}.jsonl').open('x') as stream:
                for row in selected:
                    stream.write(json.dumps(row) + '\n')
    summary = {'splits': report, 'sampling_group_key': 'mix_group',
               'human_reviewed': False, 'new_human_annotations_created': False,
               'note': 'Weak-label domain replay experiment. An already inspected store test '
                       'is fixed regression, not a fresh blind test. '
                       'No duplicated IDs; replacement sampling happens only in trainer.'}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jaad-root', type=Path, required=True)
    parser.add_argument('--store-root', type=Path, required=True)
    parser.add_argument('--store-test', type=Path, required=True,
                        help='Independent store recording manifest; not used to choose sampling weights')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(prepare(args.jaad_root, args.store_root, args.store_test, args.output), indent=2))


if __name__ == '__main__':
    main()
