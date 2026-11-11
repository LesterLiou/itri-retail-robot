"""Compare complete state runs on exactly the same labeled validation clips."""
import argparse
import json
from pathlib import Path

from .person_state import ACTIVITIES, TRANSITIONS
from .vqa_metrics import classification_metrics


def summarize(manifest, predictions):
    expected = {r['id']: json.loads(r['answer']) for r in
                map(json.loads, Path(manifest).read_text().splitlines())}
    report = {}
    for name, path in predictions.items():
        rows = [r for r in map(json.loads, Path(path).read_text().splitlines()) if 'id' in r]
        if len(rows) != len(expected) or {r['id'] for r in rows} != set(expected):
            raise ValueError('Comparison requires complete, unique, identical validation IDs')
        for row in rows:
            if row.get('expected') != expected[row['id']]:
                raise ValueError('Predictions were scored against different labels')
        report[name] = {
            'samples': len(rows), 'schema_valid_rate': sum(r['schema_valid'] for r in rows) / len(rows),
            'activity': classification_metrics(rows, 'current_activity', ACTIVITIES),
            'transition': classification_metrics(rows, 'observed_transition', TRANSITIONS)}
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--prediction', action='append', required=True, help='NAME=JSONL, repeat per run')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    paths = {}
    for value in args.prediction:
        name, sep, path = value.partition('=')
        if not sep or not name or name in paths:
            parser.error('Require unique NAME=JSONL')
        paths[name] = path
    if args.output.exists():
        parser.error('Output exists; choose a new report')
    report = summarize(args.manifest, paths)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
