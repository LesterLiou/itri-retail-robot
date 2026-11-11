# 本專案 VQA 資產與決策指令

- 模型：`models/vqa/best/`，含 model.pt、manifest、language_config、tokenizer，完整 bundle 不需另外找 base model。
- YOLO：`models/yolo/yolov8n.pt`。
- 原推論核心的固定副本：`vendor/lightweight_risk_vqa/`；來源 SHA `889558ff0ff5f43c818ce82aa39b7b95734e7e6e`，保留 THIRD_PARTY 等原始檔。
- 訓練相容指令：本目錄 `prompt_training_compatible.txt`。worker 實際讀取並核對與固定來源的 ROBOT_QUERY 一致；未驗證新 prompt 不混入這個 profile。
- 決策接口說明：`decision_contract.yaml`。模型選項固定 STOP／DANGER_WARNING／CONTINUE／OBSERVE，證據保留 observation、motion、intent、risk、explanation。DATA_LOSS 是健康狀態，不是第五個模型動作。
- `assets.manifest.json` 記錄副本逐檔 SHA256。`python3 scripts/verify_vqa_assets.py` 在宿主機核對。

模型／影片在 repo 目錄內但由 Git 忽略；不要強制加入大型權重。bundle manifest 裡的舊 training_args 路徑是歷史訓練紀錄，loader 不使用它們載入外部資產。

目前 `.env` 使用 project-relative 路徑，不再掛載另一份 `/home/lester/VLMotion`。日常使用 `make nav-vqa` 或 `make vqa-ui-video`，ROS UI 是本專案 retail_core，不直接跑上游獨立影片 UI。

解碼 profile：`baseline` 是原版；`sentence_safe` 只收緊自然語言 token 標點，保留 checkpoint、prompt、JSON enum、風險／動作映射與 schema。11 張配對樣本兩者都合法、動作無變更；這是結構測試，不是準確率驗收。`.env` 在假硬體環境明確選 sentence_safe；可切回 baseline 後重啟模型比較。

這份 checkpoint 沒有校準的四動作機率，UI 不會捏造百分比，也不會按時間輪播動作。NO_DETECT 是舊接口的空觀測 enum，不能拿來替換此模型的 OBSERVE 或 DATA_LOSS。

本機實驗另選 `VQA_GENERATION_ENGINE=grammar_cached` 合併固定 token 的 KV-cache 計算。11 筆 motion／intent／risk／action 相同，9 筆 raw 逐字相同、2 筆證據措辭不同；不是完全等價或準確率證明。僅用於隔離假硬體，`hf` 是原引擎回退選項；2 秒 TTL／schema／watchdog 仍保留。
