"""ConvLSTM monitor: embedded Qwen VQA or segmentation + external VLM baseline."""
import argparse
import json
import math
import os
from pathlib import Path
import sys
import threading
import time

# The non-headless opencv-python wheel overwrites Qt's platform-plugin path
# during import. Load PyQt's QtCore first, remember its matching plugins, then
# restore that path before QApplication is imported/constructed.
from PyQt5.QtCore import QLibraryInfo

_PYQT_PLUGIN_PATH = QLibraryInfo.location(QLibraryInfo.PluginsPath)
import cv2
os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = _PYQT_PLUGIN_PATH
if '/cv2/qt/fonts' in os.environ.get('QT_QPA_FONTDIR', ''):
    os.environ.pop('QT_QPA_FONTDIR')
import numpy as np
import torch
import torch.nn.functional as F
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit,
                             QMainWindow, QPushButton, QTextEdit, QVBoxLayout, QWidget,
                             QFrame, QSizePolicy)

from .worker_client import LlavaWorkerClient, extract_json_object
from .availability import live_decision
from .person_state import STATE_QUERY, state_schema_error
from .camera_policy import (CameraPolicy, CameraPolicyConfig, PersonBoxDetector,
                            camera_live_decision, mask_to_boxes)
from .robot_vqa import (ROBOT_QUERY, COMPACT_ROBOT_QUERY, PersonMotionHints, query_with_hints,
                        robot_live_decision, robot_schema_error, StopAfterJsonObject, json_object_end)

VQA_USER_PROMPT = """<image>
The left panel is the current camera frame. The right panel is the same frame with the
Light Foundation temporal segmentation mask shown in red. Assess only visible evidence.
Return exactly one JSON object with these keys:
{"current_observation":"brief description", "future_prediction":"brief next likely motion",
 "motion":"TOWARD_CAMERA|AWAY_FROM_CAMERA|LATERAL_ACROSS_VIEW|STATIONARY|UNCERTAIN",
 "action":"STOP|DANGER_WARNING|CONTINUE|NO_RESPONSE|OBSERVE", "confidence":0.0,
 "explanation":"brief reason"}
Use UNCERTAIN and OBSERVE when visible evidence is insufficient. Camera geometry
computes the final robot decision separately. No markdown or extra text."""
MOTIONS = {'TOWARD_CAMERA', 'AWAY_FROM_CAMERA', 'LATERAL_ACROSS_VIEW',
           'STATIONARY', 'UNCERTAIN'}
ACTIONS = {'STOP', 'DANGER_WARNING', 'CONTINUE', 'NO_RESPONSE', 'OBSERVE'}


def embedded_schema_error(parsed, expect_state=False, expect_robot=False):
    if expect_robot:
        return robot_schema_error(parsed)
    if not isinstance(parsed, dict):
        return 'model response is not a JSON object'
    if parsed.get('motion') not in MOTIONS:
        return 'model response has an invalid motion'
    if parsed.get('action') not in ACTIONS:
        return 'model response has an invalid action'
    if not isinstance(parsed.get('explanation'), str):
        return 'model response has no explanation string'
    if expect_state or 'current_activity' in parsed or 'observed_transition' in parsed:
        return state_schema_error(parsed)
    return ''


def vicuna_image_prompt(user_prompt=VQA_USER_PROMPT):
    return ('A chat between a curious user and an artificial intelligence assistant. '
            'The assistant gives helpful, detailed, and polite answers to the user. '
            '###USER: ' + user_prompt + '###ASSISTANT:')


def make_vqa_panel(frame, overlay):
    height = 360
    width = max(1, round(frame.shape[1] * height / frame.shape[0]))
    left = cv2.resize(frame, (width, height))
    right = cv2.resize(overlay, (width, height))
    for image, title in ((left, 'CURRENT FRAME'), (right, 'CONVLSTM MASK')):
        cv2.rectangle(image, (0, 0), (width, 32), (0, 0, 0), -1)
        cv2.putText(image, title, (10, 23), cv2.FONT_HERSHEY_SIMPLEX, .65,
                    (255, 255, 255), 2)
    return np.hstack((left, right))


