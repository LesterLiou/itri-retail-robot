#!/usr/bin/env python3
"""Fetch immutable upstream sources without changing existing working trees."""
import argparse
from pathlib import Path
import re
import subprocess
import yaml

ROOT = Path(__file__).resolve().parents[1]
OPENARM_PATHS = [
    'ros2_ws/src/openarm_can', 'ros2_ws/src/openarm_description',
    'ros2_ws/src/openarm_ros2/openarm_hardware',
    'ros2_ws/src/openarm_ros2/openarm_bringup',
    'ros2_ws/src/openarm_ros2/openarm_bimanual_moveit_config',
]


def git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', nargs='+')
    args = parser.parse_args()
    sources = yaml.safe_load((ROOT / 'dependencies.repos').read_text())['repositories']
    for name, spec in sources.items():
        if args.only and name not in args.only:
            continue
        sha = spec['version']
        if not re.fullmatch('[0-9a-f]{40}', sha):
            raise ValueError(f'{name}: expected full SHA')
        path = ROOT / 'external' / name
        if path.exists():
            if git(path, 'rev-parse', 'HEAD') != sha or git(path, 'status', '--porcelain'):
                raise RuntimeError(f'{path}: version mismatch or local changes; refusing to update')
            print(f'OK {name} {sha}', flush=True)
            continue
        path.mkdir(parents=True)
        git(path, 'init')
        git(path, 'remote', 'add', 'origin', spec['url'])
        git(path, 'config', 'remote.origin.promisor', 'true')
        git(path, 'config', 'remote.origin.partialclonefilter', 'blob:none')
        git(path, 'fetch', '--depth', '1', '--filter=blob:none', 'origin', sha)
        if name == 'openarm_dev':
            git(path, 'sparse-checkout', 'init', '--cone')
            git(path, 'sparse-checkout', 'set', *OPENARM_PATHS)
        git(path, 'checkout', '--detach', 'FETCH_HEAD')
        if git(path, 'rev-parse', 'HEAD') != sha:
            raise RuntimeError(f'{name}: fetched unexpected revision')
        print(f'FETCHED {name} {sha}', flush=True)


if __name__ == '__main__':
    main()
