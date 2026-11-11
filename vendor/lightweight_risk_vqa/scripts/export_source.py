"""Export only maintained source/config/docs; never bundle weights or local state."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile


def source_files(root):
    root = Path(root).resolve()
    names = {'README.md', 'CHANGELOG.md', 'THIRD_PARTY.md', 'setup.py', 'setup.cfg', 'pyproject.toml',
             'package.xml', '.gitignore', 'MANIFEST.in',
             'requirements-runtime.txt', 'requirements-vlm.txt', 'requirements-dev.txt'}
    selected = [root / name for name in sorted(names) if (root / name).is_file()]
    patterns = {
        'lightweight_risk_vqa': ('*.py',), 'light_foundation_vlm': ('*.py',),
        'test': ('*.py',), 'docs': ('*.md',), 'docs/assets': ('*.svg', '*.png'), 'scripts': ('*.py', '*.sh'),
        'config': ('*.yaml', '*.yml', '*.json'), 'launch': ('*.py',),
        '.github/workflows': ('*.yaml', '*.yml'),
    }
    for folder, globs in patterns.items():
        for pattern in globs:
            selected.extend((root / folder).rglob(pattern))
    selected.append(root / 'resource/lightweight_risk_vqa')
    safe = []
    for path in sorted(set(selected)):
        if not path.is_file() or '__pycache__' in path.parts:
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f'Refusing source symlink: {path}')
        if path.stat().st_size > 5 * 1024**2:
            raise ValueError(f'Unexpectedly large source file: {path}')
        safe.append(path)
    return safe


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists() or not args.output.name.endswith('.tar.gz'):
        parser.error('Choose a new .tar.gz output path')
    root = Path(__file__).resolve().parents[1]
    files = source_files(root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(args.output, 'w:gz') as archive:
        for path in files:
            archive.add(path, arcname=str(Path('lightweight_risk_vqa') / path.relative_to(root)), recursive=False)
    report = {'output': str(args.output.resolve()), 'files': len(files),
              'bytes': args.output.stat().st_size,
              'sha256': hashlib.sha256(args.output.read_bytes()).hexdigest()}
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