class TemporalSegmenter:
    """Use the original deployment graph and retain ConvLSTM state across samples."""
    def __init__(self, checkpoint, device, query, person_mask=False, collect_boxes=True):
        from light_foundation_vlm.checkpoint import load_bundle
        dtype = torch.bfloat16 if str(device).startswith('cuda') else torch.float32
        self.model, self.tokenizer, self.manifest = load_bundle(checkpoint, dtype)
        if not self.model.use_temporal:
            raise ValueError('Live memory UI requires a temporal checkpoint')
        self.device = torch.device(device)
        self.model.to(self.device).eval()
        self.fast_labels = None
        self.state = None
        self.steps = 0
        self.person_mask = person_mask
        self.collect_boxes = collect_boxes
        self.set_query(query)

    def set_query(self, query):
        query = str(query).strip()
        if not query or '<seg>' in query:
            raise ValueError('Segmentation query must be nonempty and must not contain <seg>')
        ids = self.tokenizer.encode(query + '\n<seg>\n', add_special_tokens=False)
        self.input_ids = torch.tensor(ids, dtype=torch.long, device=self.device)[None]
        self.attention = torch.ones_like(self.input_ids)
        self.query = query
        # State QA and all-person segmentation have different training queries.
        # Share the encoded visual/memory prefix, rather than encoding a frame twice.
        self.mask_query = 'the people' if query == STATE_QUERY or getattr(self, 'person_mask', False) else query
        mask_ids = self.tokenizer.encode(self.mask_query + '\n<seg>\n', add_special_tokens=False)
        self.mask_input_ids = torch.tensor(mask_ids, dtype=torch.long, device=self.device)[None]
        self.mask_attention = torch.ones_like(self.mask_input_ids)
        with torch.inference_mode():
            text = self.model.language_model.get_input_embeddings()(self.input_ids)
            marker = self.input_ids.eq(self.model.config.seg_token_id)
            self.answer_text = torch.where(marker[..., None], self.model.seg_embedding.to(text), text)
        self.reset()

    def reset(self):
        self.state = None
        self.steps = 0

    @staticmethod
    def preprocess(frame):
        rgb = cv2.cvtColor(cv2.resize(frame, (320, 320)), cv2.COLOR_BGR2RGB)
        tensor = torch.from_numpy(rgb.copy()).permute(2, 0, 1).float().div(255)
        mean = tensor.new_tensor((0.485, 0.456, 0.406))[:, None, None]
        std = tensor.new_tensor((0.229, 0.224, 0.225))[:, None, None]
        # Match training ClipDataset/load_frames' contiguous NCHW layout. The
        # original permuted view selected different GPU convolution kernels and
        # accumulated measurable recurrent drift against the same batch clip.
        return ((tensor - mean) / std).contiguous()

    @torch.inference_mode()
    def process(self, frame, generate_answer=False, max_new_tokens=64,
                answer_query=None, expect_robot=False, visual_callback=None):
        started = time.perf_counter()
        # The release bundle keeps vision/FPN/ConvLSTM in float32 while Qwen is
        # bfloat16. Match predict.py and let model.forward cast visual tokens.
        frames = self.preprocess(frame)[None, None].to(self.device)
        segmentation_enabled = getattr(self, 'segmentation_enabled', True)
        forward_options = {} if segmentation_enabled else {'compute_masks': False}
        output = self.model(
            frames, self.mask_input_ids, attention_mask=self.mask_attention,
            frame_valid=torch.ones((1, 1), dtype=torch.bool, device=self.device),
            dt=torch.ones((1, 1), device=self.device), state=self.state,
            return_language_context=generate_answer, **forward_options)
        self.state = tuple(value.detach() for value in output['state'])
        self.steps += 1
        overlay = frame.copy() if segmentation_enabled else None
        mask = None
        if segmentation_enabled:
            probability = F.interpolate(output['mask_logits'].float(), size=frame.shape[:2],
                                        mode='bilinear', align_corners=False).sigmoid()[0, 0]
            mask = probability.cpu().numpy() > .5
            overlay[mask] = (overlay[mask] * .55 + np.array([40, 40, 255]) * .45).astype(np.uint8)
        if self.device.type == 'cuda':
            torch.cuda.synchronize()
        bbox_started = time.perf_counter()
        person_boxes = (mask_to_boxes(mask, getattr(self, 'min_component_area', .002))
                        if mask is not None and getattr(self, 'collect_boxes', True) else [])
        bbox_ms = (time.perf_counter() - bbox_started) * 1000
        visual_ms = round((time.perf_counter() - started) * 1000, 2)
        metrics = {
            'segmentation_enabled': segmentation_enabled,
            'segmentation_ms': visual_ms if segmentation_enabled else None,
            'visual_memory_ms': visual_ms,
            'presence_uncalibrated': (round(float(output['presence_logits'].sigmoid()[0]), 4)
                                      if segmentation_enabled else None),
            'mask_area_fraction': round(float(mask.mean()), 4) if mask is not None else None,
            'memory_steps_since_reset': self.steps,
            'memory_hidden_abs_mean': round(float(self.state[0].abs().mean()), 6),
            'visual_token_count': int(output['visual_token_count']),
            'person_boxes': person_boxes,
            'bbox_extraction_ms': round(bbox_ms, 3),
        }
        if getattr(self,'fast_labels',None):
            probabilities=output['event_logits'].float().softmax(-1)[0]
            index=int(probabilities.argmax())
            metrics['fast_motion']={'motion':self.fast_labels[index],
                'probability_uncalibrated':float(probabilities[index]),
                'supervision':'machine_geometry_weak_not_collision_ground_truth'}
        answer = None
        if visual_callback is not None:
            metrics['visual_publish_latency_ms']=round(1000*(time.perf_counter()-started),2)
            visual_callback(overlay,dict(metrics))
        if generate_answer:
            answer_started = time.perf_counter()
            inputs, attention = output['language_inputs'], output['language_attention_mask']
            if answer_query is not None or self.mask_query != self.query:
                count = output['visual_token_count']
                text, text_attention = self.answer_text, self.attention
                if answer_query is not None:
                    ids = self.tokenizer.encode(answer_query + '\n<seg>\n', add_special_tokens=False)
                    ids = torch.tensor(ids, dtype=torch.long, device=self.device)[None]
                    text = self.model.language_model.get_input_embeddings()(ids)
                    text = torch.where(ids.eq(self.model.config.seg_token_id)[..., None],
                                       self.model.seg_embedding.to(text), text)
                    text_attention = torch.ones_like(ids)
                inputs = torch.cat([inputs[:, :count], text], dim=1)
                attention = torch.cat([text_attention.new_ones((1, count)), text_attention], dim=1)
            generation_options = {}
            if expect_robot:
                from transformers import StoppingCriteriaList, LogitsProcessorList
                generation_options['stopping_criteria'] = StoppingCriteriaList([StopAfterJsonObject(self.tokenizer)])
                if getattr(self, 'robot_decoding', 'constrained') == 'constrained':
                    from .robot_decoding import RobotJsonDecoder
                    if not hasattr(self, 'robot_decoder'):
                        self.robot_decoder = RobotJsonDecoder(self.tokenizer)
                    generation_options['logits_processor'] = LogitsProcessorList([self.robot_decoder.processor()])
                metrics['robot_decoding'] = getattr(self, 'robot_decoding', 'constrained')
            generated = self.model.generate_from_context(
                inputs, attention, max_new_tokens, **generation_options)
            raw = self.tokenizer.decode(generated[0], skip_special_tokens=True).strip()
            end = json_object_end(raw) if expect_robot else None
            try:
                parsed = json.loads(raw[:end] if end is not None else raw)
            except json.JSONDecodeError:
                parsed = None
            if self.device.type == 'cuda':
                torch.cuda.synchronize()
            metrics['embedded_vqa_ms'] = round((time.perf_counter() - answer_started) * 1000, 2)
            answer = {'raw': raw, 'parsed': parsed,
                      'error': embedded_schema_error(parsed, self.query == STATE_QUERY, expect_robot)}
        return overlay, metrics, answer


