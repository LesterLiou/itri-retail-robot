import json
import os
from pathlib import Path
import subprocess
import shlex

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('mode', ['vqa-only', 'yolo-assisted'])
def test_robot_modes_keep_one_vqa_and_optional_detector(tmp_path, mode):
    fake = tmp_path / 'python3'
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n')
    fake.chmod(0o755)
    capture = tmp_path / 'args'
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'], CAPTURE=str(capture))
    subprocess.run(['bash', str(ROOT / 'scripts/run_vqa_ui.sh'), mode,
                    '--checkpoint', '/models/bundle', '--video', '/video/clip.mp4'], env=env, check=True)
    args = capture.read_text().splitlines()
    assert args[args.index('--inference-mode') + 1] == mode
    assert args[args.index('--vqa-backend') + 1] == 'embedded'
    assert ('--bbox-model' in args) == (mode == 'yolo-assisted')
    assert '--camera-policy-config' not in args
    assert '--query' not in args  # Python selects the robot query, never the state-only query.


@pytest.mark.parametrize('backend', ['embedded', 'worker'])
def test_both_ui_backends_keep_distinct_entrypoints(tmp_path, backend):
    fake = tmp_path / 'python3'
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n')
    fake.chmod(0o755)
    capture = tmp_path / 'args'
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'], CAPTURE=str(capture))
    subprocess.run(['bash', str(ROOT / 'scripts/run_vqa_ui.sh'), backend,
                    '--checkpoint', '/models/a bundle', '--video', '/video/a clip.mp4'],
                   env=env, check=True)
    args = capture.read_text().splitlines()
    assert args[:2] == ['-m', 'lightweight_risk_vqa.light_foundation_live_ui']
    assert args[args.index('--vqa-backend') + 1] == backend
    assert args[args.index('--checkpoint') + 1] == '/models/a bundle'
    assert ('motion, action, and explanation' in args[args.index('--query') + 1]) == (backend == 'embedded')


def test_training_dry_run_does_not_create_outputs_or_load_models(tmp_path):
    data, output = tmp_path / 'data with spaces', tmp_path / 'new experiment'
    data.mkdir()
    (data / 'sampling_weights.json').write_text(json.dumps({'segmentation': .3, 'jaad': .35, 'store': .35}))
    result = subprocess.run(['bash', str(ROOT / 'scripts/train_grounded_vqa.sh'),
                             str(data), '/no/model/download', str(output), '2', '240', '--dry-run'],
                            check=True, capture_output=True, text=True)
    assert not output.exists()
    assert result.stdout.count('light_foundation_vlm.train') == 2
    assert result.stdout.count('--freeze-memory') == 1
    assert result.stdout.count('lightweight_risk_vqa.temporal_vqa_infer') == 5


def test_person_state_dry_run_keeps_matched_selection_and_freezing(tmp_path):
    data, init, output = tmp_path / 'data', tmp_path / 'init', tmp_path / 'new run'
    data.mkdir()
    init.mkdir()
    (data / 'READY').touch()
    (init / 'manifest.json').write_text('{}')
    (data / 'sampling_weights.json').write_text('{"mask:positive":0.5,"state":0.5}')
    result = subprocess.run(['bash', str(ROOT / 'scripts/train_person_state.sh'),
        str(data), str(init), str(output), '2', '1200', 'frozen', '--dry-run'],
        check=True, capture_output=True, text=True)
    assert not output.exists()
    args = shlex.split(result.stdout)
    assert '--freeze-memory' in args
    assert args[args.index('--selection-groups') + 1] == 'segmentation,state'
    assert args[args.index('--language-last-layers') + 1] == '1'
    assert '--augment' in args
