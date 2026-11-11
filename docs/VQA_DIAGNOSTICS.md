# VQA 短片對照、DATA_LOSS 原因與專案內模型

本輪已讀取你提供的 66.198 秒影片，抽查 2、12、25、40、58 秒畫面。影片保存在忽略的 `data/reference/vqa_reference_reel.mp4`；沒有把別人的畫面當成本專案模型的測試成功結果。

## 1. 短片實際展示什麼

畫面有固定類別、每類百分比與文字證據。抽查時看到 SITTING、WALKING、POSSIBLE FALL · REVIEW、LYING / RESTING、UNCLEAR / OTHER 等，另有 REVIEWING FOOTAGE。部分片段明示 **4×**；介面顯示約 194–364 ms 的數字，但不能由影片確認它量的是哪一段延遲、是否校準機率、使用哪個模型／API。

我們可以採用相同的**固定選項＋證據＋持續顯示**方式，但不複製它的八個人體活動標籤，也不捏造百分比。本專案現在固定四項：

| 模型選項 | 意義 | 證據要求 |
|---|---|---|
| CONTINUE | 模型評估低風險 | motion 已知、approach UNLIKELY，理由不能同時說證據不足 |
| DANGER_WARNING | elevated risk，導航依既有策略降速 | 描述支持風險的當前／時間序列證據 |
| STOP | imminent risk | 原 schema 要求符合對應風險與 action |
| OBSERVE | 模型可處理輸入，但無法可靠判斷方向／風險 | 明示不確定性，不能當成 CONTINUE |

四個格子位置固定，MODEL SELECTED 依實際模型輸出變動；失效時只能顯示 HISTORY ONLY，沒有計時輪播／隨機選項。confidence 沒有校準資料，所以顯示 unavailable。NO_DETECT 不是這份 checkpoint 的動作類別，不能拿它替換 OBSERVE 或 DATA_LOSS。

## 2. DATA_LOSS 不是一種模型動作

DATA_LOSS 是整合層的**資料／結果健康狀態**。UI 現在把 DATA HEALTH、擷取年齡、原因和四個模型選項分開。畫面可保留上一筆有效答案，但歷史不授予運動許可。

本次 45 秒、50 筆模型回答診斷：

- JSON 解析失敗：**0**。
- 非 ASCII 亂碼：**0**。
- 3 筆把偽 JSON 欄位抄進 observation／explanation，例如證據句夾入 `'motion':...`，被原 schema 拒絕。
- 1 筆 action=CONTINUE／LOW，但理由又說需要更多證據，語義矛盾，被原 schema 拒絕。
- 另外有捕獲後年齡超過 2 秒的結果，包括結果仍在路上、前一筆先過期的空檔。

來源：[原始分類統計](validation/2026-11-11-vqa-output-audit-before.json)。完整 raw 回答在忽略的 `data/audits/vqa_outputs_before.json`。

因此你的情況**不只是亂碼**。程式能推論、能產生合法 JSON，不代表這份模型已具備可靠的判斷能力。配對樣本中還看到 `noisy`、`nozzle`、`no valid evidence` 等很弱的證據，甚至和選擇的方向不相稱；這屬於模型語義／泛化品質，不能只改 UI 或限制 enum 就宣稱修好。

## 3. 目前做了什麼，仍保留什麼限制

原 prompt、權重、四類風險映射與 `robot_schema_error` 保留。新增 `sentence_safe` 解碼 profile，只收緊**證據文字**可生成的標點，排除容易形成偽 JSON 的單引號／冒號／分號；結構欄位仍由原 grammar 控制，不事後修寫模型答案，也不把矛盾的 LOW 放行。

啟用前用固定資料做離線配對：6 張真相機、5 張短片場景。短片裁掉底部別人的類別／證據介面，避免模型抄標籤。

| 配對結果 | 原版 baseline | sentence_safe |
|---|---:|---:|
| schema 通過 | 11/11 | 11/11 |
| 決策改變 | — | 0 |
| 暖機後 median（剔除首張） | 764.06 ms | 750.94 ms |

