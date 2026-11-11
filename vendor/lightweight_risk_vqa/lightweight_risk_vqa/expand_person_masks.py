"""Download additional COCO images with human person masks, preserving official image splits."""
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import random
import shutil

from .prepare_person_state import write_jsonl


def select_images(refs, coco, excluded, split, count, seed):
    memberships = defaultdict(set)
    for ref in refs:
        memberships[ref['image_id']].add(ref['split'])
    pools = {True: [], False: []}
    person = set(coco.getImgIds(catIds=coco.getCatIds(catNms=['person'])))
    for image_id, splits in sorted(memberships.items()):
        if splits == {split} and image_id not in excluded and image_id in coco.imgs:
            pools[image_id in person].append(image_id)
    rng = random.Random(seed)
    for pool in pools.values():
        rng.shuffle(pool)
    positive = round(count * .8)
    negative = count - positive
    if len(pools[True]) < positive or len(pools[False]) < negative:
        raise ValueError('Not enough disjoint official images for requested 80/20 export')
    return pools[True][:positive] + pools[False][:negative]


def main(argv=None):
    import numpy as np
    from PIL import Image
    from pycocotools.coco import COCO
    from light_foundation_vlm.download_data import download, sha256
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('dataset', 'coco-root', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--additional-train', type=int, default=2000)
    parser.add_argument('--additional-val', type=int, default=200)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--resume', action='store_true', help='Resume an interrupted, unpublished export')
    args = parser.parse_args(argv)
    if (args.output.exists() and not args.resume) or not (args.dataset / 'READY').exists() or min(
            args.additional_train, args.additional_val, args.workers) < 1:
        parser.error('Require complete base, new output, positive counts/workers')
    coco = COCO(str(args.coco_root / 'source/instances.json'))
    refs = json.loads((args.coco_root / 'source/grefs(unc).json').read_text())
    base = {s: list(map(json.loads, (args.dataset / f'{s}.jsonl').read_text().splitlines()))
            for s in ('train', 'val')}
    excluded = {int(r['group_id'].removeprefix('coco_image_')) for rows in base.values() for r in rows
                if r['group_id'].startswith('coco_image_')}
    selected = {s: select_images(refs, coco, excluded, s, count, args.seed)
                for s, count in (('train', args.additional_train), ('val', args.additional_val))}
    if set(selected['train']) & set(selected['val']):
        raise ValueError('Cross-split image leakage')
    plan = {'dataset': str(args.dataset.resolve()), 'coco_root': str(args.coco_root.resolve()),
            'selected': selected, 'seed': args.seed}
    if args.output.exists():
        if (args.output / 'READY').exists():
            raise ValueError('Completed datasets are immutable; choose a new version')
        plan_path = args.output / 'export_plan.json'
        if plan_path.exists():
            if json.loads(plan_path.read_text()) != plan:
                raise ValueError('Resume plan differs from original export')
        elif not all((args.output / f'{s}.jsonl').read_bytes() ==
                     (args.dataset / f'{s}.jsonl').read_bytes() for s in ('train', 'val')):
            raise ValueError('Cannot identify an unpublished base for resume')
    else:
        args.output.mkdir(parents=True)
    for file in args.dataset.iterdir():
        if file.is_file() and file.name != 'READY':
            shutil.copy2(file, args.output / file.name)
    (args.output / 'export_plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    (args.output / 'images').mkdir(exist_ok=True)
    (args.output / 'masks').mkdir(exist_ok=True)
    person_ids = coco.getCatIds(catNms=['person'])

    def export(item):
        split, image_id = item
        meta = coco.imgs[image_id]
        image = args.output / 'images' / f'{image_id:012d}.jpg'
        url = f'https://s3.amazonaws.com/images.cocodataset.org/train2014/COCO_train2014_{image_id:012d}.jpg'
        download(url, image)
        with Image.open(image) as handle:
            if handle.size != (meta['width'], meta['height']):
                raise ValueError('COCO image dimensions disagree with annotation')
            handle.verify()
        anns = coco.loadAnns(coco.getAnnIds(imgIds=[image_id], catIds=person_ids))
        mask = np.zeros((meta['height'], meta['width']), dtype=np.uint8)
        for ann in anns:
            mask |= coco.annToMask(ann).astype(np.uint8)
        mask_path = args.output / 'masks' / f'{image_id:012d}.png'
        Image.fromarray(mask * 255).save(mask_path)
        return split, {'id': f'person_coco_{image_id}', 'group_id': f'coco_image_{image_id}',
            'frames': [str(image.resolve())], 'frame_indices': [0], 'time_unit': 'frames',
            'query': 'the people', 'mask': str(mask_path.resolve()), 'mask_encoding': 'binary_255',
            'no_target': not bool(mask.any()), 'label_source': 'coco_human_person_union',
            'annotation_ids': [a['id'] for a in anns], 'eval_group': 'segmentation',
            'sampling_group': 'mask:positive' if mask.any() else 'mask:negative',
            'augmentation_safe_flip': True, 'source_split': split,
            'image_url': url, 'image_sha256': sha256(image)}

    additions = defaultdict(list)
    jobs = [(s, i) for s, ids in selected.items() for i in ids]
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        for split, row in executor.map(export, jobs):
            additions[split].append(row)
    for split, rows in base.items():
        rows += additions[split]
        write_jsonl(args.output / f'{split}.jsonl', rows)
        write_jsonl(args.output / f'segmentation_{split}.jsonl', [r for r in rows if r.get('mask')])
    report = {'base_dataset': str(args.dataset.resolve()),
              'additional_images': {s: len(rows) for s, rows in additions.items()},
              'official_split': True, 'excluded_existing_coco_images': len(excluded),
              'source_instances_sha256': sha256(args.coco_root / 'source/instances.json'),
              'source_refs_sha256': sha256(args.coco_root / 'source/grefs(unc).json'),
              'test_images_loaded': False, 'sampler_unchanged': True,
              'validation_policy': 'Expanded validation is a NEW protocol; compare all models on these same IDs.'}
    (args.output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    (args.output / 'READY').write_text('Expanded real-person dataset complete.\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
