# THOR VQA 模型切換

兩種 worker 共用既有 HTTP 入口和 ROS 2 Jazzy 影像鏈。以下指令從含有模型切換功能的整合 checkout 執行。各機原有的 `.env` 和權重由本機保管。影像與結果使用觀測用 domain `84`，5070 可沿用已開啟的 Monitor。

| 模型 | 模型目錄 | Compose 選項 |
|---|---|---|
| 原 ConvLSTM／Qwen | `VQA_CHECKPOINT_DIR`，預設 `./models/vqa/best` | `-f docker/compose.cross-host.yaml` |
| 原生 Qwen3-VL | `THOR_QWEN_MODEL_DIR`，預設 `./models/Qwen3-VL-2B-Instruct` | `-f docker/compose.cross-host.yaml -f docker/compose.thor-qwen.yaml` |

Qwen3-VL 以同一來源最近三張 RGB 影格判讀方向，YOLO 在最新一張量測最大單人框佔畫面的面積。建議與判定理由寫入原有 `RiskAdvisory`，依下表由上往下判定。

| 條件 | 觀測建議 |
|---|---|
| 最大單人框面積達 `QWEN_STOP_BOX_AREA_RATIO`（預設 90%） | `STOP`，這筆結果可直接由 YOLO 產生 |
| Qwen 判定 `TOWARD_CAMERA` | `DANGER_WARNING` |
| 有可量測的人框、最大框未達停車門檻，且 Qwen 判定 `AWAY_FROM_CAMERA` | `CONTINUE` |
| 只偵測到一人，Qwen 判定 `STATIONARY`，且未裁切的人框面積不超過 `QWEN_FAR_BOX_AREA_RATIO`（預設 10%） | `CONTINUE` |
| 有效但證據未達以上條件 | `OBSERVE` |
| 影格不足、回覆無效或結果過期 | `DATA_LOSS` |

框面積是單眼影像中的遠近代理，以上為觀測用門檻。模型切換、來源改變或影格時間不連續時會重新收集影格。相機與結果仍由既有 adapter 檢查時效。

## 啟動 Qwen3-VL

首次啟用人體框量測，先以既有 `itri-retail-robot:thor-vqa` 映像為基底建置 Qwen worker，並確認本機有 `models/yolo/yolov8n.pt`。

```bash
docker compose --project-directory . -f docker/compose.cross-host.yaml \
  -f docker/compose.thor-qwen.yaml build vqa-worker
```

```bash
docker compose --project-directory . -f docker/compose.cross-host.yaml \
  -f docker/compose.thor-qwen.yaml up -d --no-deps --force-recreate \
  --wait --wait-timeout 120 vqa-worker
curl -fsS http://127.0.0.1:8090/health
```

`/health` 應顯示 `model_backend: qwen3-vl`、權重的 `model_id` 和 `prompt_id`。5070 Monitor 收到相機畫面後若仍顯示 `No model response yet`，用 `docker compose --project-directory . -f docker/compose.cross-host.yaml ps -a` 檢查 `thor-adapter`。它負責把影像交給 worker 並發布 ROS 結果。若服務已停止，在同一 checkout 執行：

```bash
docker compose --project-directory . -f docker/compose.cross-host.yaml \
  up -d --no-deps thor-adapter
```

首次安裝請依主 README 建置觀測映像與 GPU worker。改用另一份相容的 Qwen3-VL bundle 時，在本機 `.env` 設定 `THOR_QWEN_MODEL_DIR`，或把該變數加在這次指令前，再執行同一條切換指令。權重需已存在，worker 以離線模式載入。

## 切回 ConvLSTM

```bash
docker compose --project-directory . -f docker/compose.cross-host.yaml \
  up -d --no-deps --force-recreate --wait --wait-timeout 120 vqa-worker
curl -fsS http://127.0.0.1:8090/health
```

`/health` 應顯示 `model_backend: convlstm`。更換同架構的 ConvLSTM checkpoint 使用 `VQA_CHECKPOINT_DIR`，啟動前先核對訓練 prompt 及介面相容性。

## 隔離回放

下列指令需要既有 THOR GPU worker 映像，以及一張本機 JPEG。Compose 的一次性 worker 不發布服務的主機埠，可與觀測服務隔離測試。

```bash
docker compose --project-directory . -f docker/compose.cross-host.yaml \
  -f docker/compose.thor-qwen.yaml run --rm --no-deps -T \
  --entrypoint bash \
  -v "$PWD/scripts/check_thor_qwen.py:/app/check_thor_qwen.py:ro" \
  -v /absolute/path/still.jpg:/tmp/still.jpg:ro vqa-worker -ec '
    python3 /app/vqa_worker.py &
    worker=$!
    trap "kill $worker 2>/dev/null || true" EXIT
    for i in $(seq 1 90); do
      kill -0 "$worker"
      if python3 -c "import urllib.request; urllib.request.urlopen(\"http://127.0.0.1:8090/health\",timeout=1)" 2>/dev/null; then
        python3 /app/check_thor_qwen.py /tmp/still.jpg
        exit
      fi
      sleep 1
    done
    exit 1
  '
```
