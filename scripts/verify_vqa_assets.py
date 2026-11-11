#!/usr/bin/env python3
"""Verify the project-local runtime and asset copies without importing Torch."""
import hashlib
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
manifest=json.loads((ROOT/'config/vqa/assets.manifest.json').read_text())
for row in manifest['files']:
    path=ROOT/row['path'];assert path.resolve().is_relative_to(ROOT),row['path']
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024*1024),b''):digest.update(block)
    assert digest.hexdigest()==row['sha256'],row['path']
for line in (ROOT/'.env').read_text().splitlines():
    if line.startswith(('VQA_CHECKPOINT_DIR=','VQA_SOURCE_DIR=','YOLO_MODEL_FILE=','VIDEO_DIR=')):
        name,value=line.split('=',1);path=(ROOT/value).resolve()
        assert path.is_relative_to(ROOT) and path.exists(),name
print(json.dumps(dict(result='PASS',verified_files=len(manifest['files']),model_id=manifest['model_id'],prompt_id=manifest['prompt_id'],all_runtime_asset_paths_inside_project=True),indent=2))
