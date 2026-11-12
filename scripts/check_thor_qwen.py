"""Check a Qwen worker with an isolated still-image replay over HTTP."""
import argparse
import base64
import json
from pathlib import Path
from urllib.request import Request, urlopen
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path, help='JPEG input for the isolated worker')
    parser.add_argument('--url', default='http://127.0.0.1:8090')
    parser.add_argument('--expect-stop', action='store_true', help='Image contains an oversized detected person')
    args = parser.parse_args()
    jpeg = base64.b64encode(args.image.read_bytes()).decode()
    epoch = f'offline:{uuid.uuid4().hex}'
    with urlopen(args.url + '/health', timeout=8) as response:
        health = json.load(response)
    assert health['ready'] and health['model_backend'] == 'qwen3-vl', health
    print(json.dumps(health), flush=True)

    for frame, stamp, source in [
        (1, 100.0, epoch), (2, 100.6, epoch), (3, 101.2, epoch),
        (1, 101.2, epoch), (1, 104.0, epoch), (1, 104.6, epoch + ':new'),
    ]:
        request = dict(camera_id='tidybot', stamp=stamp, source_epoch=source, jpeg=jpeg)
        body = json.dumps(request).encode()
        with urlopen(Request(args.url + '/infer', data=body), timeout=30) as response:
            result = json.load(response)
        assert result['stamp'] == stamp and result['model_id'] == health['model_id'], result
        assert len(result['history_capture_stamps']) == frame, result
        if args.expect_stop:
            assert result['valid'] and result['action'] == 'STOP', result
            assert result['generation_tokens'] == 0 and result['largest_person_box_area_ratio'] is not None, result
        elif frame == 3:
            assert result['valid'] and result['action'] in ('OBSERVE', 'CONTINUE', 'DANGER_WARNING'), result
            assert json.loads(result['raw'])['policy_reason'], result
            assert result['model_raw'] and result['explanation'], result
        else:
            assert not result['valid'] and result['action'] == 'DATA_LOSS', result
        print(json.dumps({key: result[key] for key in (
            'stamp', 'history_capture_stamps', 'valid', 'motion', 'action', 'reason', 'inference_ms',
        )}), flush=True)


if __name__ == '__main__':
    main()
