import importlib.util
import json
from pathlib import Path

import pytest

from lightweight_risk_vqa.prepare_mixed_vqa import prepare


def sampling_module():
    source = Path(__file__).resolve().parents[1] / 'light_foundation_vlm/sampling.py'
    spec = importlib.util.spec_from_file_location('standalone_domain_sampling', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_domain_probability_does_not_follow_dataset_size():
    rows = [{'mix_group': 'jaad'}] * 613 + [{'mix_group': 'store'}] * 54
    weights = sampling_module().domain_weights(rows, 'mix_group', {'jaad': .7, 'store': .3})
    assert sum(weights[:613]) == pytest.approx(.7)
    assert sum(weights[613:]) == pytest.approx(.3)
    control = sampling_module().domain_weights(rows, 'mix_group', {'jaad': 1, 'store': 0})
    assert all(value == 0 for value in control[613:])


def test_invalid_domain_weights_fail():
    module = sampling_module()
    for weights in ({'unknown': 1}, {'jaad': -1}, {'jaad': 0}, {'jaad': float('nan')}):
        with pytest.raises(ValueError):
            module.domain_weights([{'mix_group': 'jaad'}], 'mix_group', weights)


def make_source(tmp_path, domain, split, group=None):
    folder = tmp_path / domain
    folder.mkdir(exist_ok=True)
    images = []
    for index in range(8):
        image = folder / f'{split}_{index}.jpg'
        image.write_bytes(b'fixture')
        images.append(str(image))
    row = {'id': f'{domain}_{split}', 'group_id': group or f'{domain}_{split}',
           'frames': images, 'frame_indices': list(range(8)), 'time_unit': 'frames',
           'query': 'Predict next motion', 'answer': json.dumps({'motion': 'UNCERTAIN'})}
    path = folder / f'{split}.jsonl'
    path.write_text(json.dumps(row) + '\n')
    return path


def test_mixed_preparation_keeps_source_splits_and_unique_ids(tmp_path):
    for domain in ('jaad', 'store'):
        for split in ('train', 'val', 'test'):
            make_source(tmp_path, domain, split)
    output = tmp_path / 'mixed'
    report = prepare(tmp_path / 'jaad', tmp_path / 'store', tmp_path / 'store/test.jsonl', output)
    assert report['splits']['train']['jaad']['samples'] == 1
    rows = [json.loads(line) for line in (output / 'train.jsonl').read_text().splitlines()]
    assert len({row['id'] for row in rows}) == 2
    assert {row['mix_group'] for row in rows} == {'jaad', 'store'}
    assert len((output / 'store_val.jsonl').read_text().splitlines()) == 1


def test_mixed_preparation_rejects_recording_leakage(tmp_path):
    for domain in ('jaad', 'store'):
        for split in ('train', 'val', 'test'):
            make_source(tmp_path, domain, split, group='same_movie' if domain == 'store' else None)
    with pytest.raises(ValueError, match='leakage'):
        prepare(tmp_path / 'jaad', tmp_path / 'store', tmp_path / 'store/test.jsonl', tmp_path / 'mixed')
    assert not (tmp_path / 'mixed').exists()


def test_comparison_scores_domains_separately_and_rejects_partial_output(tmp_path):
    from lightweight_risk_vqa.compare_vqa_runs import summarize
    manifest = tmp_path / 'val.jsonl'
    manifest.write_text('\n'.join(json.dumps({'id': group, 'mix_group': group})
                                  for group in ('jaad', 'store')) + '\n')
    predictions = tmp_path / 'answers.jsonl'
    rows = [{'id': group, 'expected': {'motion': 'STATIONARY', 'action': 'CONTINUE'},
             'prediction': {'motion': 'STATIONARY', 'action': 'CONTINUE'}, 'schema_valid': True}
            for group in ('jaad', 'store')]
    predictions.write_text('\n'.join(map(json.dumps, rows)) + '\n')
    result = summarize(manifest, {'run': predictions})['run']
    assert result['val_selection_score'] == 1
    assert result['by_domain']['store']['samples'] == 1
    predictions.write_text(json.dumps(rows[0]) + '\n')
    with pytest.raises(ValueError, match='Incomplete'):
        summarize(manifest, {'run': predictions})
