"""Reuse the existing monitor, relabeling its legacy SAM3 status field."""
import sys
import threading
import signal
import json

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication, QLabel
import rclpy
from rclpy.executors import ExternalShutdownException, SingleThreadedExecutor
from rclpy.utilities import remove_ros_args

from future_risk_vqa.research_gui import ResearchGuiNode, ResearchWindow, Signals
from .direct_temporal import camera_summary


class LightweightGuiNode(ResearchGuiNode):
    def _json(self, name, data):
        if name != 'semantics':
            return super()._json(name, data)
        try:
            report = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return super()._json(name, data)
        if not isinstance(report, dict) or report.get('metadata', {}).get('analysis_mode') != 'direct_temporal':
            result = super()._json(name, data)
            if isinstance(report, dict) and report.get('metadata', {}).get('configured_storyboard_mode') == 'current_crop':
                metadata = report['metadata']
                fusion = metadata.get('temporal_fusion', {})
                self.signals.evidence_timing.emit(
                    'VQA EVIDENCE (not live): one NOW crop per person; '
                    f'LSTM directions applied={fusion.get("applied_count", 0)}; '
                    f'pipeline={float(metadata.get("pipeline_ms", 0.0)) / 1000.0:.2f}s. '
                    'The LLM receives no OLD or future image.')
            return result
        self.latest_semantics = report
        left, left_level = camera_summary(report, 'left')
        right, right_level = camera_summary(report, 'right')
        self.signals.behaviors.emit(left, left_level, right, right_level)
        model = report['metadata']['motion_source']
        self.signals.evidence_timing.emit(
            f'LIVE TEMPORAL — {model.upper()} | state={report["availability"]} | LLM DISABLED. '
            'Direction from per-ID bbox history; posture/activity are not inferred.')
        self.signals.json_update.emit(name, json.dumps(report, indent=2))

    def _decision(self, message):
        try:
            report = json.loads(message.data)
        except (ValueError, TypeError):
            return super()._decision(message)
        if not isinstance(report, dict) or report.get('decision_mode') != 'direct_temporal':
            return super()._decision(message)
        detail = (f'{report["explanation"]}\n\n'
            f'Model: {report.get("prediction_source") or "waiting"} | LLM: disabled\n'
            f'Tracking age: {report.get("geometry_age_s")}s\n'
            f'Reason: {report["reason_code"]}\n'
            f'Critical IDs: {", ".join(report.get("critical_tracks", [])) or "--"}')
        self.signals.decision.emit(report['action'], detail, json.dumps(report, indent=2))


class LightweightWindow(ResearchWindow):
    COLORS = {**ResearchWindow.COLORS, 'DATA_LOSS': '#606b78'}
    ACTION_TEXT = {**ResearchWindow.ACTION_TEXT, 'DATA_LOSS': 'DATA LOSS — UNABLE TO ASSESS'}

    def __init__(self, node, signals, shutdown):
        super().__init__(node, signals, shutdown)
        preview_only = node.get_parameter('preview_only').value
        direct = node.get_parameter('direct_temporal').value
        single_now = node.get_parameter('single_now_lstm').value
        for label in self.findChildren(QLabel):
            text = label.text().replace('SAM3', 'YOLO+ByteTrack')
            if preview_only:
                text = text.replace('TIMESTAMPED VQA EVIDENCE', 'LIVE DETECTION PREVIEW')
            if direct:
                if text.startswith('YOLO+ByteTrack IDS'):
                    text = 'YOLO+ByteTrack IDs -> BBOX HISTORY -> TEMPORAL DIRECTION (NO LLM)'
                text = text.replace('Future Behavior VQA - Research Monitor', 'Direct Temporal Motion - Research Monitor')
                text = text.replace('PER-CAMERA CURRENT OBSERVATION + FUTURE PREDICTION', 'PER-CAMERA TEMPORAL DIRECTION (NO LLM)')
                text = text.replace('VLM INPUT USED BY THE COMPLETED VQA INFERENCE (SEE THE ACTUAL MODE AND TIMING ABOVE)',
                                    'TEMPORAL INPUT: PER-ID BBOX HISTORY; NO IMAGE IS SENT TO AN LLM')
                text = text.replace('VQA', 'temporal')
            elif single_now:
                text = text.replace('YOLO+ByteTrack IDS -> OLD/MID/NOW -> CURRENT STATE -> FUTURE PREDICTION',
                                    'YOLO+ByteTrack IDs -> LSTM HISTORY -> ONE NOW IMAGE -> VQA')
                text = text.replace('VLM INPUT USED BY THE COMPLETED VQA INFERENCE (SEE THE ACTUAL MODE AND TIMING ABOVE)',
                                    'VLM INPUT: ONE NOW CROP PER PERSON; LSTM HISTORY IS TEXT CONTEXT')
            label.setText(text)
        if preview_only:
            self.evidence_timing.setText('DETECTION PREVIEW: VQA disabled; no LLM behavior assessment. Action is OBSERVE.')
        if direct:
            self.evidence_timing.setText('DIRECT TEMPORAL: waiting for per-person history; LLM disabled. Rules are explicitly labelled, not presented as LSTM.')
            self.storyboard.setText('Temporal input: 16 causal bbox feature vectors per ID.\nNo LLM/storyboard inference in this mode.')

    def _status(self, name, data):
        # The reused window keeps SAM3 as an internal dictionary key.
        super()._status(name, data)
        self.status_label.setText(self.status_label.text().replace('SAM3', 'YOLO+ByteTrack'))
        if self.node.get_parameter('direct_temporal').value:
            self.status_label.setText(self.status_label.text().replace('VQA:', 'TEMPORAL:'))


def main(args=None):
    ros_args = args if args is not None else sys.argv
    rclpy.init(args=ros_args)
    app = QApplication(remove_ros_args(args=ros_args))
    # Let Python process Unix signals while Qt owns the main event loop.
    heartbeat = QTimer()
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start(100)
    previous_handlers = {signum: signal.signal(signum, lambda *_: app.quit())
                         for signum in (signal.SIGINT, signal.SIGTERM)}
    signals = Signals()
    node = LightweightGuiNode(signals)
    node.declare_parameter('preview_only', False)
    node.declare_parameter('direct_temporal', False)
    node.declare_parameter('single_now_lstm', False)
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    stopped = threading.Event()

    def spin():
        try:
            executor.spin()
        except (KeyboardInterrupt, ExternalShutdownException):
            pass

    def shutdown():
        if stopped.is_set():
            return
        stopped.set()
        executor.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

    thread = threading.Thread(target=spin, daemon=True)
    thread.start()
    window = LightweightWindow(node, signals, shutdown)
    window.setWindowTitle('Lightweight Risk — direct temporal (LLM disabled)' if node.get_parameter('direct_temporal').value
                          else 'Lightweight Risk — YOLO + ByteTrack / VQA')
    window.show()
    try:
        return app.exec_()
    finally:
        shutdown()
        thread.join(timeout=2)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
