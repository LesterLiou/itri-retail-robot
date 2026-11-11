# 保留的基準與 ROS 開發

兩模型串接的 **ConvLSTM segmentation＋外接 LLaVA／RoboPoint VQA** 是主要外部 baseline，UI 指令見 [README](../README.md)。本頁保留 YOLO／bbox MotionLSTM 及 ROS adapters，供 detector、tracking 與時間模組的比較；沒有因新的整合模型而刪除。

## YOLO＋ByteTrack＋bbox MotionLSTM

每張新圖由 YOLO 找人，ByteTrack 維持每個相機的 ID；對同一人保留最近 16 步、5 Hz、10 維 bbox features。MotionLSTM 預測一秒後的五方向，沒有像素活動理解，也不是 pixel ConvLSTM hidden state。

目前 `temporal_mode=heuristic` 是明示的 bbox 規則基準；`temporal_mode=lstm` 必須提供相容 checkpoint。不可將 `mobilenetv3_convlstm_qwen` bundle 當成 MotionLSTM 的 `.pt`。

```bash
python3 -m pip install -r requirements-runtime.txt
python3 -m lightweight_risk_vqa.video_benchmark \
  --left-video /workspace/example/video/0610_take3n4.mp4 \
  --right-video /workspace/example/video/0610x9_v0629.mp4 \
  --frames 100 --output /workspace/results/lightweight_risk_vqa/new_session --save-preview
```

產物含 `tracks.jsonl`。要做訓練，記錄多段真正獨立的 session；[TRAINING](TRAINING.md)說明空白 CSV／離線老師與弱標籤限制。

```bash
python3 -m lightweight_risk_vqa.prepare_sequences \
  --tracks /workspace/results/lightweight_risk_vqa/day1/tracks.jsonl \
  --session day1 --labels /workspace/datasets/lightweight/day1_labels.csv \
  --output /workspace/datasets/lightweight/day1.npz

python3 -m lightweight_risk_vqa.train_lstm \
  --train /workspace/datasets/lightweight/train.npz \
  --val /workspace/datasets/lightweight/val.npz \
  --output /workspace/checkpoints/lightweight_motion_v2.pt
```

`prepare_sequences` 的 feature contract 為 `normalized_bbox_v1`；future 只建立 label。以 session 分 train/val，sampler 只向過去取樣。CLI 的 `--help` 可查 steps、頻率、horizon；模型載入時會檢查相容性。多個 session 的 NPZ 需合併時保留 sessions 欄位，不能只拼接 features 後丟掉來源。

## ROS 2／相機／Isaac

此部分依賴 VLMotion 的 sibling `future_risk_vqa` 套件；獨立發布本目錄並不包含該套件。Embedded／worker 影片 UI 不需要 ROS。

```bash
cd /workspace/ros2_ws
source /opt/ros/humble/setup.bash
colcon build --symlink-install --packages-select vlpoint future_risk_vqa lightweight_risk_vqa
source install/setup.bash

ros2 launch lightweight_risk_vqa lightweight_video.launch.py
ros2 launch lightweight_risk_vqa lightweight_live.launch.py --show-args
ros2 launch lightweight_risk_vqa lightweight_isaac.launch.py --show-args
```

同一 domain 一次選一個來源 launch，不同啟動範例不是要全部一起執行。`--show-args` 用來檢查相機 topics、compressed/raw、影片路徑與 `record_tracks_path`。詳細欄位、timestamp／ID contract 見 [INTERFACES](INTERFACES.md)。

預設 `enable_vqa=false` 使用 direct temporal UI；沒有已訓練 bbox checkpoint 時畫面明示 heuristic。真正 LSTM 用 `temporal_mode:=lstm lstm_checkpoint:=...`。可選 `enable_vqa:=true vqa_input_mode:=single_now_lstm`：LLM 看 NOW crop＋對齊的 LSTM prior；仍有 LLM 延遲，也不會把 invalid VQA 變成有效回答。

**上述 ROS launch 接的是 YOLO／bbox 路徑。**目前 ConvLSTM＋Qwen／外部 VLM 的共用 UI 仍直接讀影片。要接 ROS 相機，需新增 Image subscriber 並保留每台相機獨立 `TemporalSegmenter`、timestamp 與 reset；尚未完成，不能宣稱 Isaac 或實體相機已直接接入整合模型。