這是小型**結構／行為比較**，沒有真實標註，不是準確率驗收。原版第一張有冷啟動成本，不能拿 max 直接宣稱新版本大幅加速。[摘要](validation/2026-11-11-decoder-comparison-summary.json)，完整結果在 `data/audits/decoder_compare.json`。

另測 `grammar_cached` 引擎：把原 grammar 已決定、沒有選擇空間的 token 合併送入 KV cache，仍用模型 logits 選自由 token，不縮短證據、不改 prompt／權重。11 筆的 motion／intent／risk／action 全同；9 筆 raw 逐字相同，2 筆證據措辭有差異，所以**不能稱為位元或文字完全等價**。HF median 825.19 ms、cached 268.85 ms；這個小樣本沒有證明所有影像都等價。[比較](validation/2026-11-11-generation-engine-comparison.json)。另有 16 個 CPU 快取／固定與自由 token／截斷／四動作案例，[測試](validation/2026-11-11-grammar-cache-tests.log)。

目前只在本機假硬體實驗 profile 明確選 `VQA_GENERATION_ENGINE=grammar_cached`。可用 `hf` 回原版。這不是增加有效期或維持過期許可；也不是模型準確率驗收。新 GPU／Thor／真機不能直接沿用此小樣本的結論。

接著做 45 秒真相機觀察：86 筆回答，median **424.38 ms**、max **799.99 ms**；本段沒有 capture expired，仍有 1 筆 LOW／證據不足矛盾被原 schema 拒絕。900 次健康採樣中 882 次有效、14 次 schema 失效、4 次等待，沒有把不合格回答改成有效許可。[實測](validation/2026-11-11-vqa-output-audit-fast.json)。各窗口畫面／負載不同，這是可用性觀察，不是準確率或硬即時保證。

目前隔離假硬體 profile 在 `.env` 明確使用 `VQA_DECODING_PROFILE=sentence_safe`。可改回 baseline 並重啟原模式比較。新 profile 不保證消除語義錯誤／DATA_LOSS；擷取時間與 2 秒 TTL、watchdog、schema 仍照常檢查。

輸出訊號分成：原始 `raw_action`、schema error、decoder profile、模型推論時間／整段 request 時間、ROS 當前健康。原時間戳沒有重蓋成 now。要進一步做成高更新率的可靠分類，需要有標註資料的模型／推論評估，不能靠延續舊 CONTINUE。

## 4. 模型、prompt 和指令現在都在哪裡

| 資產 | 專案內位置 |
|---|---|
| 完整模型 bundle，約 1.2 GB | `models/vqa/best/` |
| tokenizer、語言模型設定 | 上述 bundle 的 `tokenizer/`、`language_config/` |
| YOLO 權重 | `models/yolo/yolov8n.pt` |
| 固定推論核心副本 | `vendor/lightweight_risk_vqa/` |
| 訓練相容 prompt | `config/vqa/prompt_training_compatible.txt` |
| 四決策接口說明 | `config/vqa/decision_contract.yaml` |
| 逐檔 hash | `config/vqa/assets.manifest.json` |
| 原有測試影片 | `data/video/` |
| GPU 服務指令 | `docker/vqa_worker.py`、`docker/compose*.yaml` |
| ROS 指令／UI | `ros2_ws/src/retail_core/`、`retail_rviz_plugins/` |

`.env` 已改成：

```dotenv
VQA_CHECKPOINT_DIR=./models/vqa/best
VQA_SOURCE_DIR=./vendor/lightweight_risk_vqa
YOLO_MODEL_FILE=./models/yolo/yolov8n.pt
VIDEO_DIR=./data/video
VQA_DECODING_PROFILE=sentence_safe
VQA_GENERATION_ENGINE=grammar_cached
```

模型 ID 仍為 `30a2397b628c81721afde15c690122887b5a9709a9fa1bac0f7bb73f7403e619`，prompt ID 仍為 `d02131eab27f7639a650c3bcca26baae02ad80fd3d0f3558f8ed14e2ce070f21`。複製時逐檔核對，沒有修改原來源。bundle 裡 training_args 的舊絕對路徑只是歷史 metadata，loader 不使用它們找另一份模型。

