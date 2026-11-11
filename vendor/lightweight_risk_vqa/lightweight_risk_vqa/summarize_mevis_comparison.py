"""Validate and summarize a same-init MeViS addition experiment."""
import argparse
import json
from pathlib import Path

from .summarize_person_state import summarize as summarize_states


def read_json(path):
    return json.loads(Path(path).read_text())


def mask_result(root):
    summary = read_json(root / 'summary.json')
    rows = list(map(json.loads, (root / 'predictions.jsonl').read_text().splitlines()))
    ids = [row['id'] for row in rows]
    if len(set(ids)) != len(ids) or len(ids) != summary['samples']:
        raise ValueError('Incomplete or duplicate mask predictions')
    negatives = [row['predicted_area_fraction'] for row in rows if row['negative_any_pixel_fp'] is not None]
    return summary, ids, (sum(negatives) / len(negatives) if negatives else None)


def summarize(experiment, results):
    arms = ('without_mevis', 'with_mevis')
    args = {arm: read_json(experiment / arm / 'training_args.json') for arm in arms}
    fixed = ('init', 'seed', 'epochs', 'epoch_samples', 'batch_size', 'lr', 'language_lr',
             'language_last_layers', 'freeze_encoder', 'freeze_memory', 'stage', 'augment', 'selection_groups')
    if any(args[arms[0]][key] != args[arms[1]][key] for key in fixed):
        raise ValueError('The two training arms have unmatched settings')
    common = Path(args['without_mevis']['train_jsonl']).parent
    states = {history: summarize_states(common / 'state_val.jsonl',
        {arm: results / arm / f'state.{history}.jsonl' for arm in arms})
        for history in ('full', 'last', 'repeat')}
    report = {'matched_settings': {key: args[arms[0]][key] for key in fixed}, 'models': {},
              'state': states, 'latency_comparison_valid': False,
              'latency_note': 'No controlled end-to-end latency protocol supplied; this report compares quality.'}
    for task in ('masks', 'mevis_full', 'mevis_last', 'mevis_repeat'):
        measurements = {arm: mask_result(results / arm / task) for arm in arms}
        a, b = [measurements[arm] for arm in arms]
        if (a[0]['manifest'] != b[0]['manifest'] or a[0]['threshold'] != b[0]['threshold']
                or a[0]['evaluation_size'] != b[0]['evaluation_size'] or set(a[1]) != set(b[1])):
            raise ValueError(f'Mask evaluation protocols differ: {task}')
        for arm, (summary, _, area) in measurements.items():
            model = report['models'].setdefault(arm, {})
            model[task] = {key: summary[key] for key in ('samples', 'positive_samples', 'negative_samples',
                'positive_mask_miou', 'negative_image_any_pixel_fp_rate', 'checkpoint_epoch')}
            model[task]['negative_mean_predicted_area_fraction'] = area
    for arm in arms:
        report['models'][arm]['checkpoint'] = str(experiment / arm / 'best')
    report['differences_with_minus_without'] = {
        'coco_person_miou': report['models']['with_mevis']['masks']['positive_mask_miou'] -
                            report['models']['without_mevis']['masks']['positive_mask_miou'],
        'mevis_miou': report['models']['with_mevis']['mevis_full']['positive_mask_miou'] -
                      report['models']['without_mevis']['mevis_full']['positive_mask_miou'],
        'activity_macro_f1': states['full']['with_mevis']['activity']['macro_f1'] -
                             states['full']['without_mevis']['activity']['macro_f1'],
        'transition_macro_f1': states['full']['with_mevis']['transition']['macro_f1'] -
                               states['full']['without_mevis']['transition']['macro_f1']}
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('experiment', 'results', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error('Choose a new output report')
    report = summarize(args.experiment, args.results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report['differences_with_minus_without'], indent=2))


if __name__ == '__main__':
    main()
