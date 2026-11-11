"""Audit actual tensor changes against the declared trainable parameters."""
import argparse
from collections import defaultdict
import json
from pathlib import Path


def audit(initial, checkpoint):
    import torch
    torch.set_num_threads(min(torch.get_num_threads(), 4))
    before = torch.load(initial / 'model.pt', map_location='cpu', weights_only=True, mmap=True)
    after = torch.load(checkpoint / 'model.pt', map_location='cpu', weights_only=True, mmap=True)
    declared = json.loads((checkpoint.parent / 'trainable_parameters.json').read_text())
    trainable = set(declared['names'])
    # Release bundles merge adapter updates into plain base weights.
    trainable_export = {name.rsplit('.',1)[0]+'.weight' if name.endswith(('.lora_A','.lora_B')) else name
                        for name in trainable}
    if before.keys() != after.keys():
        args=json.loads((checkpoint.parent/'training_args.json').read_text())
        added=set(after)-set(before)
        if set(before)-set(after) or not args.get('attach_event_head') or added != {'event_head.weight','event_head.bias'}:
            raise ValueError('Unexpected model tensor key changes')
    groups = defaultdict(lambda: {'tensors': 0, 'changed_tensors': 0, 'changed_elements': 0})
    changed = []
    for name, old in before.items():
        new = after[name]
        if old.shape != new.shape:
            raise ValueError(f'Tensor shape changed: {name}')
        if name.startswith('language_model.model.layers.'):
            group = '.'.join(name.split('.')[:4])
        elif name.startswith('language_model.'):
            group = 'other_language_model'
        else:
            group = name.split('.')[0]
        stats = groups[group]
        stats['tensors'] += 1
        if not torch.equal(old, new):
            changed.append(name)
            stats['changed_tensors'] += 1
            stats['changed_elements'] += int((old != new).sum())
    return {'initial_bundle': str(initial), 'checkpoint': str(checkpoint),
            'trainable_parameters': declared['trainable_count'], 'total_parameters': declared['total_count'],
            'by_module': dict(groups), 'actual_changed_tensor_names': changed,
            'new_tensor_names': sorted(set(after)-set(before)),
            'unexpected_changed_frozen_tensors': sorted(set(changed) - trainable_export),
            'declared_but_unchanged_tensors': sorted(trainable_export - set(changed) - (set(after)-set(before)))}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('initial', 'checkpoint', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error('Choose a new audit output')
    report = audit(args.initial, args.checkpoint)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items()
                      if k not in {'actual_changed_tensor_names', 'declared_but_unchanged_tensors'}}, indent=2))


if __name__ == '__main__':
    main()