這些大型資產雖在專案資料夾內，仍被 Git 忽略。不要強制提交權重；備份／搬機時另外複製 models、data 和本機 config。vendor 是固定小型來源副本，不修改 external 或另一個 VLMotion checkout。

## 5. 直接使用與檢查

### VQA 頻率應配合運算時間嗎？

應配合，但目前已是**單筆推論、完成後取最新影像**，不會把相機每一幀排隊送進模型。相機 15 FPS、ROS 100 ms 健康檢查、實際 VQA 完成率，是三件事。100 ms timer 不是 10 個並行 VQA request。

若一次計算 0.6–1 秒，單一 worker 自然只有約 1–1.7 Hz 的理論推論率，另有傳輸／處理成本。新快取引擎本次實測約 1.9 個結果／秒。只有一筆 pending，忙碌期間覆蓋最新輸入，不排舊影像。

單純再降低頻率會讓前一筆觀測更老。假設上一張在 t=0 擷取、t=1 才回來，下一張 t=1 擷取、t=2 回來，舊結果在更新前已接近 2 秒；加上抖動就會過期。若完成後又等 1 秒，情況更差。

若未來要限頻，應採「下一次開始=max(上次完成，上一輪開始＋目標週期)」，不是每個 timer 強送，也不是每次完成再固定睡一大段。時間預算要滿足「相鄰擷取週期＋下一次推論／傳輸／抖動 < 有效期」，且保留餘量。目前維持完成驅動，不靠加長 TTL 或重蓋 timestamp 解決。剩下的 schema 語義矛盾，也不會因降頻自動消失。

宿主機：

```bash
python3 scripts/verify_vqa_assets.py
make nav-vqa
# 或原影片
make vqa-ui-video VIDEO=/data/video/0610_take3n4.mp4
```

另一個宿主終端 `make shell` 後，在容器唯讀診斷：

```bash
ros2 run retail_core demo_control status
python3 /workspace/scripts/audit_vqa_outputs.py --seconds 45 --output /tmp/vqa_outputs.json
```

raw 報告留在 container `/tmp`。宿主取回：

```bash
docker compose cp sim:/tmp/vqa_outputs.json data/audits/vqa_outputs_latest.json
```

離線比較會占 GPU，先取消任務並確認停止，宿主停止線上 worker 再比較：

```bash
docker compose stop vqa-worker
docker compose --project-directory . -f docker/compose.yaml -f docker/compose.vqa.yaml \
  -f docker/compose.ui.yaml run --rm --no-deps --entrypoint python3 \
  -v "$PWD/scripts/compare_vqa_decoders.py:/app/compare_vqa_decoders.py:ro" \
  -v "$PWD/data/evaluation:/evaluation:ro" \
  vqa-worker /app/compare_vqa_decoders.py
# 完成後重新啟動原模式
make nav-vqa
```

`data/evaluation/frames.json` 和影像是本機測試資產，未提交 Git；新主機先建立自己的資料再跑，不能把這一小批當成通用基準。

## 6. 你的 MoveIt 為什麼一直 fail

查到當時 **CONTROL_MODE=mission**、手臂 **authorized=False**。日誌顯示 Cartesian path 已完成 **100%／59 points**，之後 trajectory 被 gate／controller 入口拒絕，最後 ABORTED。這表示規劃成功、執行許可沒有取得，和 VQA JSON 無關。

把 Planning Attempts 加到 27、Velocity Scaling 設 1.0，無法取得手臂控制權。截圖還勾了 Cartesian Path；初次測試先取消這個勾選、速度／加速度回 0.1，用簡單 joint4=0.3 rad。

先取消導航、確認停止，宿主 `make stop` → `make moveit`。MODE 應顯示 MoveIt manual，設定 Joints，按 Arm execution window 等 ready，再於窗口內 Plan & Execute。`hands_up` 只是命名目標，不會自動取得許可。現在 RViz 也會明確顯示導航模式的 MoveIt 是 PREVIEW ONLY。

所有欄位與視角切換見 [MoveIt 逐項教學](RVIZ_MOVEIT_GUIDE.md)，整個 UI 流程見 [操作架構](OPERATIONS.md)。
