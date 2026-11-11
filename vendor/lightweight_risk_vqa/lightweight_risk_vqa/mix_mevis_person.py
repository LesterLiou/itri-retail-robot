"""Add a bounded MeViS temporal-mask task while preserving common selection groups."""
import argparse
import json
from pathlib import Path
import shutil

from .prepare_person_state import write_jsonl


def prepare(dataset, mevis, output, weight=.2):
    if output.exists() or not all((p / 'READY').exists() for p in (dataset, mevis)):
        raise ValueError('Require completed inputs and NEW output')
    if not 0 < weight < .5:
        raise ValueError('MeViS weight must be in (0,.5)')
    splits = {}
    additions = {}
    for split, original in (('train', 'train'), ('val', 'valid_u')):
        extra = list(map(json.loads, (mevis / f'{original}_person.jsonl').read_text().splitlines()))
        if not extra or any(r.get('answer') or not r.get('mask') or len(r['frames']) != 8 for r in extra):
            raise ValueError('MeViS must supply real temporal masks without generated VQA labels')
        base = list(map(json.loads, (dataset / f'{split}.jsonl').read_text().splitlines()))
        splits[split] = base + extra
        additions[split] = extra
        if len({r['id'] for r in splits[split]}) != len(splits[split]):
            raise ValueError('Duplicate example IDs')
    if ({r['group_id'] for r in splits['train']} & {r['group_id'] for r in splits['val']}
        or {f for r in splits['train'] for f in r['frames']} & {f for r in splits['val'] for f in r['frames']}):
        raise ValueError('Source video/frame leakage')
    output.mkdir(parents=True)
    for file in dataset.iterdir():
        if file.is_file() and file.name != 'READY':
            shutil.copy2(file, output / file.name)
    for split in ('train', 'val'):
        write_jsonl(output / f'{split}.jsonl', splits[split])
        write_jsonl(output / f'mevis_{split}.jsonl', additions[split])
    weights = json.loads((dataset / 'sampling_weights.json').read_text())
    weights = {k: v * (1 - weight) for k, v in weights.items()}
    weights['mevis'] = weight
    (output / 'sampling_weights.json').write_text(json.dumps(weights, indent=2) + '\n')
    report = {'base_dataset': str(dataset.resolve()), 'mevis': str(mevis.resolve()),
              'added_clips': {s: len(r) for s, r in additions.items()},
              'selection_groups': ['segmentation', 'state'],
              'comparison': 'Use same init, seed, update budget, COCO/state validation for baseline and MeViS arm; evaluate both on identical MeViS val IDs.'}
    (output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    (output / 'READY').write_text('MeViS mixed dataset complete.\n')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('dataset', 'mevis', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--weight', type=float, default=.2)
    args = parser.parse_args(argv)
    print(json.dumps(prepare(args.dataset, args.mevis, args.output, args.weight), indent=2))


if __name__ == '__main__':
    main()