class LivePipeline:
    def __init__(self, args):
        self.args = args
        self.camera_policy = (CameraPolicy(CameraPolicyConfig.load(args.camera_policy_config))
                              if args.inference_mode == 'legacy' else None)
        self.motion_hints = PersonMotionHints() if args.inference_mode == 'yolo-assisted' else None
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.paused = False
        self.requested_query = args.query
        self.state = {'status': 'LOADING MODEL', 'frame': None, 'overlay': None,
                      'frame_index': 0, 'loop': 0, 'metrics': {}, 'vqa_raw': '',
                      'vqa_parsed': None, 'vqa_error': '', 'vqa_model': args.model_name,
                      'segmentation_query': args.query, 'updated_at': None,
                      'frame_at': None, 'vqa_evidence_at': None, 'pipeline_error': '',
                      'capture_complete': False, 'segmentation_frame': None}
        if args.vqa_backend == 'embedded':
            self.state['vqa_model'] = f'embedded:{Path(args.checkpoint).name}'
        self.capture_thread = threading.Thread(target=self._capture, daemon=True)
        self.inference_thread = threading.Thread(target=self._infer, daemon=True)
        self.vqa_thread = threading.Thread(target=self._vqa, daemon=True)

    def start(self):
        self.capture_thread.start()
        self.inference_thread.start()
        if self.args.vqa_backend == 'worker':
            self.vqa_thread.start()

    def stop(self):
        self.stop_event.set()
        self.capture_thread.join(timeout=3)
        self.inference_thread.join(timeout=3)
        if self.args.vqa_backend == 'worker':
            self.vqa_thread.join(timeout=3)

    def set_query(self, query):
        with self.lock:
            self.requested_query = query

    def snapshot(self):
        with self.lock:
            value = dict(self.state)
            value.update(paused=self.paused, requested_query=self.requested_query.strip())
            value['decision'] = live_decision(value, time.monotonic(),
                self.args.max_frame_age_s, self.args.max_result_age_s)
            mode = getattr(self.args, 'inference_mode', 'legacy')
            if getattr(self.args,'fast_motion_head',False):
                from .fast_motion import fast_motion_decision
                value['vqa_health']=robot_live_decision(value,time.monotonic(),mode,
                    self.args.max_frame_age_s,self.args.max_result_age_s)
                value['decision']=fast_motion_decision(value,time.monotonic(),
                    getattr(self.args,'fast_head_threshold',.7),self.args.max_frame_age_s,
                    self.args.max_frame_age_s)
            elif mode != 'legacy':
                value['decision'] = robot_live_decision(value, time.monotonic(), mode,
                    self.args.max_frame_age_s, self.args.max_result_age_s)
            elif self.args.decision_policy == 'camera':
                value['decision'] = camera_live_decision(value, time.monotonic(),
                    self.args.max_frame_age_s, self.args.max_result_age_s,
                    self.camera_policy.config.max_gap_s)
            for key in ('frame', 'overlay', 'segmentation_frame'):
                if value[key] is not None:
                    value[key] = value[key].copy()
            return value

    def _capture(self):
        reader = cv2.VideoCapture(str(self.args.video))
        if not reader.isOpened():
            with self.lock:
                self.state['status'] = f'VIDEO ERROR: {self.args.video}'
                self.state['pipeline_error'] = self.state['status']
            return
        fps = reader.get(cv2.CAP_PROP_FPS)
        fps = fps if math.isfinite(fps) and fps>0 else 30.0
        count = reader.get(cv2.CAP_PROP_FRAME_COUNT)
        count = int(count) if math.isfinite(count) and count>0 else None
        delay = 1.0 / min(max(fps, 1.0), self.args.display_fps)
        index = loop = 0  # index is the next source frame to decode.
        playback_started=time.monotonic()
        pause_started=None
        try:
            while not self.stop_event.is_set():
                started = time.monotonic()
                if self.paused:
                    if pause_started is None:
                        pause_started=started
                    self.stop_event.wait(.05)
                    continue
                if pause_started is not None:
                    playback_started+=started-pause_started
                    pause_started=None
                # Preview FPS caps publication, not source-video playback speed.
                # Reading one source frame per 15Hz preview previously played
                # 30fps videos at half speed and changed the temporal evidence.
                wanted=max(0,int((started-playback_started)*fps+1e-6))
                if count is not None and wanted>=count:
                    if not self.args.loop:
                        with self.lock:
                            self.state.update(status='VIDEO COMPLETE',capture_complete=True)
                        break
                    cycles=wanted//count
                    loop+=cycles
                    playback_started+=cycles*count/fps
                    wanted%=count
                    reader.set(cv2.CAP_PROP_POS_FRAMES,0)
                    index=0
                if wanted<index:
                    self.stop_event.wait(min(delay,max(0,index/fps-(started-playback_started))))
                    continue
                if wanted-index>fps:
                    reader.set(cv2.CAP_PROP_POS_FRAMES,wanted)
                    index=wanted
                okay=True
                while index<wanted:
                    if not reader.grab():
                        okay=False
                        break
                    index+=1
                ok,frame=reader.read() if okay else (False,None)
                if not ok:
                    if not self.args.loop:
                        with self.lock:
                            self.state['status'] = 'VIDEO COMPLETE'
                            self.state['capture_complete'] = True
                        break
                    reader.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    index, loop = 0, loop + 1
                    playback_started=time.monotonic()
                    continue
                with self.lock:
                    self.state.update(frame=frame, frame_index=index, loop=loop,
                                      frame_at=min(time.monotonic(),playback_started+index/fps),
                                      source_fps=fps,video_time_s=index/fps)
                index += 1
                self.stop_event.wait(max(0, delay - (time.monotonic() - started)))
        finally:
            reader.release()

    def _infer(self):
        try:
            segmenter = TemporalSegmenter(self.args.checkpoint,
                                          self.args.device, self.args.query,
                                          person_mask=self.args.inference_mode != 'legacy' or self.args.decision_policy == 'camera',
                                          collect_boxes=self.args.inference_mode == 'legacy')
            segmenter.segmentation_enabled = not getattr(self.args, 'no_segmentation', False)
            segmenter.robot_decoding = self.args.robot_decoding
            if getattr(self.args,'fast_motion_head',False):
                from .fast_motion import head_labels
                if segmenter.model.event_head is None:
                    raise ValueError('This bundle does not contain a trained motion head')
                segmenter.fast_labels=head_labels(segmenter.manifest)
            if self.camera_policy is not None:
                segmenter.min_component_area = self.camera_policy.config.min_component_area
            detector = (PersonBoxDetector(self.args.bbox_model, self.args.device, self.args.bbox_image_size)
                        if self.args.bbox_source == 'yolo' else None)
        except Exception as exc:
            with self.lock:
                self.state['status'] = f'MODEL ERROR: {exc}'
                self.state['pipeline_error'] = self.state['status']
            return
        last_index = -1
        last_loop = -1
        interval = 1.0 / self.args.segmentation_hz
        last_embedded_vqa = float('-inf')
        with self.lock:
            self.state['status'] = 'LIVE — waiting for first frame'
        while not self.stop_event.wait(interval):
            with self.lock:
                frame = None if self.state['frame'] is None else self.state['frame'].copy()
                index, loop = self.state['frame_index'], self.state['loop']
                frame_at = self.state['frame_at']
                query = self.requested_query.strip()
            if frame is None or (index == last_index and loop == last_loop):
                continue
            if loop != last_loop and last_loop >= 0:
                segmenter.reset()
                if self.camera_policy is not None:
                    self.camera_policy.reset()
                if self.motion_hints is not None:
                    self.motion_hints.reset()
            if query != segmenter.query:
                try:
                    segmenter.set_query(query)
                    if self.camera_policy is not None:
                        self.camera_policy.reset()
                    if self.motion_hints is not None:
                        self.motion_hints.reset()
                except ValueError as exc:
                    with self.lock:
                        self.state['status'] = f'QUERY ERROR: {exc}'
                        self.state['pipeline_error'] = self.state['status']
                    continue
            last_index, last_loop = index, loop
            try:
                generate = (self.args.vqa_backend == 'embedded'
                            and time.monotonic() - last_embedded_vqa >= 1.0 / self.args.vqa_hz)
                # Detect BEFORE generation: the exact current frame's causal
                # measurements enter Qwen's text context, not an action override.
                boxes, hints, detector_ms = [], None, None
                if detector is not None:
                    detector_started = time.perf_counter()
                    boxes = detector.detect(frame)
                    detector_ms = round((time.perf_counter() - detector_started) * 1000, 3)
                    if self.motion_hints is not None:
                        hints = self.motion_hints.update(boxes, frame_at)
                process_options={}
                if getattr(self.args,'fast_motion_head',False):
                    def publish_visual(visual_overlay,visual_metrics):
                        visual_metrics.update(inference_mode=self.args.inference_mode,bbox_source=self.args.bbox_source)
                        if detector is not None:
                            visual_metrics.update(person_boxes=boxes, bbox_detector_ms=detector_ms)
                        with self.lock:
                            self.state.update(status='LIVE — motion head ready; VQA may still be generating',
                                visual_published_at=time.monotonic(),
                                overlay=visual_overlay,metrics=visual_metrics,segmentation_frame=frame,
                                segmentation_evidence_at=frame_at,segmentation_context=(loop,segmenter.query),
                                segmentation_index=(loop,index),segmentation_query=segmenter.mask_query,
                                yolo_hints=hints,updated_at=time.time())
                            if not self.state['pipeline_error'].startswith('VIDEO ERROR'):
                                self.state['pipeline_error']=''
                    process_options['visual_callback']=publish_visual
                overlay, metrics, answer = segmenter.process(
                    frame, generate_answer=generate, max_new_tokens=self.args.max_new_tokens,
                    answer_query=query_with_hints(query, hints) if generate and hints is not None else None,
                    expect_robot=self.args.inference_mode != 'legacy',**process_options)
                metrics['bbox_source'] = self.args.bbox_source
                metrics['inference_mode'] = self.args.inference_mode
                if self.args.inference_mode != 'legacy':
                    metrics['robot_decoding'] = self.args.robot_decoding
                if detector is not None:
                    metrics['mask_component_boxes'] = metrics['person_boxes']
                    metrics['person_boxes'] = boxes
                    metrics['bbox_detector_ms'] = detector_ms
                geometry = None
                if self.camera_policy is not None:
                    policy_started = time.perf_counter()
                    geometry = self.camera_policy.update(metrics['person_boxes'], frame_at,
                        person_target=detector is not None or segmenter.mask_query.lower() in {'the people', 'people', 'person', 'the person', 'persons'})
                    geometry['bbox_source'] = self.args.bbox_source
                    metrics['camera_policy_ms'] = round((time.perf_counter() - policy_started) * 1000, 3)
                if generate:
                    last_embedded_vqa = time.monotonic()
                with self.lock:
                    self.state.update(status=('LIVE — ConvLSTM segmentation running' if segmenter.segmentation_enabled
                                              else 'LIVE — ConvLSTM memory + VQA; segmentation OFF'),
                                      overlay=overlay, metrics=metrics,
                                      camera_geometry=geometry,
                                      yolo_hints=hints,
                                      segmentation_frame=frame,
                                      segmentation_evidence_at=frame_at,
                                      segmentation_context=(loop, segmenter.query),
                                      segmentation_index=(loop, index),
                                      segmentation_query=segmenter.mask_query,
                                      vqa_query=segmenter.query,
                                      updated_at=time.time())
                    if not self.state['pipeline_error'].startswith('VIDEO ERROR'):
                        self.state['pipeline_error'] = ''
                    if answer is not None:
                        self.state.update(vqa_raw=answer['raw'], vqa_parsed=answer['parsed'],
                                          vqa_published_at=time.monotonic(),
                                          vqa_error=answer['error'],
                                          vqa_evidence_at=frame_at,
                                          vqa_index=(loop,index),
                                          vqa_context=(loop, segmenter.query),
                                          vqa_yolo_hints=hints,
                                          vqa_decoding=metrics.get('robot_decoding', 'free'),
                                          vqa_latency_ms=metrics['embedded_vqa_ms'])
            except Exception as exc:
                with self.lock:
                    self.state['status'] = f'SEGMENTATION ERROR: {exc}'
                    self.state['pipeline_error'] = self.state['status']
                continue

    def _vqa(self):
        if self.args.vqa_backend != 'worker':
            return
        try:
            vqa = LlavaWorkerClient(self.args.controller_url, self.args.model_name,
                                    request_timeout_s=45, max_new_tokens=192,
                                    request_retry_count=0)
            vqa._resolve_model()
            with self.lock:
                self.state['vqa_model'] = vqa.model_name
        except Exception as exc:
            with self.lock:
                self.state['vqa_error'] = f'VQA WORKER UNAVAILABLE: {exc}'
            return
        last_index = -1
        interval = 1.0 / self.args.vqa_hz
        while not self.stop_event.wait(interval):
            with self.lock:
                frame = (None if self.state['segmentation_frame'] is None else
                         self.state['segmentation_frame'].copy())
                overlay = None if self.state['overlay'] is None else self.state['overlay'].copy()
                index = self.state.get('segmentation_index')
                evidence_at = self.state.get('segmentation_evidence_at')
                context = self.state.get('segmentation_context')
            if frame is None or overlay is None or index == last_index:
                continue
            last_index = index
            try:
                panel = make_vqa_panel(frame, overlay)
                ok, jpeg = cv2.imencode('.jpg', panel, [cv2.IMWRITE_JPEG_QUALITY, 88])
                if not ok:
                    raise RuntimeError('Cannot encode VQA panel')
                raw, metadata = vqa.generate(vicuna_image_prompt(), jpeg.tobytes(),
                                             max_new_tokens=192)
                try:
                    parsed = extract_json_object(raw)
                except ValueError:
                    parsed = None
                with self.lock:
                    self.state.update(vqa_raw=raw, vqa_parsed=parsed,
                                      vqa_error=embedded_schema_error(parsed),
                                      vqa_evidence_at=evidence_at, vqa_context=context,
                                      vqa_latency_ms=metadata['round_trip_ms'])
            except Exception as exc:
                with self.lock:
                    self.state.update(vqa_error=str(exc), vqa_parsed=None)


