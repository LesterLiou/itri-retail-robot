"""Optional worker using the unmodified social-safety-decision MASt3R adapter.

Requires a separately validated CUDA runtime and MASt3R source/weights.
The HTTP adapter has a deadline; a hung worker cannot block a local motion gate.
"""
import base64
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
import threading
import time
import cv2
import numpy as np
from realtime_safety.config import ReconstructionConfig
from realtime_safety.pipeline.mast3r_slam_adapter import Mast3rSlamAdapter
from realtime_safety.types import FramePacket

config = ReconstructionConfig(
    mast3r_slam_path=os.environ['MAST3R_SOURCE_DIR'],
    mast3r_slam_python=os.environ['MAST3R_PYTHON'],
    mast3r_slam_checkpoint=os.environ['MAST3R_CHECKPOINT'],
    mast3r_slam_retrieval_checkpoint=os.environ.get('MAST3R_RETRIEVAL_CHECKPOINT'),
    max_points=30000)
adapter = Mast3rSlamAdapter(config)
adapter.load()  # Missing dependencies cause a visible startup failure.
lock = threading.Lock()
index = 0


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        global index
        size = int(self.headers.get('Content-Length','0'))
        if not 0<size<8_000_000 or not lock.acquire(blocking=False):
            self.send_error(429)
            return
        try:
            data = json.loads(self.rfile.read(size))
            bgr = cv2.imdecode(np.frombuffer(base64.b64decode(data['jpeg']),np.uint8),cv2.IMREAD_COLOR)
            if bgr is None:
                raise ValueError('bad image')
            stamp = float(data['stamp'])
            frame = FramePacket(index,stamp,time.time(),bgr,cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB),0.0,bgr.shape[1],bgr.shape[0])
            index += 1
            cloud = adapter.infer(frame)
            points = cloud.points[np.isfinite(cloud.points).all(axis=1)]
            raw = json.dumps({'points':points.tolist(),'stamp':stamp,'frame':'mast3r_map',
                              'axes':'x-right/y-forward/z-up','metric_verified':False}).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except Exception as exc:
            self.send_error(500,str(exc))
        finally:
            lock.release()


ThreadingHTTPServer(('0.0.0.0',8091),Handler).serve_forever()

