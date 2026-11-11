"""Headless adapter to the pinned ConvLSTM/Qwen graph and constrained decoder."""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time
import traceback
import uuid

import cv2
import numpy as np
import torch
from transformers import StoppingCriteriaList, LogitsProcessorList
from light_foundation_vlm.checkpoint import load_bundle
from lightweight_risk_vqa.robot_vqa import (ROBOT_QUERY, robot_schema_error, StopAfterJsonObject,
    json_object_end, PersonMotionHints, query_with_hints)
from lightweight_risk_vqa.camera_policy import PersonBoxDetector
from lightweight_risk_vqa.robot_decoding import RobotJsonDecoder


@torch.inference_mode()
def generate_grammar_cached(language_model,inputs,attention,max_tokens,processor):
    """Batch grammar-forced runs into KV-cache updates; do not alter free logits."""
    if inputs.shape[0]!=1:raise ValueError('Grammar cached decoding supports batch one')
    config=language_model.generation_config
    if config.num_beams not in (None,1) or config.repetition_penalty not in (None,1.0) or config.no_repeat_ngram_size:
        raise ValueError('Unsupported generation options for exact greedy comparison')
    output=language_model(inputs_embeds=inputs,attention_mask=attention,use_cache=True,return_dict=True,logits_to_keep=1)
    cache=output.past_key_values;scores=output.logits[:,-1,:];tokens=[]
    def ids():return torch.tensor([tokens],dtype=torch.long,device=inputs.device)
    while len(tokens)<max_tokens:
        masked=processor(ids(),scores)
        if processor.phase=='done':break
        tokens.append(int(masked.argmax(dim=-1).item()));pending=[tokens[-1]]
        while len(tokens)<max_tokens:
            masked=processor(ids(),scores)
            if processor.phase=='done':break
            top=torch.topk(masked,2,dim=-1)
            if not torch.isneginf(top.values[0,1]).item():break
            token=int(top.indices[0,0].item());tokens.append(token);pending.append(token)
        if processor.phase=='done' or len(tokens)>=max_tokens:break
        mask=torch.ones((1,inputs.shape[1]+len(tokens)),dtype=attention.dtype,device=inputs.device)
        output=language_model(input_ids=torch.tensor([pending],dtype=torch.long,device=inputs.device),
            attention_mask=mask,past_key_values=cache,use_cache=True,return_dict=True,logits_to_keep=1)
        cache=output.past_key_values;scores=output.logits[:,-1,:]
    return ids()


