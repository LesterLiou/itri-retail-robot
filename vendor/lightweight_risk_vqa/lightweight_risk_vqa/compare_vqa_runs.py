"""Compare complete VQA runs by domain on the same validation manifest."""
import argparse
import json
from pathlib import Path

from .vqa_metrics import classification_metrics
from .temporal import LABELS


def summarize(manifest, prediction_files):
    records = {r['id']: r for r in map(json.loads, Path(manifest).read_text().splitlines())}
    results = {}
    for name, path in prediction_files.items():
        generated = [r for r in map(json.loads, Path(path).read_text().splitlines()) if 'id' in r]
        ids = [r['id'] for r in generated]
        if len(ids) != len(set(ids)) or set(ids) != set(records):
            raise ValueError(f'Incomplete/duplicate/mismatched validation predictions: {name}')
        domains = {}
        for domain in sorted({r['mix_group'] for r in records.values()}):
            rows = [row for row in generated if records[row['id']]['mix_group'] == domain]
            domains[domain] = {
                'samples': len(rows),
                'motion': classification_metrics(rows, 'motion', set(LABELS)),
                'action': classification_metrics(rows, 'action', {'STOP', 'DANGER_WARNING', 'CONTINUE', 'NO_RESPONSE'}),
                'schema_valid_rate': sum(row['schema_valid'] for row in rows) / len(rows),
            }
        results[name] = {'by_domain': domains, 'val_selection_score': sum(
            domain['motion']['macro_f1'] for domain in domains.values()) / len(domains)}
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--prediction', action='append', required=True,
                        help='NAME=FILE; repeat for each run, all evaluated on the same validation IDs')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    files = {}
    for value in args.prediction:
        name, separator, path = value.partition('=')
        if not separator or not name or not path or name in files:
            parser.error('Each prediction must be a unique NAME=FILE')
        files[name] = Path(path)
    report = summarize(args.manifest, files)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
