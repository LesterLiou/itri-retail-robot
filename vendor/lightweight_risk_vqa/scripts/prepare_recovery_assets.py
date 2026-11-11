"""Fetch pinned public recovery assets; no private images are uploaded."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--teacher', type=Path)
    args = parser.parse_args()
    import requests
    args.root.mkdir(parents=True, exist_ok=True)
    url = 'https://s3.amazonaws.com/images.cocodataset.org/annotations/annotations_trainval2014.zip'
    expected = '031296bbc80c45a1d1f76bf9a90ead27e94e99ec629208449507a4917a3bf009'
    target = args.root / 'annotations_trainval2014.zip'
    if not target.exists():
        temporary = target.with_suffix('.part')
        response = requests.get(url, stream=True, timeout=(30, 120))
        response.raise_for_status()
        with temporary.open('wb') as stream:
            for block in response.iter_content(4 * 1024**2):
                stream.write(block)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != expected:
            raise ValueError('Official COCO annotation checksum differs from TFDS checksum')
        temporary.rename(target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest != expected:
        raise ValueError('Existing COCO annotation archive checksum mismatch')
    with zipfile.ZipFile(target) as archive:
        for name in ('captions_train2014.json', 'captions_val2014.json'):
            path = args.root / name
            if not path.exists():
                path.write_bytes(archive.read('annotations/' + name))
    report = {'coco_url': url, 'sha256': digest,
              'checksum_source': 'https://raw.githubusercontent.com/tensorflow/datasets/master/tensorflow_datasets/url_checksums/coco_captions.txt'}
    print(json.dumps(report), flush=True)
    if args.teacher:
        from huggingface_hub import HfApi, snapshot_download
        model = 'Qwen/Qwen3-VL-2B-Instruct'
        pin_file = args.root / 'teacher_revision.json'
        if pin_file.exists():
            pin = json.loads(pin_file.read_text())
        else:
            pin = {'model': model, 'revision': HfApi().model_info(model).sha,
                   'source': 'https://huggingface.co/' + model}
            pin_file.write_text(json.dumps(pin, indent=2))
        snapshot_download(model, revision=pin['revision'], local_dir=args.teacher,
                          allow_patterns=['*.json', '*.safetensors', '*.txt', '*.jinja', 'README.md'])
        report['teacher'] = {**pin, 'path': str(args.teacher)}
        print(json.dumps(report['teacher']), flush=True)
    (args.root / 'assets.json').write_text(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