class Backend:
    def __init__(self, checkpoint, device='cuda:0'):
        self.device = torch.device(device)
        dtype = torch.bfloat16 if self.device.type == 'cuda' else torch.float32
        self.model,self.tokenizer,self.manifest = load_bundle(checkpoint,dtype)
        if not self.model.use_temporal:
            raise ValueError('Temporal checkpoint required')
        self.model.to(self.device).eval()
        self.states = {}
        self.detector = None
        self.hints = {}
        self.worker_id = uuid.uuid4().hex
        sha = hashlib.sha256()
        with open(Path(checkpoint)/'model.pt','rb') as f:
            for block in iter(lambda:f.read(4*1024*1024),b''):
                sha.update(block)
        self.model_id = sha.hexdigest()
        self.prompt_id = hashlib.sha256(ROBOT_QUERY.encode()).hexdigest()
        self.decoder = RobotJsonDecoder(self.tokenizer)
        self.decoders = {'baseline':self.decoder}
        prompt_file=os.environ.get('VQA_PROMPT_FILE')
        self.prompt=Path(prompt_file).read_text() if prompt_file else ROBOT_QUERY
        if self.prompt!=ROBOT_QUERY:
            raise ValueError('Project prompt differs from the validated checkpoint-compatible prompt; evaluate a separate research profile first')
        self.query = self.ids(self.prompt)
        self.mask_query = self.ids('the people')
        if (self.mask_query == self.model.config.seg_token_id).sum().item() != 1:
            raise RuntimeError('VQA segmentation token check failed on the inference device')
        self.lock = threading.Lock()

    def ids(self,query):
        marker = self.model.config.seg_token_id
        tokens = self.tokenizer.encode(query+'\n<seg>\n', add_special_tokens=False)
        if marker not in tokens:
            # Some tokenizer builds split the marker when encoding adjacent text.
            tokens = (
                self.tokenizer.encode(query+'\n', add_special_tokens=False)
                + [marker]
                + self.tokenizer.encode('\n', add_special_tokens=False)
            )
        if tokens.count(marker) != 1:
            raise ValueError('VQA prompt must encode exactly one checkpoint <seg> token')
        return torch.tensor(tokens, dtype=torch.long, device=self.device)[None]

    @staticmethod
    def preprocess(frame):
        rgb = cv2.cvtColor(cv2.resize(frame,(320,320)),cv2.COLOR_BGR2RGB)
        tensor = torch.from_numpy(rgb.copy()).permute(2,0,1).float().div(255)
        mean = tensor.new_tensor((0.485,0.456,0.406))[:,None,None]
        std = tensor.new_tensor((0.229,0.224,0.225))[:,None,None]
        return ((tensor-mean)/std).contiguous()

    @torch.inference_mode()
    def infer(self,data):
        request_started=time.monotonic()
        camera,stamp,epoch = data['camera_id'],float(data['stamp']),data['source_epoch']
        mode = data.get('inference_mode','vqa-only')
        decoding_profile=data.get('decoding_profile',os.environ.get('VQA_DECODING_PROFILE','baseline'))
        engine=data.get('generation_engine',os.environ.get('VQA_GENERATION_ENGINE','hf'))
        if engine not in ('hf','grammar_cached'):raise ValueError('Unknown generation engine')
        if decoding_profile not in ('baseline','sentence_safe'):raise ValueError('Unknown decoding profile')
        if decoding_profile not in self.decoders:
            # Restrict narrative tokens only. JSON enum grammar, risk/action
            # coupling, model logits and semantic schema checks are unchanged.
            decoder=RobotJsonDecoder(self.tokenizer)
            strings=self.tokenizer.batch_decode([[i] for i in decoder.text_ids],skip_special_tokens=False)
            decoder.text_ids=[i for i,value in zip(decoder.text_ids,strings)
                              if all(c.isascii() and (c.isalnum() or c in ' .,!?-') for c in value)]
            self.decoders[decoding_profile]=decoder
        decoder=self.decoders[decoding_profile]
        if mode not in ('vqa-only','yolo-assisted'):
            raise ValueError('Unknown inference mode')
        if camera not in ('chest','left_shoulder','right_shoulder','left_wrist','tidybot'):
            raise ValueError('Unknown camera ID')
        frame = cv2.imdecode(np.frombuffer(base64.b64decode(data['jpeg'],validate=True),np.uint8),cv2.IMREAD_COLOR)
        if frame is None or frame.shape[0]*frame.shape[1]>4096*4096:
            raise ValueError('Invalid/oversized image')
        old = self.states.get(camera)
        state = None
        continuity = bool(old and old[0] == epoch and old[3] == mode and old[4] == decoding_profile and old[5] == engine and 0 < stamp-old[1] <= self.manifest['config']['max_gap_s'])
        if continuity:
            state = old[2]
        started = time.monotonic()
        boxes,hints,detector_ms = [],None,None
        query = self.query
        if mode == 'yolo-assisted':
            if self.detector is None:
                self.detector = PersonBoxDetector(os.environ.get('YOLO_WEIGHTS','/models/yolov8n.pt'),str(self.device),416)
            if not continuity:self.hints[camera] = PersonMotionHints()
            detector_started = time.monotonic()
            boxes = self.detector.detect(frame)
            detector_ms = round((time.monotonic()-detector_started)*1000,3)
            hints = self.hints[camera].update(boxes,stamp)
            query = self.ids(query_with_hints(self.prompt,hints))
        vision_started = time.monotonic()
        attention = torch.ones_like(self.mask_query)
        output = self.model(self.preprocess(frame)[None,None].to(self.device),self.mask_query,
                            attention_mask=attention,frame_valid=torch.ones((1,1),dtype=torch.bool,device=self.device),
                            dt=torch.ones((1,1),device=self.device),state=state,
                            return_language_context=True,compute_masks=False)
        if self.device.type=='cuda':torch.cuda.synchronize()
        visual_ms = round((time.monotonic()-vision_started)*1000,3)
        self.states[camera] = (epoch,stamp,tuple(v.detach() for v in output['state']),mode,decoding_profile,engine)
        count = output['visual_token_count']
        text = self.model.language_model.get_input_embeddings()(query)
        text = torch.where(query.eq(self.model.config.seg_token_id)[...,None],
                           self.model.seg_embedding.to(text),text)
        inputs = torch.cat([output['language_inputs'][:,:count],text],dim=1)
        attention = torch.ones(inputs.shape[:2],dtype=torch.long,device=self.device)
        if engine=='grammar_cached':
            generated=generate_grammar_cached(self.model.language_model,inputs,attention,192,decoder.processor())
        else:
            generated = self.model.generate_from_context(
                inputs,attention,192,
                stopping_criteria=StoppingCriteriaList([StopAfterJsonObject(self.tokenizer)]),
                logits_processor=LogitsProcessorList([decoder.processor()]))
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
                      decoding='constrained',camera_id=camera,stamp=stamp,inference_mode=mode,
                      decoding_profile=decoding_profile,schema_error=error,
                      generation_engine=engine,
                      generation_tokens=int(generated.shape[-1]),
                      request_ms=round((time.monotonic()-request_started)*1000,2),
                      yolo_hints=hints,metrics=dict(inference_mode=mode,segmentation_enabled=False,
                          bbox_source='yolo' if mode=='yolo-assisted' else 'none',person_boxes=boxes,
                          bbox_detector_ms=detector_ms,visual_memory_ms=visual_ms))
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
                               prompt_id=BACKEND.prompt_id,torch=torch.__version__,
                               default_decoding_profile=os.environ.get('VQA_DECODING_PROFILE','baseline'),
                               default_generation_engine=os.environ.get('VQA_GENERATION_ENGINE','hf')))

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
            print(json.dumps({k:result[k] for k in ('camera_id','stamp','action','raw_action','valid','reason','inference_ms','generation_tokens','decoding_profile','generation_engine','model_id')}),flush=True)
            self.send_json(200,result)
        except Exception as exc:
            traceback.print_exc()
            self.send_json(500,{'error':str(exc)})
        finally:
            BACKEND.lock.release()


if __name__ == '__main__':
    torch.set_num_threads(2)
    device = torch.device(os.environ.get('VQA_DEVICE', 'cuda:0'))
    if device.type == 'cuda':
        # A visible GPU can still lack executable kernels for its architecture.
        print(json.dumps(dict(cuda_capability=torch.cuda.get_device_capability(device),
                              cuda_arches=torch.cuda.get_arch_list())), flush=True)
        if torch.arange(4, device=device).sum().item() != 6:
            raise RuntimeError('CUDA startup computation returned an incorrect result')
    BACKEND = Backend(os.environ.get('VQA_CHECKPOINT', '/models/vqa'), device)
    print(json.dumps(dict(ready=True,model_id=BACKEND.model_id,prompt_id=BACKEND.prompt_id,
                         worker_id=BACKEND.worker_id)),flush=True)
    ThreadingHTTPServer(('0.0.0.0',8090),Handler).serve_forever()

