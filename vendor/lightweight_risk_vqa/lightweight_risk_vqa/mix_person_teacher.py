"""Opt-in weak SAM3 rehearsal: train split only; human validation remains unchanged."""
import argparse
import json
from pathlib import Path
import shutil

from .prepare_person_state import write_jsonl


def prepare(dataset, teacher, output, weight=.2):
    if output.exists() or not (dataset / 'READY').exists() or not (teacher / 'READY').exists():
        raise ValueError('Require completed inputs and new output')
    if not 0 < weight < .5:
        raise ValueError('Weak teacher weight must be in (0,.5)')
    candidates = list(map(json.loads, (teacher / 'candidates.jsonl').read_text().splitlines()))
    weak = [r for r in candidates if r['split'] == 'train']
    if not weak:
        raise ValueError('No teacher training clips')
    train = list(map(json.loads, (dataset / 'train.jsonl').read_text().splitlines())) + weak
    val = list(map(json.loads, (dataset / 'val.jsonl').read_text().splitlines()))
    if len({r['id'] for r in train}) != len(train):
        raise ValueError('Teacher masks already imported')
    if ({r['group_id'] for r in train} & {r['group_id'] for r in val}
        or {f for r in train for f in r['frames']} & {f for r in val for f in r['frames']}):
        raise ValueError('Train/val leakage')
    output.mkdir(parents=True)
    for file in dataset.iterdir():
        if file.is_file() and file.name != 'READY':
            shutil.copy2(file, output / file.name)
    write_jsonl(output / 'train.jsonl', train)
    write_jsonl(output / 'segmentation_train.jsonl', [r for r in train if r.get('mask')])
    weights = json.loads((dataset / 'sampling_weights.json').read_text())
    weights = {k: v * (1 - weight) for k, v in weights.items()}
    weights['mask:teacher'] = weight
    (output / 'sampling_weights.json').write_text(json.dumps(weights, indent=2) + '\n')
    report = {'base_dataset': str(dataset.resolve()), 'teacher_source': str(teacher.resolve()),
              'weak_train_mask_clips': len(weak), 'teacher_val_clips_excluded': len(candidates) - len(weak),
              'human_reviewed': False, 'teacher_sampling_weight': weight,
              'val_unchanged': True, 'warning': 'Explicit weak supervision, not ground truth or added retail activity labels.'}
    (output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    (output / 'READY').write_text('Weak-teacher training variant; validation unchanged.\n')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('dataset', 'teacher', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--train-weight', type=float, default=.2)
    args = parser.parse_args(argv)
    print(json.dumps(prepare(args.dataset, args.teacher, args.output, args.train_weight), indent=2))


if __name__ == '__main__':
    main()
