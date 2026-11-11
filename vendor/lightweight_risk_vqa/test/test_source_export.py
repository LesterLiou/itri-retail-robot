import importlib.util
from pathlib import Path

import pytest


def exporter():
    path = Path(__file__).resolve().parents[1] / 'scripts/export_source.py'
    spec = importlib.util.spec_from_file_location('lightweight_source_export', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_excludes_weights_recordings_credentials_and_build_output(tmp_path):
    names = ['README.md', '.gitignore', 'light_foundation_vlm/model.py',
             'docs/TRAINING.md', 'resource/lightweight_risk_vqa',
             '.env', 'checkpoints/model.pt', 'datasets/input.jpg', 'results/result.json',
             'build/lib/copied.py', 'light_foundation_vlm/model.pt', 'scripts/credentials.key']
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture')
    files = {str(p.relative_to(tmp_path)) for p in exporter().source_files(tmp_path)}
    assert files == set(names[:5])


def test_release_refuses_source_symlinks(tmp_path):
    (tmp_path / 'README.md').symlink_to('/etc/hosts')
    with pytest.raises(ValueError, match='symlink'):
        exporter().source_files(tmp_path)


def test_release_includes_camera_policy_configuration(tmp_path):
    path = tmp_path / 'config/camera_policy.json'
    path.parent.mkdir()
    path.write_text('{}')
    assert path in exporter().source_files(tmp_path)
