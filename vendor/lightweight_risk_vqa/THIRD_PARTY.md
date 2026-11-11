# Source and asset provenance

The `light_foundation_vlm/` model core was consolidated from this workspace's
`light_foundation_vlm_deployment_v1/source/light_foundation_vlm/` on 2026-10-22.
Only the maintained model, training, data loading, download, smoke and prediction
modules are included. The original HPC launchers, wheelhouse and model weights
are not part of this source distribution. Existing checkpoint tensor keys and
bundle format remain compatible.

`lightweight_risk_vqa/worker_client.py` retains the VLMotion controller/worker
HTTP protocol from `future_risk_vqa/model_client.py`, plus its response-object
parser. Both embedded and worker UI modes can run without importing ROS packages.
ROS launch/adapters still require the sibling `future_risk_vqa` package.

Third-party assets are obtained separately and retain their original terms:

| Component | Upstream |
|---|---|
| Qwen3 | https://huggingface.co/Qwen/Qwen3-0.6B |
| Offline Qwen3-VL teacher (pinned revision recorded in data metadata) | https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct |
| MobileNetV3 | https://pytorch.org/vision/stable/models/mobilenetv3.html |
| gRefCOCO | https://github.com/henghuiding/gRefCOCO |
| COCO human person annotations/images | https://cocodataset.org/ |
| MeViS | https://github.com/henghuiding/MeViS |
| JAAD | https://github.com/ykotseruba/JAAD |
| SAM3 offline mask teacher | https://github.com/facebookresearch/sam3 |
| Ultralytics / ByteTrack integration | https://github.com/ultralytics/ultralytics |

The existing ROS metadata declares MIT for the package. The imported research
deployment did not include a separate source license; this file records its
origin and does not grant a new license for that code, checkpoints or datasets.
In particular, the original temporal checkpoints include MeViS training, whose
dataset terms restrict commercial use. Keep upstream notices with redistributed
assets; no weights, credentials, recordings or dataset contents are bundled here.

The 2026-10-23 recovery experiments add official COCO captions from the original
2014 annotation archive (checksum verified), reuse existing COCO image splits,
and implement native linear LoRA with merged release weights. Teacher-generated
retail captions, masks and geometry-derived motion/policy labels remain explicitly
marked as weak supervision; no human collision or psychological-intent labels
are asserted. See `docs/RECOVERY.md` for lineage and limitations.
