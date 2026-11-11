"""Native Qwen3-VL direction estimates for observation-only replay and deployment."""
from collections import deque
import hashlib
import json
import math
from pathlib import Path
import threading
import time
import uuid

import cv2
from PIL import Image
import torch
from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

from lightweight_risk_vqa.robot_vqa import MOTIONS


PROMPT = (
    'These three images are consecutive samples from a forward camera, shown oldest to newest. '
    'Compare the same main visible person across the images. Classify their movement relative to the camera '
    'over the sequence using exactly one of TOWARD_CAMERA, AWAY_FROM_CAMERA, LATERAL_ACROSS_VIEW, '
    'STATIONARY, UNCERTAIN. Judge change across images and use UNCERTAIN if the evidence is ambiguous. '
    'Return only a JSON object with keys "motion" and "evidence". In evidence, briefly say what visibly changed or stayed the same.'
)
FRAME_COUNT = 3
MAX_GAP_SECONDS = 2.0


def parse_motion(raw):
    text = raw.strip()
    if text.startswith('```') and text.endswith('```'):
        text = text.removeprefix('```json').removeprefix('```').removesuffix('```').strip()
    try:
        result = json.loads(text)
    except ValueError:
        return None
    if not isinstance(result, dict) or set(result) != {'motion', 'evidence'}:
        return None
    motion, evidence = result['motion'], result['evidence']
    if not isinstance(motion, str) or motion not in MOTIONS or not isinstance(evidence, str) or not evidence.strip():
        return None
    return result


class Qwen3VLBackend:
    model_backend = 'qwen3-vl'

    def __init__(self, checkpoint, device, decode_frame):
        checkpoint = Path(checkpoint)
        weights = sorted(checkpoint.glob('model*.safetensors'))
        if not weights:
            raise ValueError(f'No Qwen3-VL safetensors weights in {checkpoint}')
        hashes = []
        for path in weights:
            with path.open('rb') as stream:
                hashes.append(hashlib.file_digest(stream, 'sha256').hexdigest())
        self.model_id = hashes[0] if len(hashes) == 1 else hashlib.sha256(''.join(hashes).encode()).hexdigest()
        self.prompt_id = hashlib.sha256(PROMPT.encode()).hexdigest()
        self.worker_id = uuid.uuid4().hex
        self.lock = threading.Lock()
        self.history = {}
        self.device = device
        self.decode_frame = decode_frame
        dtype = torch.bfloat16 if device.type == 'cuda' else torch.float32
        self.model = Qwen3VLForConditionalGeneration.from_pretrained(
            checkpoint, local_files_only=True, dtype=dtype, attn_implementation='sdpa',
        ).to(device).eval()
        self.processor = AutoProcessor.from_pretrained(checkpoint, local_files_only=True)

    @torch.inference_mode()
    def infer(self, data):
        started = time.monotonic()
        camera, stamp, epoch = data['camera_id'], float(data['stamp']), data['source_epoch']
        if not math.isfinite(stamp) or data.get('inference_mode', 'vqa-only') != 'vqa-only':
            raise ValueError('Qwen3-VL requires a finite capture timestamp and vqa-only input')
        image = Image.fromarray(cv2.cvtColor(self.decode_frame(data), cv2.COLOR_BGR2RGB))
        image.thumbnail((480, 360), Image.Resampling.BILINEAR)
        previous_epoch, frames = self.history.get(camera, (None, ()))
        if previous_epoch != epoch or not frames or not 0 < stamp - frames[-1][0] <= MAX_GAP_SECONDS:
            frames = deque(maxlen=FRAME_COUNT)
        frames.append((stamp, image))
        self.history[camera] = (epoch, frames)

        raw = ''
        parsed = None
        tokens = 0
        reason = 'Qwen3-VL waiting for three consecutive camera frames'
        if len(frames) == FRAME_COUNT:
            content = [{'type': 'image', 'image': frame} for _, frame in frames]
            content.append({'type': 'text', 'text': PROMPT})
            inputs = self.processor.apply_chat_template(
                [{'role': 'user', 'content': content}], tokenize=True, add_generation_prompt=True,
                return_dict=True, return_tensors='pt',
            ).to(self.device)
            output = self.model.generate(**inputs, max_new_tokens=128, do_sample=False)
            generated = output[0, inputs['input_ids'].shape[1]:]
            tokens = generated.numel()
            raw = self.processor.decode(generated, skip_special_tokens=True).strip()
            parsed = parse_motion(raw)
            reason = '' if parsed else 'Qwen3-VL response failed the motion schema'

        elapsed = round((time.monotonic() - started) * 1000, 2)
        # This checkpoint estimates direction only. Publish OBSERVE to the
        # observation domain and retain the model's evidence for the monitor.
        action = 'OBSERVE' if parsed else 'DATA_LOSS'
        evidence = parsed['evidence'] if parsed else ''
        view = dict(parsed, explanation=evidence) if parsed else None
        return dict(
            action=action, raw_action=action, valid=bool(parsed), reason=reason,
            motion=parsed['motion'] if parsed else 'UNCERTAIN', observation='', explanation=evidence,
            raw=json.dumps(view, ensure_ascii=False) if view else raw, model_raw=raw,
            model_id=self.model_id, prompt_id=self.prompt_id, worker_id=self.worker_id,
            model_backend=self.model_backend, stamp=stamp, camera_id=camera,
            inference_mode='qwen3-vl-observe', inference_ms=elapsed, generation_tokens=tokens,
            decoding_profile='motion-json', generation_engine='hf-native',
            history_capture_stamps=[captured_at for captured_at, _ in frames],
            metrics=dict(inference_mode='qwen3-vl-observe', segmentation_enabled=False),
        )
