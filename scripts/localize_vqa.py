#!/usr/bin/env python3
"""Copy a complete existing bundle/runtime into this repo without changing sources."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
def digest(path):
    result=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024*1024),b''):result.update(block)
    return result.hexdigest()
def copy_file(source,target):
    if not target.resolve().is_relative_to(ROOT):raise ValueError('Destination escapes project')
    value=digest(source);target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if digest(target)!=value:raise FileExistsError(f'Refusing to overwrite different file: {target}')
    else:shutil.copyfile(source,target)
    if digest(target)!=value:raise RuntimeError(f'Hash mismatch: {target}')
    return dict(path=str(target.relative_to(ROOT)),bytes=target.stat().st_size,sha256=value)
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--checkpoint',type=Path,required=True)
    parser.add_argument('--source-package',type=Path,required=True)
    parser.add_argument('--yolo',type=Path,required=True)
    parser.add_argument('--videos',type=Path)
    parser.add_argument('--apply-env',action='store_true')
    args=parser.parse_args();records=[]
    groups=[(args.checkpoint,ROOT/'models/vqa/best'),(args.source_package,ROOT/'vendor/lightweight_risk_vqa')]
    if args.videos:groups.append((args.videos,ROOT/'data/video'))
    for source,destination in groups:
        if not source.is_dir():raise FileNotFoundError(source)
        for item in sorted(source.rglob('*')):
            if item.is_file() and not any(p in ('__pycache__','.pytest_cache','.git') for p in item.relative_to(source).parts):
                records.append(copy_file(item,destination/item.relative_to(source)))
    records.append(copy_file(args.yolo,ROOT/'models/yolo/yolov8n.pt'))
    module=ROOT/'vendor/lightweight_risk_vqa/lightweight_risk_vqa/robot_vqa.py'
    prompt=next(ast.literal_eval(n.value) for n in ast.parse(module.read_text()).body
                if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='ROBOT_QUERY' for t in n.targets))
    target=ROOT/'config/vqa/prompt_training_compatible.txt';target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists() and target.read_text()!=prompt:raise FileExistsError('Different prompt already exists')
    target.write_text(prompt)
    records.append(dict(path=str(target.relative_to(ROOT)),sha256=digest(target),bytes=target.stat().st_size))
    manifest=dict(format_version=1,upstream_sha='889558ff0ff5f43c818ce82aa39b7b95734e7e6e',
        model_id=digest(ROOT/'models/vqa/best/model.pt'),prompt_id=hashlib.sha256(prompt.encode()).hexdigest(),files=records)
    (ROOT/'config/vqa/assets.manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    if args.apply_env:
        env=ROOT/'.env';lines=env.read_text().splitlines() if env.exists() else []
        updates=dict(VQA_CHECKPOINT_DIR='./models/vqa/best',VQA_SOURCE_DIR='./vendor/lightweight_risk_vqa',YOLO_MODEL_FILE='./models/yolo/yolov8n.pt')
        if args.videos:updates['VIDEO_DIR']='./data/video'
        for name,value in updates.items():
            lines=[line for line in lines if not line.startswith(name+'=')];lines.append(name+'='+value)
        env.write_text('\n'.join(lines)+'\n')
    print(json.dumps(dict(copied_files=len(records),model_id=manifest['model_id'],prompt_id=manifest['prompt_id']),indent=2))
if __name__=='__main__':main()
