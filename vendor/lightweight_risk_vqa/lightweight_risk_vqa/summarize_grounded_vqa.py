"""Report same-budget memory ablation, keeping mask/VQA metrics separate."""
import argparse
from collections import Counter
import json
from pathlib import Path

from .compare_vqa_runs import summarize


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--checkpoints', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--baseline-predictions', type=Path, required=True)
    args = parser.parse_args(argv)
    names = [f'{run}.{history}' for run in ('frozen_memory', 'train_memory') for history in ('full', 'last')]
    prediction_files = {name: args.results / f'{name}.val.jsonl' for name in names}
    prediction_files['baseline.full'] = args.baseline_predictions
    report = summarize(args.manifest, prediction_files)
    baseline = json.loads((args.checkpoints / 'frozen_memory/baseline.json').read_text())
    report['baseline.full']['segmentation'] = baseline['by_group']['segmentation']
    update_counts = {}
    for run in ('frozen_memory', 'train_memory'):
        metadata = json.loads((args.checkpoints / run / 'best/manifest.json').read_text())['metadata']
        epoch = metadata['metrics']['epoch']
        segmentation = metadata['metrics']['validation_by_group']['segmentation']
        for history in ('full', 'last'):
            report[f'{run}.{history}']['best_epoch'] = epoch
        report[f'{run}.full']['segmentation'] = segmentation
        counts = Counter()
        for line in (args.checkpoints / run / 'metrics.jsonl').read_text().splitlines():
            for key, value in json.loads(line).get('sampled_group_counts', {}).items():
                counts[key.split(':')[0]] += value
        update_counts[run] = dict(counts)
        report[f'{run}.full']['update_counts'] = dict(counts)
    selected = max(('frozen_memory.full', 'train_memory.full'),
                   key=lambda name: report[name]['val_selection_score'])
    decision = {'selected_validation_candidate': selected,
                'checkpoint': str(args.checkpoints / selected.split('.')[0] / 'best'),
                'criterion': 'equal-domain mean validation motion macro-F1, full-history only',
                'test_used_for_selection': False, 'automatically_deployed': False}
    (args.results / 'comparison.json').write_text(json.dumps(report, indent=2) + '\n')
    (args.results / 'selected_candidate.json').write_text(json.dumps(decision, indent=2) + '\n')
    lines = ['# Mask rehearsal + balanced VQA + ConvLSTM unfreezing', '',
             'Validation only; motion scores measure agreement with geometry weak labels.', '',
             '| Run | Epoch | JAAD acc | JAAD F1 | Store acc | Store F1 | Mask positive mIoU | Negative any-pixel FP |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    for name in ['baseline.full', *names]:
        item = report[name]
        j, s = item['by_domain']['jaad']['motion'], item['by_domain']['store']['motion']
        mask = item.get('segmentation')
        mask_text = f'{mask["positive_mask_miou"]:.1%} | {mask["negative_image_any_pixel_fp_rate"]:.1%}' if mask else '— | —'
        lines.append(f'| {name} | {item.get("best_epoch", "initial")} | {j["accuracy"]:.1%} | '
                     f'{j["macro_f1"]:.1%} | {s["accuracy"]:.1%} | {s["macro_f1"]:.1%} | {mask_text} |')
    lines += ['', f'Validation candidate for follow-up regression: `{selected}`.', '',
              'Mask metrics are from the selected checkpoint on the same real-mask validation split. '
              'Last-only mask ablation was not measured. Static-mask improvement does not prove temporal segmentation.', '',
              'Actual training draws (replayed samples, not new annotations): ' + json.dumps(update_counts) + '.', '',
              'Checkpoint epoch is selected using equal-domain VQA validation loss; candidate comparison uses '
              'free-generation motion macro-F1. Neither uses the fixed 0626 regression clip.']
    (args.results / 'comparison.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps(decision, indent=2))


if __name__ == '__main__':
    main()
