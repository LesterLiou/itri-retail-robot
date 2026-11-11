"""Apply explicitly approved human CSV labels to JAAD manifests.

Pending and rejected rows are excluded. No automatic approval is performed.
"""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path

from .prepare_jaad_vqa import answer_for
from .temporal import LABELS


def apply_review(dataset_root, review_csv, output):
    root, output = Path(dataset_root), Path(output)
    if output.exists():
        raise FileExistsError(f'Output exists: {output}')
    with Path(review_csv).open(newline='') as stream:
        reviews = list(csv.DictReader(stream))
    by_id = {}
    for row in reviews:
        if row['id'] in by_id:
            raise ValueError(f'Duplicate review ID: {row["id"]}')
        if row['review_status'] not in ('pending', 'approved', 'rejected'):
            raise ValueError(f'Unsupported review status for {row["id"]}')
        if row['review_status'] == 'approved' and row['reviewed_motion'] not in LABELS:
            raise ValueError(f'Approved row needs reviewed_motion: {row["id"]}')
        by_id[row['id']] = row
    exported, known = {}, set()
    for split in ('train', 'val', 'test'):
        records = [json.loads(line) for line in
                   (root / 'manifests' / f'{split}.jsonl').read_text().splitlines() if line.strip()]
        selected = []
        for record in records:
            known.add(record['id'])
            review = by_id.get(record['id'])
            if not review or review['review_status'] != 'approved':
                continue
            if review['split'] != split:
                raise ValueError('Review must not change official split')
            for frame in record['frames']:
                if not Path(frame).is_file():
                    raise FileNotFoundError(frame)
            selected.append(dict(
                record, answer=answer_for(review['reviewed_motion']),
                label_source='jaad_human_reviewed_apparent_motion', human_reviewed=True,
                review_notes=review.get('notes', ''),
                original_weak_answer=record['answer']))
        exported[split] = selected
    if set(by_id) - known:
        raise ValueError('Review CSV contains IDs absent from this dataset')
    if not all(exported.values()):
        raise ValueError('Approve at least one sample in EACH split before exporting reviewed data')
    output.mkdir(parents=True)
    for split, records in exported.items():
        with (output / f'{split}.jsonl').open('x') as stream:
            for record in records:
                stream.write(json.dumps(record) + '\n')
    summary = {'source_dataset': str(root.resolve()), 'review_csv': str(Path(review_csv).resolve()),
               'reviewed_motion_only': True, 'action_and_explanation': 'templates; not human risk labels',
               'splits': {s: {'samples': len(rows), 'motion_counts': dict(Counter(
                   json.loads(r['answer'])['motion'] for r in rows))} for s, rows in exported.items()}}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-root', type=Path, required=True)
    parser.add_argument('--review-csv', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(apply_review(args.dataset_root, args.review_csv, args.output), indent=2))


if __name__ == '__main__':
    main()
