#!/usr/bin/env python3
"""Separate transport/expiry, JSON/enum and semantic schema errors, read-only."""
import argparse
from collections import Counter
import json
from pathlib import Path
import time
import rclpy
from rclpy.node import Node
from retail_interfaces.msg import RiskAdvisory
from std_msgs.msg import String
from lightweight_risk_vqa.robot_vqa import robot_schema_error

parser=argparse.ArgumentParser();parser.add_argument('--seconds',type=float,default=45)
parser.add_argument('--output',default='/tmp/vqa_outputs.json')
args=parser.parse_args();rclpy.init();node=Node('audit_vqa_outputs');records={};health=Counter();latest=[None]
def key(source,stamp):return f'{source}|{stamp:.9f}'
def risk(msg):
    latest[0]=msg
    at=msg.header.stamp.sec+msg.header.stamp.nanosec/1e9
    record=records.setdefault(key(msg.source_id,at),{})
    record.setdefault('first_action',msg.action);record['reason']=msg.reason
    record.setdefault('statuses',[])
    state=f'{msg.action}:{msg.valid}'
    if state not in record['statuses']:record['statuses'].append(state)
def details(msg):
    value=json.loads(msg.data);stamp=value.get('stamp',0)
    raw=value.get('raw','');parsed=None;json_error=''
    if raw:
        try:parsed=json.loads(raw)
        except ValueError as exc:json_error=str(exc)
    records.setdefault(key(value.get('source_id',''),stamp),{}).update(
        raw=raw,parsed=parsed,json_error=json_error,non_ascii=any(ord(c)>127 for c in raw),
        schema_error=robot_schema_error(parsed) if parsed is not None else 'no parsed JSON',
        inference_ms=value.get('inference_ms'),model_action=value.get('raw_action',value.get('action')),
        request_ms=value.get('request_ms'),generation_engine=value.get('generation_engine'),decoding_profile=value.get('decoding_profile'),
        source_id=value.get('source_id'),stamp=stamp)
def sample():
    msg=latest[0]
    if not msg:health['waiting']+=1;return
    age=node.get_clock().now().nanoseconds/1e9-msg.header.stamp.sec-msg.header.stamp.nanosec/1e9
    healthy=msg.valid and 0<=age<=2
    health['valid' if healthy else (msg.reason or 'capture expired before next adapter tick')]+=1
node.create_subscription(RiskAdvisory,'/perception/vqa/chest',risk,10)
node.create_subscription(String,'/perception/vqa/chest/details',details,10)
node.create_timer(.05,sample);end=time.monotonic()+args.seconds
while time.monotonic()<end:rclpy.spin_once(node,timeout_sec=.05)
completed=[v for v in records.values() if v.get('raw')]
summary=dict(seconds=args.seconds,unique_model_results=len(completed),
    model_actions=dict(Counter(v['model_action'] for v in completed)),
    json_errors=sum(bool(v['json_error']) for v in completed),non_ascii_outputs=sum(v['non_ascii'] for v in completed),
    schema_errors=dict(Counter(v['schema_error'] for v in completed if v['schema_error'])),sampled_health=dict(health))
output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
output.write_text(json.dumps(dict(summary=summary,results=list(records.values())),ensure_ascii=False,indent=2)+'\n')
print(json.dumps(summary,ensure_ascii=False,indent=2))
node.destroy_node();rclpy.shutdown()
