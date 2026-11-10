"""Headless adapter to the pinned ConvLSTM/Qwen graph and constrained decoder."""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time
import uuid

import cv2
import numpy as np
import torch
from transformers import StoppingCriteriaList, LogitsProcessorList
from light_foundation_vlm.checkpoint import load_bundle
from lightweight_risk_vqa.robot_vqa import ROBOT_QUERY, robot_schema_error, StopAfterJsonObject, json_object_end
from lightweight_risk_vqa.robot_decoding import RobotJsonDecoder


class Backend:
    def __init__(self, checkpoint, device='cuda:0'):
        self.device = torch.device(device)
        dtype = torch.bfloat16 if self.device.type == 'cuda' else torch.float32
        self.model,self.tokenizer,self.manifest = load_bundle(checkpoint,dtype)
        if not self.model.use_temporal:
            raise ValueError('Temporal checkpoint required')
        self.model.to(self.device).eval()
        self.states = {}
        self.worker_id = uuid.uuid4().hex
        sha = hashlib.sha256()
        with open(Path(checkpoint)/'model.pt','rb') as f:
            for block in iter(lambda:f.read(4*1024*1024),b''):
                sha.update(block)
        self.model_id = sha.hexdigest()
        self.prompt_id = hashlib.sha256(ROBOT_QUERY.encode()).hexdigest()
        self.decoder = RobotJsonDecoder(self.tokenizer)
        self.query = self.ids(ROBOT_QUERY)
        self.mask_query = self.ids('the people')
        self.lock = threading.Lock()

    def ids(self,query):
        return torch.tensor(self.tokenizer.encode(query+'\n<seg>\n',add_special_tokens=False),
                            dtype=torch.long,device=self.device)[None]

    @staticmethod
    def preprocess(frame):
        rgb = cv2.cvtColor(cv2.resize(frame,(320,320)),cv2.COLOR_BGR2RGB)
        tensor = torch.from_numpy(rgb.copy()).permute(2,0,1).float().div(255)
        mean = tensor.new_tensor((0.485,0.456,0.406))[:,None,None]
        std = tensor.new_tensor((0.229,0.224,0.225))[:,None,None]
        return ((tensor-mean)/std).contiguous()

    @torch.inference_mode()
    def infer(self,data):
        camera,stamp,epoch = data['camera_id'],float(data['stamp']),data['source_epoch']
        if camera not in ('chest','left_shoulder','right_shoulder','left_wrist'):
            raise ValueError('Unknown camera ID')
        frame = cv2.imdecode(np.frombuffer(base64.b64decode(data['jpeg'],validate=True),np.uint8),cv2.IMREAD_COLOR)
        if frame is None or frame.shape[0]*frame.shape[1]>4096*4096:
            raise ValueError('Invalid/oversized image')
        old = self.states.get(camera)
        state = None
        if old and old[0] == epoch and 0 < stamp-old[1] <= self.manifest['config']['max_gap_s']:
            state = old[2]
        started = time.monotonic()
        attention = torch.ones_like(self.mask_query)
        output = self.model(self.preprocess(frame)[None,None].to(self.device),self.mask_query,
                            attention_mask=attention,frame_valid=torch.ones((1,1),dtype=torch.bool,device=self.device),
                            dt=torch.ones((1,1),device=self.device),state=state,
                            return_language_context=True,compute_masks=False)
        self.states[camera] = (epoch,stamp,tuple(v.detach() for v in output['state']))
        count = output['visual_token_count']
        text = self.model.language_model.get_input_embeddings()(self.query)
        text = torch.where(self.query.eq(self.model.config.seg_token_id)[...,None],
                           self.model.seg_embedding.to(text),text)
        inputs = torch.cat([output['language_inputs'][:,:count],text],dim=1)
        attention = torch.ones(inputs.shape[:2],dtype=torch.long,device=self.device)
        generated = self.model.generate_from_context(
            inputs,attention,192,
            stopping_criteria=StoppingCriteriaList([StopAfterJsonObject(self.tokenizer)]),
            logits_processor=LogitsProcessorList([self.decoder.processor()]))
        raw = self.tokenizer.decode(generated[0],skip_special_tokens=True).strip()
        end = json_object_end(raw)
        try:
            parsed = json.loads(raw[:end] if end is not None else raw)
        except json.JSONDecodeError:
            parsed = None
        error = robot_schema_error(parsed)
        result = dict(parsed) if isinstance(parsed,dict) else {}
        result.update(raw=raw,raw_action=result.get('action',''),valid=not bool(error),
                      reason=error,model_id=self.model_id,prompt_id=self.prompt_id,worker_id=self.worker_id,
                      inference_ms=round((time.monotonic()-started)*1000,2),
                      decoding='constrained',camera_id=camera,stamp=stamp)
        if error:
            result['action'] = 'DATA_LOSS'
        return result


BACKEND = None


class Handler(BaseHTTPRequestHandler):
    def send_json(self,status,value):
        raw = json.dumps(value,allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.send_json(200,dict(ready=True,worker_id=BACKEND.worker_id,model_id=BACKEND.model_id,
                               prompt_id=BACKEND.prompt_id,torch=torch.__version__))

    def do_POST(self):
        if self.path != '/infer':
            self.send_json(404,{'error':'Unknown endpoint'})
            return
        size = int(self.headers.get('Content-Length','0'))
        if not (0 < size < 8_000_000):
            self.send_json(413,{'error':'Invalid request size'})
            return
        data = json.loads(self.rfile.read(size))
        if not BACKEND.lock.acquire(blocking=False):
            self.send_json(429,{'error':'Inference busy; drop frame'})
            return
        try:
            result = BACKEND.infer(data)
            print(json.dumps({k:result[k] for k in ('camera_id','stamp','action','valid','inference_ms','model_id')}),flush=True)
            self.send_json(200,result)
        except Exception as exc:
            self.send_json(500,{'error':str(exc)})
        finally:
            BACKEND.lock.release()


if __name__ == '__main__':
    torch.set_num_threads(2)
    BACKEND = Backend(os.environ.get('VQA_CHECKPOINT','/models/vqa'),os.environ.get('VQA_DEVICE','cuda:0'))
    print(json.dumps(dict(ready=True,model_id=BACKEND.model_id,prompt_id=BACKEND.prompt_id,
                         worker_id=BACKEND.worker_id)),flush=True)
    ThreadingHTTPServer(('0.0.0.0',8090),Handler).serve_forever()

