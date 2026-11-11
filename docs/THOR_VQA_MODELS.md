# THOR VQA 模型切換

兩種 worker 共用既有 HTTP 入口和 ROS 2 Jazzy 影像鏈。以下指令從含有模型切換功能的整合 checkout 執行。各機原有的 `.env` 和權重由本機保管。影像與結果使用觀測用 domain `84`，5070 可沿用已開啟的 Monitor。

| 模型 | 模型目錄 | Compose 選項 |
|---|---|---|
| 原 ConvLSTM／Qwen | `VQA_CHECKPOINT_DIR`，預設 `./models/vqa/best` | `-f docker/compose.cross-host.yaml` |
| 原生 Qwen3-VL | `THOR_QWEN_MODEL_DIR`，預設 `./models/Qwen3-VL-2B-Instruct` | `-f docker/compose.cross-host.yaml -f docker/compose.thor-qwen.yaml` |

Qwen3-VL 使用同一來源最近三張依擷取時間排序的 RGB 影格，發布方向和模型理由供觀測。有效回覆的建議固定是 `OBSERVE`。每次切換後會重新收集三張影格。若畫面間隔過大、來源改變或時間戳倒退，會重新收集。相機與結果仍由既有 adapter 檢查時效。

## 啟動 Qwen3-VL

```bash
docker compose --project-directory . -f docker/compose.cross-host.yaml \
  -f docker/compose.thor-qwen.yaml up -d --no-deps --force-recreate \
  --wait --wait-timeout 120 vqa-worker
curl -fsS http://127.0.0.1:8090/health
```

`/health` 應顯示 `model_backend: qwen3-vl`、權重的 `model_id` 和 `prompt_id`。此指令假設既有 `thor-adapter` 已啟動。首次安裝請依主 README 建置觀測映像與 GPU worker。改用另一份相容的 Qwen3-VL bundle 時，在本機 `.env` 設定 `THOR_QWEN_MODEL_DIR`，或把該變數加在這次指令前，再執行同一條切換指令。權重需已存在，worker 以離線模式載入。

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