def to_pixmap(frame, target_width=720, target_height=None):
    if frame is None:
        return QPixmap()
    height, width = frame.shape[:2]
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    image = QImage(rgb.data, width, height, 3 * width, QImage.Format_RGB888).copy()
    pixmap = QPixmap.fromImage(image)
    if target_height is not None:
        return pixmap.scaled(target_width, target_height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return pixmap.scaledToWidth(target_width, Qt.SmoothTransformation)


def monitor_summary(state, parsed, evidence):
    """Present current evidence without inventing counts or changing decisions."""
    metrics = state.get('metrics', {})
    health = state.get('vqa_health', state['decision'])
    seg_age = evidence.get('segmentation_age_s')
    seg_context = state.get('segmentation_context')
    seg_fresh = (isinstance(seg_age, (int, float)) and 0 <= seg_age <= evidence.get('max_result_age_s', 8)
                 and seg_context is not None
                 and seg_context == (state.get('loop'), state.get('requested_query'))
                 and not state.get('pipeline_error'))
    people = 'Not provided\nVQA has no count output'
    source = metrics.get('bbox_source')
    if source in {'yolo', 'mask'}:
        if not seg_fresh:
            people = 'Unavailable\nDetection missing / stale'
        elif source == 'yolo':
            people = f"{len(metrics.get('person_boxes', []))} people\nYOLO detections"
        else:
            people = f"≈ {len(metrics.get('person_boxes', []))} regions\nMask components, not people IDs"
    elif parsed and type(parsed.get('person_count')) is int and parsed['person_count'] >= 0:
        people = f"{parsed['person_count']} people\nVQA estimate"
    def ms(value):
        return f'{value:,.0f} ms' if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0 else '—'
    latency = f"VQA {ms(state.get('vqa_latency_ms'))}"
    if metrics.get('segmentation_enabled', True):
        latency += f"\nSeg {ms(metrics.get('segmentation_ms'))}"
    else:
        latency += f"\nVision + memory {ms(metrics.get('visual_memory_ms'))} · Seg OFF"
    if source == 'yolo':
        latency += f" · YOLO {ms(metrics.get('bbox_detector_ms'))}"
    decision = state['decision']
    if not decision.get('data_available'):
        reason = f"WHY: Unable to assess current input — {decision['reason_code']}"
    elif not health.get('data_available'):
        reason = f"WHY: {decision['reason_code']}\nVQA unavailable: {health['reason_code']}"
    elif parsed:
        observation = parsed.get('observation') or parsed.get('current_activity') or parsed.get('current_observation') or 'Not provided'
        temporal = parsed.get('motion') or parsed.get('observed_transition') or parsed.get('future_prediction') or 'Not provided'
        explanation = parsed.get('explanation') or 'No explanation provided'
        # Keep the summary readable; the exact unabridged output is below.
        compact = lambda value: ' '.join(str(value).split())[:220]
        reason = (f"WHY (model): {compact(explanation)}\n"
                  f"SEEN: {compact(observation)} | MOTION: {compact(temporal)}")
        if parsed.get('collision_risk'):
            reason += f" | RISK: {parsed['collision_risk']}"
        if decision.get('decision_authority') not in {'embedded_vqa', 'model_research_hint'}:
            reason += f"\nCOMMAND SOURCE: {decision.get('decision_authority')} · {decision['reason_code']}"
    else:
        reason = f"WHY: {decision['reason_code']}"
    def age(name):
        value = evidence.get(name + '_age_s')
        return f'{value:.1f}s' if isinstance(value, (float, int)) and math.isfinite(value) else '—'
    visual_label = 'mask' if metrics.get('segmentation_enabled', True) else 'memory'
    freshness = (f"Evidence age · live {age('live')} · {visual_label} {age('segmentation')} · VQA {age('vqa')}"
                 f" | VQA {'valid' if health.get('data_available') else 'unavailable'}"
                 f" | Command source: {decision.get('decision_authority', 'unknown')}"
                 " | Latency = last completed call; evidence age includes waiting")
    return dict(people=people, latency=latency, reason=reason, freshness=freshness)


class Monitor(QMainWindow):
    def __init__(self, pipeline):
        super().__init__()
        self.pipeline = pipeline
        self.setWindowTitle('Light Foundation ConvLSTM + VQA — Live Monitor')
        self.resize(1500, 920)
        root = QWidget()
        layout = QVBoxLayout(root)
        root.setStyleSheet('QWidget{font-family:Sans Serif;} QPushButton{padding:6px;}')
        layout.setSpacing(8)
        self.status = QLabel('LOADING MODEL')
        self.status.setTextFormat(Qt.PlainText)
        self.status.setStyleSheet('font-size:13px;color:#526173;padding:2px')
        layout.addWidget(self.status)
        cards = QHBoxLayout()
        def card(title):
            box = QFrame()
            box.setStyleSheet('QFrame{background:#edf2f6;border-radius:8px;}')
            column = QVBoxLayout(box)
            caption = QLabel(title)
            caption.setStyleSheet('font-size:13px;color:#526173')
            value = QLabel('—')
            value.setTextFormat(Qt.PlainText)
            value.setWordWrap(True)
            value.setStyleSheet('font-size:25px;font-weight:bold;padding:4px')
            column.addWidget(caption)
            column.addWidget(value)
            cards.addWidget(box, 1)
            return value
        self.decision = card('CURRENT COMMAND')
        self.people = card('PEOPLE DETECTED')
        self.latency = card('MODEL LATENCY')
        layout.addLayout(cards)
        self.answer_summary = QLabel('Waiting for a valid VQA result...')
        self.answer_summary.setTextFormat(Qt.PlainText)
        self.answer_summary.setWordWrap(True)
        self.answer_summary.setStyleSheet('font-size:16px;padding:10px;background:#edf2f6;border-radius:8px')
        self.answer_summary.setMaximumHeight(145)
        layout.addWidget(self.answer_summary)
        images = QHBoxLayout()
        self.evidence_column = None
        self.original = QLabel('Waiting for video...')
        self.overlay = QLabel('Waiting for ConvLSTM...')
        for label, title in ((self.original, 'LIVE CAMERA'),
                             (self.overlay, 'SEGMENTATION EVIDENCE')):
            label.setAlignment(Qt.AlignCenter)
            label.setMinimumSize(320, 180)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
            label.setStyleSheet('background:#16191d;color:white;border:1px solid #555')
            column = QVBoxLayout()
            heading = QLabel(title)
            heading.setAlignment(Qt.AlignCenter)
            heading.setStyleSheet('font-size:13px;font-weight:bold;color:#526173')
            column.addWidget(heading)
            column.addWidget(label, 1)
            image_box = QWidget()
            image_box.setLayout(column)
            images.addWidget(image_box, 1)
            if label is self.overlay:
                self.evidence_column = image_box
        self.segmentation_enabled = not getattr(pipeline.args, 'no_segmentation', False)
        self.evidence_column.setVisible(self.segmentation_enabled)
        layout.addLayout(images, 5)
        self.freshness = QLabel('Waiting for evidence...')
        self.freshness.setTextFormat(Qt.PlainText)
        self.freshness.setStyleSheet('font-size:12px;color:#526173')
        layout.addWidget(self.freshness)
        controls = QHBoxLayout()
        pause = QPushButton('Pause / Resume')
        pause.clicked.connect(self._pause)
        controls.addWidget(pause)
        details_button = QPushButton('Show technical details / edit prompt')
        details_button.setCheckable(True)
        controls.addWidget(details_button)
        controls.addStretch()
        layout.addLayout(controls)
        self.details = QWidget()
        details_layout = QVBoxLayout(self.details)
        prompt_row = QHBoxLayout()
        prompt_row.addWidget(QLabel('Query:'))
        self.query = QLineEdit(pipeline.args.query)
        prompt_row.addWidget(self.query)
        apply_button = QPushButton('Apply + reset LSTM')
        apply_button.clicked.connect(lambda: pipeline.set_query(self.query.text()))
        prompt_row.addWidget(apply_button)
        details_layout.addLayout(prompt_row)
        self.metrics = QLabel('')
        self.metrics.setTextFormat(Qt.PlainText)
        self.metrics.setWordWrap(True)
        self.metrics.setMaximumHeight(70)
        details_layout.addWidget(self.metrics)
        mode = getattr(pipeline.args, 'inference_mode', 'legacy')
        backend = 'embedded Qwen' if pipeline.args.vqa_backend == 'embedded' else 'external LLaVA'
        self.vqa_heading = QLabel(f'{mode} | {backend} | ConvLSTM memory')
        details_layout.addWidget(self.vqa_heading)
        self.vqa = QTextEdit()
        self.vqa.setReadOnly(True)
        self.vqa.setMaximumHeight(130)
        details_layout.addWidget(self.vqa)
        self.details.hide()
        details_button.toggled.connect(self.details.setVisible)
        layout.addWidget(self.details)
        raw_heading = QLabel('FULL RAW MODEL RESPONSE — latest completed inference')
        raw_heading.setStyleSheet('font-size:13px;font-weight:bold;color:#526173')
        layout.addWidget(raw_heading)
        self.raw_answer = QTextEdit()
        self.raw_answer.setReadOnly(True)
        self.raw_answer.setMinimumHeight(100)
        self.raw_answer.setStyleSheet('font-family:monospace;font-size:13px;background:#f6f8fa')
        layout.addWidget(self.raw_answer, 2)
        self.setCentralWidget(root)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(100)

    def _pause(self):
        self.pipeline.paused = not self.pipeline.paused

    def refresh(self):
        state = self.pipeline.snapshot()
        self.status.setText(state['status'])
        decision = state['decision']
        color = {'DATA_LOSS': '#606b78', 'OBSERVE': '#1473a8', 'CONTINUE': '#198754',
                 'DANGER_WARNING': '#e08a00', 'STOP': '#c62828'}.get(decision['action'], '#606b78')
        text = ('DATA LOSS — UNABLE TO ASSESS' if decision['action'] == 'DATA_LOSS'
                else decision['action'])
        self.decision.setText(text)
        self.decision.setStyleSheet(f'background:{color};color:white;font-size:20px;padding:8px')
        if state['frame'] is not None:
            self.original.setPixmap(to_pixmap(state['frame'], max(1, self.original.width() - 4), max(1, self.original.height() - 4)))
        if state['overlay'] is not None:
            overlay = state['overlay']
            self.overlay.setPixmap(to_pixmap(overlay, max(1, self.overlay.width() - 4), max(1, self.overlay.height() - 4)))
        metrics = dict(state.get('metrics', {}))
        clock_now=time.monotonic()
        evidence={'live_frame':(state['loop'],state['frame_index']),
                  'source_fps':state.get('source_fps'),'source_video_time_s':state.get('video_time_s'),
                  'max_result_age_s':getattr(self.pipeline.args, 'max_result_age_s', 8.0),
                  'segmentation_frame':state.get('segmentation_index'),
                  'vqa_frame':state.get('vqa_index')}
        for label,key in [('live','frame_at'),('segmentation','segmentation_evidence_at'),('vqa','vqa_evidence_at')]:
            stamp=state.get(key)
            evidence[label+'_age_s']=round(clock_now-stamp,3) if stamp is not None else None
        metrics['evidence']=evidence
        metrics.update(frame_index=state['frame_index'], video_loop=state['loop'],
                       target=state['segmentation_query'], vqa_model=state['vqa_model'],
                       vqa_latency_ms=state.get('vqa_latency_ms'))
        self.metrics.setText(json.dumps(metrics, ensure_ascii=False))
        vqa_health=state.get('vqa_health',decision)
        parsed = state.get('vqa_parsed') if vqa_health['data_available'] else None
        summary = monitor_summary(state, parsed, evidence)
        self.people.setText(summary['people'])
        self.latency.setText(summary['latency'])
        self.answer_summary.setText(summary['reason'])
        self.freshness.setText(summary['freshness'])
        raw = state.get('vqa_raw')
        raw_text = raw if isinstance(raw, str) else ('' if raw is None else str(raw))
        if not raw_text:
            raw_text = 'No model response yet.'
        if self.raw_answer.toPlainText() != raw_text:
            self.raw_answer.setPlainText(raw_text)
        report = {'decision': decision,
                  'metrics': metrics,
                  'evidence':evidence,
                  'vqa_health':state.get('vqa_health'),
                  'camera_geometry': state.get('camera_geometry'),
                  'yolo_hints_used_for_answer': state.get('vqa_yolo_hints'),
                  'parsed': parsed,
                  'raw_last_response_for_debug': state.get('vqa_raw'),
                  'error': state.get('vqa_error')}
        self.vqa.setPlainText(json.dumps(report, ensure_ascii=False, indent=2))

    def closeEvent(self, event):
        self.pipeline.stop()
        event.accept()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, required=True,
                        help='Complete temporal model bundle; weights are supplied separately')
    parser.add_argument('--video', type=Path, required=True)
    parser.add_argument('--query', help='Optional question; robot modes default to region-free robot VQA')
    parser.add_argument('--device', default='cuda:0')
    parser.add_argument('--cpu-threads',type=int,default=4,help='Bound CPU initialization/preprocessing threads alongside video decoding')
    parser.add_argument('--controller-url', default='http://127.0.0.1:11000')
    parser.add_argument('--model-name', default='')
    parser.add_argument('--vqa-backend', choices=['worker', 'embedded'], default='worker')
    parser.add_argument('--inference-mode', choices=['legacy', 'vqa-only', 'yolo-assisted'], default='legacy')
    parser.add_argument('--no-segmentation', action='store_true',
                        help='Embedded robot modes: skip mask/presence decoding and overlay; keep visual memory and VQA')
    parser.add_argument('--robot-prompt', choices=['compatible', 'compact'], default='compatible',
                        help='Training-compatible default; compact is an experimental shorter query')
    parser.add_argument('--robot-decoding', choices=['constrained', 'free'], default='constrained',
                        help='Robot modes: enforce consistent JSON during generation, or reproduce free decoding')
    parser.add_argument('--fast-motion-head',action='store_true',help='Experimental weak motion head controls the hint; Qwen still provides VQA')
    parser.add_argument('--fast-head-threshold',type=float,default=.7,help='Uncalibrated probability threshold; low scores give OBSERVE')
    parser.add_argument('--decision-policy', choices=['camera', 'model'], default='model',
                        help='camera: mask bbox image geometry overrides model action; model: legacy VQA hint')
    parser.add_argument('--camera-policy-config', type=Path,
                        help='Optional JSON thresholds/normalized image ROIs; defaults built into CameraPolicyConfig')
    parser.add_argument('--bbox-source', choices=['none', 'mask', 'yolo'], default='mask',
                        help='mask uses existing segmentation; yolo optionally detects complete person boxes')
    parser.add_argument('--bbox-model', type=Path, help='Existing local YOLO detection weights; never downloaded')
    parser.add_argument('--bbox-image-size', type=int, default=416)
    parser.add_argument('--max-new-tokens', type=int)
    parser.add_argument('--segmentation-hz', type=float, default=2.0)
    parser.add_argument('--vqa-hz', type=float, default=0.5)
    parser.add_argument('--display-fps', type=float, default=15.0)
    parser.add_argument('--max-frame-age-s', type=float, default=2.0)
    parser.add_argument('--max-result-age-s', type=float, default=8.0,
                        help='Maximum age of the input evidence, including VQA inference time')
    parser.add_argument('--exit-after-s', type=float, default=0.0,
                        help='Testing aid: close the UI automatically; 0 keeps running')
    parser.add_argument('--no-loop', dest='loop', action='store_false')
    args = parser.parse_args(argv)
    if args.no_segmentation and (args.vqa_backend != 'embedded' or args.inference_mode == 'legacy'):
        parser.error('--no-segmentation requires embedded vqa-only or yolo-assisted; worker baseline needs its mask')
    if args.cpu_threads<1:
        parser.error('cpu-threads must be positive')
    torch.set_num_threads(args.cpu_threads)
    if args.fast_motion_head and (args.inference_mode=='legacy' or args.vqa_backend!='embedded'):
        parser.error('fast-motion-head requires an embedded robot VQA mode')
    if not 0 < args.fast_head_threshold <= 1:
        parser.error('fast-head-threshold must be in (0,1]')
    if args.inference_mode != 'legacy':
        if args.vqa_backend != 'embedded':
            parser.error('Robot VQA modes use one embedded VQA bundle; worker is a separate legacy baseline')
        if args.decision_policy != 'model' or args.camera_policy_config:
            parser.error('Robot VQA modes do not use camera ROI policies/configs; final action comes from VQA')
        if args.inference_mode == 'vqa-only' and (args.bbox_source == 'yolo' or args.bbox_model):
            parser.error('vqa-only does not load YOLO; use yolo-assisted to supply detector measurements')
        args.bbox_source = 'yolo' if args.inference_mode == 'yolo-assisted' else 'none'
    if args.query is None:
        robot_query = COMPACT_ROBOT_QUERY if args.robot_prompt == 'compact' else ROBOT_QUERY
        args.query = robot_query if args.inference_mode != 'legacy' else 'the people'
    if args.max_new_tokens is None:
        args.max_new_tokens = 192 if args.inference_mode != 'legacy' else 64
    if args.bbox_source == 'yolo' and (not args.bbox_model or not args.bbox_model.is_file()):
        parser.error('--bbox-source yolo requires --bbox-model pointing to existing local weights')
    if args.inference_mode == 'legacy':
        try:
            CameraPolicyConfig.load(args.camera_policy_config)
        except (ValueError, TypeError, OSError) as exc:
            parser.error(f'Invalid camera policy config: {exc}')
    if (min(args.segmentation_hz, args.vqa_hz, args.display_fps, args.max_new_tokens,
            args.max_frame_age_s, args.max_result_age_s, args.bbox_image_size) <= 0
            or args.exit_after_s < 0):
        parser.error('All rates must be positive')
    for path in (args.checkpoint / 'manifest.json', args.checkpoint / 'model.pt', args.video):
        if not path.exists():
            parser.error(f'Path does not exist: {path}')
    app = QApplication(sys.argv[:1])
    pipeline = LivePipeline(args)
    window = Monitor(pipeline)
    pipeline.start()
    window.show()
    if args.exit_after_s:
        QTimer.singleShot(round(args.exit_after_s * 1000), window.close)
    code = app.exec_()
    final = pipeline.snapshot()
    print(json.dumps({key: value for key, value in final.items()
                      if key not in {'frame', 'overlay', 'segmentation_frame'}}, ensure_ascii=False, indent=2),
          flush=True)
    return code


if __name__ == '__main__':
    main()
