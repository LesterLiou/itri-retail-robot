import base64
import json

import pytest

from lightweight_risk_vqa.worker_client import LlavaWorkerClient, extract_json_object


class Response:
    def __init__(self, value=None, chunks=()):
        self.value, self.chunks = value, chunks

    def raise_for_status(self):
        pass

    def json(self):
        return self.value

    def iter_lines(self, **kwargs):
        assert kwargs['delimiter'] == b'\0'
        return iter(self.chunks)


def test_external_baseline_preserves_controller_and_worker_protocol(monkeypatch):
    calls = []
    prompt = 'USER: <image> question ASSISTANT:'
    answer = '{"motion":"STATIONARY","action":"CONTINUE","explanation":"still"}'

    def post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith('/list_models'):
            return Response({'models': ['llava-baseline']})
        if url.endswith('/get_worker_address'):
            return Response({'address': 'http://worker:22000'})
        return Response(chunks=[json.dumps({'error_code': 0, 'text': prompt + answer}).encode()])

    monkeypatch.setattr('lightweight_risk_vqa.worker_client.requests.post', post)
    client = LlavaWorkerClient('http://controller:11000', request_retry_count=0)
    raw, metadata = client.generate(prompt, b'JPEG', max_new_tokens=192)
    payload = calls[-1][1]['json']
    assert payload['prompt'] == prompt
    assert payload['images'] == [base64.b64encode(b'JPEG').decode('ascii')]
    assert payload['task_type'] == 'risk_assessment'
    assert payload['use_sam3_candidates'] is False
    assert metadata['model'] == 'llava-baseline'
    assert raw == answer
    assert extract_json_object(raw)['motion'] == 'STATIONARY'


def test_unstructured_direction_cannot_masquerade_as_vqa_object():
    with pytest.raises(ValueError, match='does not contain an object'):
        extract_json_object('L864: AWAY FROM CAMERA')
