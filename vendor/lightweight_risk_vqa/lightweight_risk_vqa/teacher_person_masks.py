"""Offline SAM3 person masks for retail clips; candidates require explicit human acceptance."""
import argparse
import csv
import html
import json
import os
from pathlib import Path

from .prepare_person_state import write_jsonl


def main(argv=None):
    import numpy as np
    from PIL import Image
    import torch
    from transformers import Sam3Model, Sam3Processor
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', default='facebook/sam3')
    parser.add_argument('--device', default='cuda:0')
    parser.add_argument('--threshold', type=float, default=.5)
    args = parser.parse_args(argv)
    if args.output.exists() or not 0 < args.threshold < 1:
        parser.error('Choose a new output directory and threshold in (0,1)')
    rows = list(map(json.loads, (args.dataset / 'store_pending.jsonl').read_text().splitlines()))
    dtype = torch.bfloat16 if args.device.startswith('cuda') else torch.float32
    # Use only the cached model; no implicit large/gated download.
    model = Sam3Model.from_pretrained(args.model, dtype=dtype, local_files_only=True).to(args.device).eval()
    processor = Sam3Processor.from_pretrained(args.model, local_files_only=True)
    args.output.mkdir(parents=True)
    (args.output / 'masks').mkdir()
    (args.output / 'overlays').mkdir()
    candidates, queue = [], []
    for row in rows:
        image = Image.open(row['frames'][-1]).convert('RGB')
        inputs = processor(images=image, text='person', return_tensors='pt').to(args.device)
        with torch.inference_mode(), torch.autocast(device_type='cuda', dtype=dtype,
                                                    enabled=args.device.startswith('cuda')):
            outputs = model(**inputs)
        result = processor.post_process_instance_segmentation(outputs, threshold=args.threshold,
            mask_threshold=.5, target_sizes=[(image.height, image.width)])[0]
        masks = result['masks'].detach().cpu().numpy().astype(bool)
        if masks.ndim == 4:
            masks = masks[:, 0]
        union = masks.any(axis=0) if len(masks) else np.zeros((image.height, image.width), dtype=bool)
        mask_path = args.output / 'masks' / (row['id'] + '.png')
        overlay_path = args.output / 'overlays' / (row['id'] + '.jpg')
        Image.fromarray(union.astype(np.uint8) * 255).save(mask_path)
        overlay = np.asarray(image).copy()
        overlay[union] = (.55 * overlay[union] + .45 * np.array([255, 40, 40])).astype(np.uint8)
        Image.fromarray(overlay).save(overlay_path)
        candidate = {**row, 'id': 'teacher_mask_' + row['id'], 'query': 'the people',
            'mask': str(mask_path.resolve()), 'mask_encoding': 'binary_255',
            'label_source': 'sam3_person_union_candidate', 'teacher_model': args.model,
            'teacher_revision': getattr(model.config, '_commit_hash', None),
            'teacher_threshold': args.threshold, 'human_reviewed': False,
            'eval_group': 'segmentation', 'sampling_group': 'mask:teacher',
            'augmentation_safe_flip': True}
        candidates.append(candidate)
        queue.append({'id': candidate['id'], 'split': row['split'], 'review_status': 'pending',
                      'reviewer': '', 'mask_path': str(mask_path.resolve()),
                      'overlay': str(overlay_path.resolve())})
        print(json.dumps({'id': row['id'], 'instances': len(masks), 'mask_fraction': float(union.mean())}), flush=True)
    write_jsonl(args.output / 'candidates.jsonl', candidates)
    with (args.output / 'review.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(queue[0]))
        writer.writeheader()
        writer.writerows(queue)
    (args.output / 'READY').write_text('Teacher candidates generated; NOT training ground truth.\n')
    (args.output / 'index.html').write_text('<!doctype html><meta charset="utf-8">'
        '<h1>SAM3 all-person mask candidates — review pending</h1>' + ''.join(
            f'<article><h3>{html.escape(r["id"])}</h3><img loading="lazy" width="640" '
            f'src="{html.escape(os.path.relpath(r["overlay"], args.output))}"></article>' for r in queue))


if __name__ == '__main__':
    main()
