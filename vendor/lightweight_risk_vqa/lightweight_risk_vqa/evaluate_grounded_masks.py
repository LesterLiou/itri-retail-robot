"""Evaluate real-mask manifests and save image/ground-truth/prediction previews."""
import argparse
from html import escape
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
import torch


def mask_evaluation_records(records):
    if any(not row.get('mask') for row in records):
        raise ValueError('Mask evaluation requires a target mask for every record')
    # Reviewed paired state/mask examples are valid, but answers must never be inputs.
    return [{key: value for key, value in row.items() if key != 'answer'} for row in records]


def evaluate(checkpoint, manifest, output, device, previews=8, history='full'):
    from light_foundation_vlm.checkpoint import load_bundle
    from light_foundation_vlm.data import ClipDataset, collate
    from .temporal_vqa_infer import history_ablation

    model, tokenizer, bundle = load_bundle(checkpoint, torch.bfloat16 if device.startswith('cuda') else torch.float32)
    model.to(device).eval()
    data = ClipDataset(manifest, tokenizer, image_size=320, max_frames=8)
    data.records = mask_evaluation_records(data.records)
    if history not in {'full', 'last', 'repeat'}:
        raise ValueError('Unknown history mode')
    if history != 'full':
        data.records = [history_ablation(r, history == 'last', history == 'repeat') for r in data.records]
    if model.use_temporal and data.time_units != {model.config.time_unit}:
        raise ValueError('Manifest time units must match the temporal checkpoint')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    results, cards = [], []
    preview_count = {'positive': 0, 'negative': 0}
    # Reserve previews for both types; empty-target references must stay visible.
    quota = {'positive': previews - previews // 4, 'negative': previews // 4}
    for index, record in enumerate(data.records):
        batch = collate([data[index]], tokenizer.pad_token_id)
        target = batch.pop('mask_targets')[0, 0].numpy()
        batch.pop('labels')
        batch.pop('event_targets')
        with torch.inference_mode():
            prediction = model(**{k: v.to(device) for k, v in batch.items()})['mask_logits'][0, 0]
        if not torch.isfinite(prediction).all():
            raise ValueError(f'Nonfinite mask: {record["id"]}')
        predicted = prediction.gt(0).cpu().numpy()
        valid, truth = target != 255, target == 1
        predicted &= valid
        positive = bool(truth.any())
        iou = float((predicted & truth).sum() / max(1, (predicted | truth).sum())) if positive else None
        # Partly annotated empty masks are not reliable negative examples.
        negative_fp = bool(predicted.any()) if not positive and valid.all() else None
        row = {'id': record['id'], 'query': record['query'], 'positive_mask_iou': iou,
               'negative_any_pixel_fp': negative_fp, 'predicted_area_fraction': float(predicted.mean())}
        results.append(row)
        kind = 'positive' if positive else 'negative'
        if preview_count[kind] < quota[kind]:
            preview_count[kind] += 1
            image = Image.open(data.root / record['frames'][-1]).convert('RGB').resize((320, 320))
            panels = [image]
            for region, color in ((truth, [30, 220, 80]), (predicted, [255, 50, 50])):
                pixels = np.asarray(image).copy()
                pixels[region] = (pixels[region] * .55 + np.array(color) * .45).astype(np.uint8)
                panels.append(Image.fromarray(pixels))
            canvas = Image.new('RGB', (960, 345), 'white')
            draw = ImageDraw.Draw(canvas)
            for n, (title, panel) in enumerate(zip(('RGB', 'Ground truth', 'Prediction > 0.5'), panels)):
                canvas.paste(panel, (n * 320, 25))
                draw.text((n * 320 + 5, 5), title, fill='black')
            name = f'{index:04d}.jpg'
            canvas.save(output / name)
            cards.append(f'<article><p>{escape(record["id"])}: {escape(record["query"])}</p>'
                         f'<p>IoU={iou}; negative FP={negative_fp}</p><img src="{name}" width="960"></article>')
    positives = [r['positive_mask_iou'] for r in results if r['positive_mask_iou'] is not None]
    negatives = [r['negative_any_pixel_fp'] for r in results if r['negative_any_pixel_fp'] is not None]
    summary = {'checkpoint': str(checkpoint), 'manifest': str(manifest), 'samples': len(results),
               'positive_samples': len(positives), 'negative_samples': len(negatives),
               'positive_mask_miou': float(np.mean(positives)) if positives else None,
               'negative_image_any_pixel_fp_rate': float(np.mean(negatives)) if negatives else None,
               'threshold': .5, 'evaluation_size': 320, 'history_mode': history,
               'checkpoint_epoch': bundle.get('metadata', {}).get('metrics', {}).get('epoch')}
    (output / 'predictions.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in results))
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    (output / 'index.html').write_text('<!doctype html><meta charset="utf-8"><title>Mask evaluation</title>'
                                      '<h1>Real-mask validation</h1><pre>' + escape(json.dumps(summary, indent=2)) +
                                      '</pre>' + ''.join(cards))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--device', default='cuda:0')
    parser.add_argument('--previews', type=int, default=8)
    parser.add_argument('--history', choices=['full', 'last', 'repeat'], default='full')
    args = parser.parse_args(argv)
    if args.previews < 0:
        parser.error('previews must be nonnegative')
    print(json.dumps(evaluate(args.checkpoint, args.manifest, args.output, args.device,
                              args.previews, args.history), indent=2))


if __name__ == '__main__':
    main()
