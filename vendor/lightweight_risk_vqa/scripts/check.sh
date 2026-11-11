#!/usr/bin/env bash
set -euo pipefail
package_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$package_root"
export PYTHONDONTWRITEBYTECODE=1
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
export PYTHONPATH="$package_root${PYTHONPATH:+:$PYTHONPATH}"
python3 - <<'PY'
import ast
from pathlib import Path
import re
import subprocess
for folder in ('lightweight_risk_vqa', 'light_foundation_vlm', 'test', 'launch'):
    for file in Path(folder).rglob('*.py'):
        ast.parse(file.read_text(), filename=str(file))
for file in Path('scripts').glob('*.sh'):
    subprocess.run(['bash', '-n', str(file)], check=True)
for file in [Path('README.md'), *Path('docs').glob('*.md')]:
    for link in re.findall(r'\[[^\]]*\]\(([^\s)]+)\)', file.read_text()):
        if ':' not in link and not link.startswith('#'):
            target = link.split('#', 1)[0]
            assert (file.parent / target).exists(), (file, link)
print('Python, shell and local documentation checks passed.')
PY
python3 -m pytest --import-mode=importlib -p no:cacheprovider -q test
