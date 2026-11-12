"""Monitor view ported from VLMotion_VQA 889558ff (light_foundation_live_ui).

Only the Qt view is reused here; RosPipeline supplies timestamped ROS observations.
Original model, prompt, schema and detector remain in the pinned upstream package.
"""
import json
import math
import time
import os
from PyQt5.QtCore import QLibraryInfo
import cv2
os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = QLibraryInfo.location(QLibraryInfo.PluginsPath)
if '/cv2/qt/fonts' in os.environ.get('QT_QPA_FONTDIR',''):
    os.environ.pop('QT_QPA_FONTDIR')
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (QApplication,QHBoxLayout,QLabel,QLineEdit,QMainWindow,
 QPushButton,QTextEdit,QVBoxLayout,QWidget,QFrame,QSizePolicy)
from .policy import ADVISORY_SPEED_PERCENT

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
        if parsed.get('policy_reason'):
            reason += f"\nWHY (policy): {compact(parsed['policy_reason'])}"
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
        self.decision = card('VQA ADVISORY')
        self.people = card('PEOPLE DETECTED')
        self.latency = card('MODEL LATENCY')
        layout.addLayout(cards)
        self.history = QLabel('Last valid decision: waiting')
        self.history.setWordWrap(True)
        self.history.setStyleSheet('font-size:14px;padding:5px;background:#fff2ce;color:#503800')
        layout.addWidget(self.history)
        self.health = QLabel('DATA HEALTH: waiting')
        self.health.setWordWrap(True)
        self.health.setStyleSheet('font-size:13px;padding:4px')
        layout.addWidget(self.health)
        choices=QHBoxLayout()
        self.action_choices={}
        for action in ('STOP','DANGER_WARNING','CONTINUE','OBSERVE'):
            label=QLabel(action+'\n—')
            label.setAlignment(Qt.AlignCenter);label.setMinimumWidth(120)
            choices.addWidget(label,1);self.action_choices[action]=label
        layout.addLayout(choices)
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
        pause = QPushButton('Pause / Resume display')
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
        self.query.setReadOnly(True)
        prompt_row.addWidget(self.query)
        apply_button = QPushButton('Training-compatible prompt (fixed)')
        apply_button.setEnabled(False)
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
        history=state.get('last_valid')
        if history:
            self.history.setText(f"Last valid: {history['action']} · {history['age_sec']:.1f}s ago · HISTORY ONLY, no motion permission"+
                ('' if history['same_source'] else ' · previous source'))
            if decision['action']=='DATA_LOSS':
                text=f"LAST VALID: {history['action']} (history only)\nCURRENT: DATA_LOSS — motion denied · 0%"
        valid=bool(decision.get('data_available'))
        advisory = decision['action'] if valid else 'OBSERVE'
        parsed = state.get('vqa_parsed') if valid else None
        source = 'POLICY SELECTED' if isinstance(parsed, dict) and parsed.get('policy_reason') else 'MODEL SELECTED'
        age=state.get('capture_age_sec',float('inf'))
        age_text=f'{age:.2f}s' if math.isfinite(age) else 'unavailable'
        self.health.setText(f"DATA HEALTH: {'VALID' if valid else 'DATA_LOSS'} · capture age {age_text} · {state.get('health_reason','')} · confidence percentages unavailable")
        self.health.setStyleSheet('font-size:13px;padding:4px;color:'+('#126b35' if valid else '#9d3518'))
        for action,label in self.action_choices.items():
            current=advisory==action
            retained=not valid and history and history['action']==action
            if current and action == 'OBSERVE':
                tag = 'MOTION DENIED · 0% OUTPUT'
            else:
                tag=source if current else ('HISTORY ONLY' if retained else '—')
            background=({'STOP':'#c62828','DANGER_WARNING':'#c17a00','CONTINUE':'#198754','OBSERVE':'#1473a8'}[action] if current else ('#fff2ce' if retained else '#edf2f6'))
            label.setText(f'{action} · {ADVISORY_SPEED_PERCENT[action]}% PROFILE\n{tag}')
            label.setStyleSheet(f'background:{background};color:'+('white' if current else '#384654')+';font-size:13px;padding:6px;border-radius:5px')
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

