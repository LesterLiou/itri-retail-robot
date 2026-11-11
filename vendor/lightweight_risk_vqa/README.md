# Lightweight Risk VQA

版本 **0.3.0**。以 MobileNetV3＋ConvLSTM 視覺記憶＋Qwen3-0.6B 做串流 VQA，保留外接 LLaVA baseline、YOLO 輔助與 bbox ROS 比較路徑。

**目前是研究模型。** 基本人數／人物分割有改善，但時間、方向、意圖與碰撞仍未可靠。先讀[整合報告](docs/MODEL_DATASET_REPORT.md)：dataset／表現表格、微調範圍、架構圖、完整 prompt、輸出與限制。[版本變更](CHANGELOG.md)。

## 保留的模型與執行模式

| 路線 | 入口 | 功能 |
|---|---|---|
| 整合模型 | `vqa-only` | 一個 MobileNet／ConvLSTM／Qwen bundle；可選 mask，無獨立人數輸出 |
| 整合模型＋偵測 | `yolo-assisted` | 同一 bundle＋YOLOv8n 人數／框歷史提示；VQA 回答與受限 policy 配對 |
| 外接 VLM baseline | `worker` | 分割 bundle 的 frame＋mask 交給另一個 LLaVA／RoboPoint worker |
| 舊整合入口 | `embedded` | 保留舊 query／state 比較，和新 robot schema 分開 |
| ROS bbox baseline | `launch/` | YOLO＋ByteTrack＋bbox MotionLSTM；與像素 ConvLSTM 不共用權重 |

## 安裝與資產

容器 `/workspace` 對應主機 `/home/lester/VLMotion`。需既有相容 Torch／torchvision／CUDA；影片 UI 不需要 ROS，桌面需可用 DISPLAY／X11。

```bash
cd /workspace/ros2_ws/src/lightweight_risk_vqa
python3 -m pip install -r requirements-vlm.txt
python3 -m pip install --no-deps -e .
```

新 clone 不包含權重、資料或影片。Bundle 必須有 `model.pt`、`manifest.json`、`language_config/`、`tokenizer/`；本機路徑見整合報告。離線 SAM3／Qwen3-VL 教師需較新的 Transformers，版本口徑見報告；不要在沿用環境時自行更換 CUDA。

## 日常 UI

```bash
# YOLO 提供人數與位置，關閉分割，使用訓練相容問題
bash scripts/run_vqa_ui.sh yolo-assisted \
  --checkpoint /workspace/checkpoints/robot_vqa_recovery_v1/c_policy/best \
  --video /workspace/example/video/0610_take3n4.mp4 \
  --no-segmentation --robot-prompt compatible

# 單一模型，不載 YOLO
bash scripts/run_vqa_ui.sh vqa-only \
  --checkpoint /workspace/checkpoints/robot_vqa_recovery_v1/c_policy/best \
  --video /workspace/example/video/0610_take3n4.mp4 --no-segmentation
```

移除 `--no-segmentation` 恢復 mask／overlay；它是計算開關，不需重訓。外接 worker 不接受此開關。UI 上方顯示指令、人數來源、VQA／vision／YOLO 延遲及理由，底部保留完整 raw；`Show technical details / edit prompt` 展開細節。

`vqa-only` 未輸出 `person_count` 時不猜人數，mask 連通區也不當成人物 ID。`DATA_LOSS` 表示資料／回答不可用，不是 DANGER_WARNING。相同影片已用於訓練，這是功能展示而非泛化測試。

`--robot-prompt compact`、`--robot-decoding free` 與 `--fast-motion-head` 保留實驗比較。Compact 未優於預設；head 只學弱方向代理、尚不可靠，預設關閉。完整契約與操作見 [Prompt](docs/PROMPT.md)和[接口](docs/INTERFACES.md)。

## 外接 LLaVA baseline

先開 controller／worker，再執行：

```bash
bash scripts/run_vqa_ui.sh worker \
  --checkpoint /workspace/light_foundation_vlm_deployment_v1/models/temporal_best \
  --video /workspace/example/video/0610_take3n4.mp4 --query 'the people' \
  --controller-url http://127.0.0.1:11000 --model-name llava-v1.5-7b --vqa-hz 0.5
```

此處舊 deployment 目錄只提供權重，程式由本套件載入；外部答案不來自內建 Qwen，也不直接讀取記憶 tensor。舊 `embedded` state UI 的重現見[保留訓練工具](docs/TRAINING.md#134-測試-mevis-微調模型的-ui)。

## 文件與程式閱讀

| 要做的事 | 唯一主要入口 |
|---|---|
| 知道模型／dataset／結果／prompt | [整合報告](docs/MODEL_DATASET_REPORT.md) |
| 沿程式理解模型與兩條架構 | [Architecture](docs/ARCHITECTURE.md) → `light_foundation_vlm/model.py` |
| 新資料、教師、複核、A→B→C、續訓 | [Recovery 操作](docs/RECOVERY.md) |
| 重建 JAAD／MeViS／舊基準 | [Training 工具](docs/TRAINING.md)、[歷史結果](docs/RESULTS.md) |
| 問題與生成診斷 | [Prompt](docs/PROMPT.md) → `robot_vqa.py`／`robot_decoding.py` |
| ROS 相機／Isaac／bbox 路線 | [Baselines](docs/BASELINES.md)、[Interfaces](docs/INTERFACES.md) |
| 來源與資產條款 | [Third party](THIRD_PARTY.md) |

## 檢查與交付

```bash
python3 -m pip install -r requirements-dev.txt
bash scripts/check.sh
python3 scripts/export_source.py --output dist/lightweight_risk_vqa-0.3.0-source.tar.gz
```

來源封裝只含維護中的程式、設定、文件圖片與測試，不含 `.git`、權重、影片、dataset、results 或本機設定；已存在的輸出不覆蓋。套件缺 Torch／Qt 時會明示略過相關測試，完整 ROS 測試從主專案用 `bash scripts/check.sh --ros`。所有 CLI 參數見 `python3 -m lightweight_risk_vqa.light_foundation_live_ui --help`。

主專案一起提交時沿用根目錄 GitHub Actions；獨立複製此套件才使用套件的 workflow，不要在主 repository 新增巢狀 `.git`。本機資料請另行備份，Git ignore 不等於備份。
