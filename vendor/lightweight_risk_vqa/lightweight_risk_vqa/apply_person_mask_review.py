"""Import accepted SAM3 all-person masks, retaining real clip history and source split."""
import argparse
import csv
import json
from pathlib import Path
import shutil

from .prepare_person_state import write_jsonl


def apply(dataset, teacher, reviews, output):
    import numpy as np
    from PIL import Image
    if output.exists() or not (dataset / 'READY').exists() or not (teacher / 'READY').exists():
        raise ValueError('Require complete inputs and new output')
    candidates = {r['id']: r for r in map(json.loads, (teacher / 'candidates.jsonl').read_text().splitlines())}
    accepted, seen = [], set()
    for review in csv.DictReader(reviews.open()):
        if review['id'] in seen or review['id'] not in candidates:
            raise ValueError('Unknown or duplicate review ID')
        seen.add(review['id'])
        if review['review_status'] != 'accepted':
            continue
        row = dict(candidates[review['id']])
        if not review['reviewer'].strip() or review['split'] != row['split']:
            raise ValueError('Require reviewer and unchanged source split')
        mask = (reviews.parent / review['mask_path']).resolve()
        with Image.open(mask) as image, Image.open(row['frames'][-1]) as frame:
            if image.size != frame.size or not np.isin(np.asarray(image.convert('L')), [0, 255]).all():
                raise ValueError('Mask must match final frame and be binary 0/255')
        row.update(mask=str(mask), human_reviewed=True, reviewer=review['reviewer'],
                   label_source='sam3_person_union_human_reviewed')
        accepted.append(row)
    if not any(r['split'] == 'train' for r in accepted):
        raise ValueError('No accepted training masks; pending candidates cannot enter training')
    splits = {}
    for split in ('train', 'val'):
        rows = list(map(json.loads, (dataset / f'{split}.jsonl').read_text().splitlines()))
        rows += [r for r in accepted if r['split'] == split]
        if len({r['id'] for r in rows}) != len(rows):
            raise ValueError('Duplicate IDs')
        splits[split] = rows
    if ({r['group_id'] for r in splits['train']} & {r['group_id'] for r in splits['val']}
        or {f for r in splits['train'] for f in r['frames']} & {f for r in splits['val'] for f in r['frames']}):
        raise ValueError('Train/val recording or frame leakage')
    output.mkdir(parents=True)
    for file in dataset.iterdir():
        if file.is_file() and file.name != 'READY':
            shutil.copy2(file, output / file.name)
    for split, rows in splits.items():
        write_jsonl(output / f'{split}.jsonl', rows)
        write_jsonl(output / f'segmentation_{split}.jsonl', [r for r in rows if r.get('mask')])
    weights = json.loads((dataset / 'sampling_weights.json').read_text())
    weights = {k: v * .8 for k, v in weights.items()}
    weights['mask:teacher'] = .2
    (output / 'sampling_weights.json').write_text(json.dumps(weights, indent=2) + '\n')
    summary = {'base_dataset': str(dataset.resolve()), 'accepted_teacher_masks': len(accepted),
               'teacher_candidates': str(teacher.resolve())}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    (output / 'READY').write_text('Reviewed temporal person masks imported.\n')
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('dataset', 'teacher', 'reviews', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(apply(args.dataset, args.teacher, args.reviews, args.output), indent=2))


if __name__ == '__main__':
    main()
