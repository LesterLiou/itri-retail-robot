"""Native Qwen3-VL direction estimates for observation-only replay and deployment."""
from collections import deque
import hashlib
import json
import math
import os
from pathlib import Path
import threading
import time
import uuid

import cv2
from PIL import Image
import torch
from transformers import AutoProcessor, Qwen3VLForConditionalGeneration, StoppingCriteriaList

from lightweight_risk_vqa.robot_vqa import MOTIONS, json_object_end
from lightweight_risk_vqa.camera_policy import PersonBoxDetector


PROMPT = (
    'These three images are consecutive samples from a forward camera, shown oldest to newest. '
    'Compare the same main visible person across the images. Classify their movement relative to the camera '
    'over the sequence using exactly one of TOWARD_CAMERA, AWAY_FROM_CAMERA, LATERAL_ACROSS_VIEW, '
    'STATIONARY, UNCERTAIN. Judge change across images and use UNCERTAIN if the evidence is ambiguous. '
    'Return a single compact JSON object with keys "motion" and "evidence". '
    'Use at most eight words of visible evidence. No markdown or extra text.'
)
FRAME_COUNT = 3
MAX_GAP_SECONDS = 2.0


def motion_json(raw):
    return raw.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()


def parse_motion(raw):
    try:
        result = json.loads(motion_json(raw))
    except ValueError:
        return None
    if not isinstance(result, dict) or set(result) != {'motion', 'evidence'}:
        return None
    motion, evidence = result['motion'], result['evidence']
    if not isinstance(motion, str) or motion not in MOTIONS or not isinstance(evidence, str) or not evidence.strip():
        return None
    return result


def motion_advisory(parsed, boxes, box_area, far_area, stop_area):
    if box_area is not None and box_area >= stop_area:
        return 'STOP', f'Largest person box covers {box_area:.1%} of the image (threshold {stop_area:.0%}).'
    if parsed is None:
        return 'DATA_LOSS', ''
    if parsed['motion'] == 'TOWARD_CAMERA':
        return 'DANGER_WARNING', 'Model detected movement toward the camera.'
    if parsed['motion'] == 'AWAY_FROM_CAMERA' and box_area is not None:
        return 'CONTINUE', 'Model detected movement away from the camera.'
    if parsed['motion'] == 'STATIONARY' and len(boxes) == 1 and box_area <= far_area:
        x1, y1, x2, y2 = boxes[0]
        if 0 < x1 < x2 < 1 and 0 < y1 < y2 < 1:
            return 'CONTINUE', f'One stationary person with an unclipped box covering {box_area:.1%} of the image.'
    return 'OBSERVE', 'Motion or person size does not meet the criteria for a direction-based advisory.'


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
        self.stop_area = float(os.environ.get('QWEN_STOP_BOX_AREA_RATIO', '0.90'))
        self.far_area = float(os.environ.get('QWEN_FAR_BOX_AREA_RATIO', '0.10'))
        if not 0 < self.far_area < self.stop_area <= 1:
            raise ValueError('Qwen person-box thresholds must satisfy 0 < far < stop <= 1')
        self.person_detector = PersonBoxDetector(os.environ.get('YOLO_WEIGHTS', '/models/yolov8n.pt'), str(device))
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
        frame = self.decode_frame(data)
        boxes = self.person_detector.detect(frame)
        box_area = max(((x2 - x1) * (y2 - y1) for x1, y1, x2, y2 in boxes), default=None)
        oversized = box_area is not None and box_area >= self.stop_area
        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        image.thumbnail((384, 288), Image.Resampling.BILINEAR)
        previous_epoch, frames = self.history.get(camera, (None, ()))
        if previous_epoch != epoch or not frames or not 0 < stamp - frames[-1][0] <= MAX_GAP_SECONDS:
            frames = deque(maxlen=FRAME_COUNT)
        frames.append((stamp, image))
        self.history[camera] = (epoch, frames)

        raw = ''
        parsed = None
        tokens = 0
        reason = 'Qwen3-VL waiting for three consecutive camera frames'
        if not oversized and len(frames) == FRAME_COUNT:
            content = [{'type': 'image', 'image': frame} for _, frame in frames]
            content.append({'type': 'text', 'text': PROMPT})
            inputs = self.processor.apply_chat_template(
                [{'role': 'user', 'content': content}], tokenize=True, add_generation_prompt=True,
                return_dict=True, return_tensors='pt',
            ).to(self.device)
            prefix_length = inputs['input_ids'].shape[1]

            def complete_motion_json(input_ids, scores, **kwargs):
                text = self.processor.decode(input_ids[0, prefix_length:], skip_special_tokens=True)
                return json_object_end(motion_json(text)) is not None

            output = self.model.generate(
                **inputs, max_new_tokens=64, do_sample=False,
                stopping_criteria=StoppingCriteriaList([complete_motion_json]),
            )
            generated = output[0, prefix_length:]
            tokens = generated.numel()
            raw = self.processor.decode(generated, skip_special_tokens=True).strip()
            parsed = parse_motion(raw)
            reason = '' if parsed else 'Qwen3-VL response failed the motion schema'

        elapsed = round((time.monotonic() - started) * 1000, 2)
        action, policy_reason = motion_advisory(parsed, boxes, box_area, self.far_area, self.stop_area)
        valid = bool(parsed) or oversized
        motion = parsed['motion'] if parsed else 'UNCERTAIN'
        evidence = parsed['evidence'] if parsed else policy_reason
        view = dict(motion=motion, evidence=evidence, explanation=evidence,
                    policy_reason=policy_reason, largest_person_box_area_ratio=box_area) if valid else None
        return dict(
            action=action, raw_action=action, valid=valid, reason='' if valid else reason,
            motion=motion, observation='', explanation=evidence,
            raw=json.dumps(view, ensure_ascii=False) if view else raw, model_raw=raw,
            model_id=self.model_id, prompt_id=self.prompt_id, worker_id=self.worker_id,
            model_backend=self.model_backend, stamp=stamp, camera_id=camera,
            inference_mode='qwen3-vl-observe', inference_ms=elapsed, generation_tokens=tokens,
            decoding_profile='motion-json', generation_engine='hf-native',
            history_capture_stamps=[captured_at for captured_at, _ in frames],
            largest_person_box_area_ratio=box_area,
            metrics=dict(inference_mode='qwen3-vl-observe', segmentation_enabled=False,
                         bbox_source='yolo', person_boxes=boxes),
        )
