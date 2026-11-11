"""Import explicitly accepted retail state/mask reviews into a NEW dataset version."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import shutil

from .person_state import state_answer
from .prepare_person_state import write_jsonl


def apply(dataset, reviews, output):
    if output.exists() or not (dataset / 'READY').is_file():
        raise ValueError('Require complete source dataset and a new output directory')
    pending = {r['id']: r for r in map(json.loads, (dataset / 'store_pending.jsonl').read_text().splitlines())}
    accepted, seen = [], set()
    for review in csv.DictReader(reviews.open()):
        if review['id'] in seen:
            raise ValueError('Duplicate review ID')
        seen.add(review['id'])
        if review['id'] not in pending:
            raise ValueError('Unknown review ID')
        if review['review_status'] != 'accepted':
            continue
        if not review['reviewer'].strip() or not review['explanation'].strip():
            raise ValueError('Accepted labels need a reviewer and explanation')
        row = dict(pending[review['id']])
        if review['split'] != row['split']:
            raise ValueError('Cannot move source recording between splits')
        row.update(answer=state_answer(review['current_activity'], review['observed_transition'],
                                      review['explanation']),
                   state_task=True, eval_group='store_state',
                   sampling_group=f"store:{review['current_activity']}",
                   label_source='human_reviewed_retail_state', reviewer=review['reviewer'])
        if review.get('mask_path'):
            from PIL import Image
            import numpy as np
            mask = (reviews.parent / review['mask_path']).resolve()
            with Image.open(mask) as image, Image.open(row['frames'][-1]) as frame:
                if image.size != frame.size or not np.isin(np.asarray(image.convert('L')), [0, 255]).all():
                    raise ValueError('Reviewed mask must match final frame and contain only 0/255')
            row.update(mask=str(mask), mask_encoding='binary_255', mask_reviewed=True)
        accepted.append(row)
    if not accepted:
        raise ValueError('No accepted reviews; pending teacher suggestions are not training labels')
    training = [r for r in accepted if r['split'] == 'train']
    if not training:
        raise ValueError('At least one accepted training review is required')
    # Preserve original absolute asset paths; do not copy thousands of source frames.
    output.mkdir(parents=True)
    for file in dataset.iterdir():
        if file.is_file() and file.name != 'READY':
            shutil.copy2(file, output / file.name)
    all_rows = {}
    for split in ('train', 'val'):
        rows = list(map(json.loads, (dataset / f'{split}.jsonl').read_text().splitlines()))
        rows += [r for r in accepted if r['split'] == split]
        if len({r['id'] for r in rows}) != len(rows):
            raise ValueError('Reviewed state ID already exists')
        all_rows[split] = rows
    if ({r['group_id'] for r in all_rows['train']} & {r['group_id'] for r in all_rows['val']}
        or {f for r in all_rows['train'] for f in r['frames']} & {f for r in all_rows['val'] for f in r['frames']}):
        raise ValueError('Train/val leakage')
    for split, rows in all_rows.items():
        write_jsonl(output / f'{split}.jsonl', rows)
        write_jsonl(output / f'state_{split}.jsonl', [r for r in rows if r.get('state_task')])
        write_jsonl(output / f'segmentation_{split}.jsonl', [r for r in rows if r.get('mask')])
        write_jsonl(output / f'store_state_{split}.jsonl', [r for r in rows if r['eval_group'] == 'store_state'])
    weights = json.loads((dataset / 'sampling_weights.json').read_text())
    weights = {k: .8 * v for k, v in weights.items()}
    keys = {r['sampling_group'] for r in training}
    weights.update({k: .2 / len(keys) for k in keys})
    (output / 'sampling_weights.json').write_text(json.dumps(weights, indent=2) + '\n')
    summary = {'base_dataset': str(dataset.resolve()), 'accepted_reviews': len(accepted),
               'counts': {s: dict(Counter(r['sampling_group'] for r in rows)) for s, rows in all_rows.items()}}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    (output / 'READY').write_text('Review import completed.\n')
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--reviews', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(apply(args.dataset, args.reviews, args.output), indent=2))


if __name__ == '__main__':
    main()
