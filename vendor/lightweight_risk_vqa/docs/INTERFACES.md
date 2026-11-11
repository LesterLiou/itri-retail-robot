# 介面與設定

本頁先說明 ConvLSTM VQA 的兩種無固定區域模式，再列出保留的 **YOLO／bbox MotionLSTM ROS adapters**。模型邊界見 [ARCHITECTURE](ARCHITECTURE.md)；後文的 `enable_vqa=false` 只是 ROS baseline 預設。

## ConvLSTM VQA UI：無固定區域的兩種模式

2026-10-23：依目前設計改為讓 VQA 判斷全畫面人物如何移動、是否可能靠近機器人，以及下一步 action。**不畫固定停止區、通道或人物框，不以人物位於某區域來產生新模式的 action。** 相機朝向視為機器人前方；「觀察到接近」與「推測接近意圖」分欄呈現，意圖不是可直接量測的心理事實。

| 開法 | 載入模型 | 輸入與決策 |
|---|---|---|
| `vqa-only` | 一個 ConvLSTM＋內建 Qwen bundle | 單張 NOW＋ConvLSTM 記憶 → mask、人物移動／接近意圖／風險／action／文字說明。沒有 YOLO、mask bbox 或幾何 action 規則 |
| `yolo-assisted` | 相同 bundle＋本機 YOLOv8n | YOLO 提供目前人物 bbox 及過去到 NOW 的大小／中心變化，當作文字輔助證據輸入相同 Qwen。**最終 action 仍由 VQA 回答**，不是 detector 覆蓋 |

可加 `--no-segmentation` 真正跳過分割計算；預設 `--robot-prompt compatible`，`compact` 是未證明更好的實驗選項。UI 摘要與 raw、prompt 全文見 [整合報告](MODEL_DATASET_REPORT.md)。

兩種模式共用 UI、同一影片、同一權重、同一 `ROBOT_QUERY` 基本問題，便於比較。mask query 固定 `the people`；VQA 復用同一影像 forward 的 80-token visual/memory prefix，影像 encoder／ConvLSTM 每個新影格只更新一次。YOLO 模式只加文字 embeddings，不再編碼影像；不啟動 ByteTrack 或外部 LLM worker。

從容器內的套件目錄執行：

```bash
cd /workspace/ros2_ws/src/lightweight_risk_vqa

# A：單一 VQA 模型
bash scripts/run_vqa_ui.sh vqa-only \
  --checkpoint /workspace/checkpoints/robot_vqa_recovery_v1/c_policy/best \
  --video /workspace/example/video/0610_take3n4.mp4

# B：相同 VQA 模型＋YOLO 輔助
bash scripts/run_vqa_ui.sh yolo-assisted \
  --checkpoint /workspace/checkpoints/robot_vqa_recovery_v1/c_policy/best \
  --video /workspace/example/video/0610_take3n4.mp4 \
  --bbox-model /workspace/yolov8n.pt --bbox-image-size 416
```

一次開一個 UI 比較。可替換影片／checkpoint。這兩個入口已設定 robot prompt、192 個生成 tokens、目標 segmentation 2 Hz、VQA 1 Hz；不需傳舊 `state_query.txt`。自訂 `--query` 仍須要求同一 robot schema；只問走路／站立的 state 問題無法提供完整機器人判斷。

### VQA 輸出與最終 action

```json
{
  "observation": "A person is walking closer to the robot.",
  "motion": "TOWARD_CAMERA",
  "approach_intent": "LIKELY",
  "collision_risk": "ELEVATED",
  "action": "DANGER_WARNING",
  "explanation": "The observed movement is consistent with approaching."
}
```

這是 schema 示範，不是模型實測結果。`robot_vqa.py` 定義問題、驗證及輸入健康檢查；不從 bbox 值計算 action。`motion` 分靠近／遠離／橫移／靜止／不確定；`approach_intent` 為 LIKELY／UNLIKELY／UNCERTAIN。`collision_risk` 與 `action` 必須由模型一致回答：

| 模型風險 | 模型 action |
|---|---|
| IMMINENT | STOP |
| ELEVATED | DANGER_WARNING |
| LOW | CONTINUE |
| UNCERTAIN | OBSERVE |

目前兩種 robot 模式的共用 prompt 明確定義接近警告策略：`motion=TOWARD_CAMERA` **或** `approach_intent=LIKELY` 時，要求 `ELEVATED + DANGER_WARNING`；有足夠可見碰撞迫近證據才升級 `IMMINENT + STOP`。只有遠離／橫移／靜止、意圖為 UNLIKELY 且無可見危險時，才允許 `LOW + CONTINUE`；其他情形要求 `UNCERTAIN + OBSERVE`。文字說明需與所選欄位一致，衣著／性別依原始影像判讀、沒有清楚證據則省略；mask 顏色僅是標記。

### 生成時限制合法組合（2026-10-23）

兩種新模式現在預設 `--robot-decoding constrained`，使用 `robot_decoding.py` 的小型 token 文法。Qwen 先生成 observation，再選 motion／approach_intent／risk，生成時只允許一致的 action；例如 `IMMINENT` 後只能接 `STOP`，TOWARD_CAMERA／LIKELY 後只能選 ELEVATED 或 IMMINENT。其他情形保留 LOW／UNCERTAIN／較高風險的合法選擇；LOW 只允許明確非靠近方向且 intent 為 UNLIKELY。

這是可見的任務策略與輸出限制，**不是模型從資料自動學得的安全保證**。選擇仍依同一 Qwen logits 與 visual/memory prefix，不增加模型、不重跑 encoder，不用 bbox／文字關鍵字覆寫 action，也不把錯誤 raw JSON 改值後假裝是模型回答。risk→action 配對是文法限制，不再讓兩欄自由產生互相矛盾的值。

observation／explanation 仍是模型產生的英文短句，各最多24／32個生成 tokens，允許英文、數字、空格及基本句子標點；字元範圍以 `robot_decoding.py` 為準；不允許把 JSON 標點／方向標籤當作完整敘述，也不允許提前 EOS。達上限會關閉字串，因此句子可能被截短。這只能保證結構與指定 enum 組合，**無法保證自然語言描述、人物方向、接近意圖、風險符合影像，已加有限文字矛盾檢查，但不保證完整語意一致性**。不要用合法 JSON 或不再出現 DATA_LOSS 來判定準確率已提升。

可加 `--robot-decoding free` 重現自由生成對照。free 模式回答若靠近卻 LOW／UNCERTAIN、risk/action 矛盾，或 observation 只有 enum 標籤，會被 schema 驗證判為 DATA_LOSS。UI metrics 會顯示實際 robot_decoding，decision 亦附 `decoding`／`action_constraint`，明示是受限制的模型輸出；純 VQA 和 YOLO 輔助都使用相同生成限制。預設192個生成 tokens；自訂過小的 token 上限仍可能造成未完成 JSON。

影像／VQA 過期、loop/query 不符、推論失敗、缺必要欄位、風險與 action 矛盾 → **DATA_LOSS**，不補造意圖、不套框大小規則救回。Qwen 原始錯誤輸出仍在 `raw_last_response_for_debug`。新模式的 `decision_authority` 是 `embedded_vqa` 或 `embedded_vqa_with_yolo_hints`。暫停／結束沿用 OBSERVE，pipeline／VQA error 優先。

目前 robot prompt 僅列任務、欄位與合法值，不提供可被抄寫的 JSON 佔位範例。robot 模式在生成第一個完整 JSON object 後停止，避免重複輸出；缺欄位仍報錯。生成限制只用於新 robot UI，舊模型評估入口的生成預設保持原行為。改程式後需關閉舊 UI，以相同入口重新啟動；只點 Apply 仍會沿用輸入框中的舊 prompt。

### YOLO 輔助究竟提供什麼

`PersonMotionHints` 用很短的 IoU 關聯歷史提供 normalized bbox、樣本數、跨度秒數、中心位移與框高變化。每次輸入只涵蓋過去到 NOW；合併／分裂、斷流、影片循環、換問題會重設相關歷史。它不提供 approach_intent、collision_risk、action 或固定區域標籤。遮擋、相機轉動與前進仍可能影響這些數值，prompt 明確把它當成有雜訊的輔助證據。

預設最多送6人的資料並明示省略數量，降低 prompt 成本。YOLO 限 person class，confidence 0.25、NMS IoU 0.45、最多50框；只讀本機權重、不自動下載。新模式不做 mask-to-bbox。UI JSON 的 `yolo_hints_used_for_answer` 對齊產生該回答的影格，不以更新的偵測假裝是舊回答的輸入。新增 detector 耗時見 `bbox_detector_ms`；總生成耗時仍受 prompt 長度與 GPU 負載影響。

### 現有權重的限制與後續訓練

`person_state_mevis_ablation_v1` 的文字訓練以 JAAD walking/standing／transition 為主，state answer 的 action 固定 OBSERVE；MeViS 提供 mask supervision，**沒有讓 Qwen 學會接近意圖與碰撞風險**。目前 C 已加入弱幾何 policy 訓練，仍未超過方向多數類基準；上面命令不能保證正確判讀影像，亦不能把啟動成功當成風險辨識已完成。

下一輪需要與 robot query 一致的因果影片問答：人物實際靠近、橫越、遠離、停留、遮擋，以及相機自身移動。標註 observation、motion、approach_intent（不確定時保留 UNCERTAIN）、collision_risk、action、explanation，經人工複核；以不同原始影片切 train/val/test。依距離／路徑與場景真值標風險，不能只從 bbox 大小自動造意圖答案。YOLO 輔助樣本使用相同取像時間與演算法產生 hints，並混入無 hints 的同問題訓練，以維持純 VQA 能力。更新現有 joint 訓練的 ConvLSTM、projectors、mask heads 與 Qwen 最後層；沒有這些標註時不宣稱已訓練此能力。

### 保留的舊 baseline

`embedded`／`worker` 仍保留整合與外接架構。舊 `state_query.txt` 可以重現走路／站立任務，該模型回答仍為 OBSERVE；它與新 robot schema 是不同任務。舊 `camera_policy.py`／`config/camera_policy.json` 僅作歷史幾何 baseline，需顯式選 legacy 與 `--decision-policy camera`；新模式拒絕這份 ROI 設定，UI 也已移除其繪框。新模式不發布機器人底盤控制命令。

## 預設直接時序路徑（2026-10-15）

```text
YOLO + ByteTrack -> per-ID temporal prediction
  -> lightweight_temporal_analysis (no HTTP, no LLM)
  -> UI / decision / action
```

`enable_vqa` 預設 false。直接模式不啟動 `future_vqa` 或 `temporal_fusion`，而由 `lightweight_temporal_analysis` 發布既有 topic 名稱的相容報告。`metadata.analysis_mode=direct_temporal`、`llm_used=false`、`motion_source=heuristic/lstm`；schema 為 `lightweight_temporal_v1`。無足夠歷史顯示 WARMING_UP／OBSERVE；信心不足為 DATA_LOSS；有效靠近預測為方向警告，資料過期先於空場景判斷；姿態與活動標示 not inferred。UI 在此模式不用 VQA JSON presenter。

`enable_temporal_analysis:=false` 配合 `enable_vqa:=false` 可回到純偵測預覽／observe_only。`temporal_mode:=lstm` 需要 checkpoint，不會以規則降級冒充。

## 可選 VQA 資料流（enable_vqa=true）

```text
Image / CompressedImage (video / live / Isaac)
  -> lightweight_person_tracker (YOLO shared, ByteTrack state per camera)
  -> /future_risk/person_tracks [same JSON contract + temporal_prediction]
  -> original ground_trajectory
  -> original future_vqa [same prompt, schema, temporal storyboard]
  -> /lightweight_risk/vlm_events_raw
  -> lightweight_temporal_fusion
  -> /future_risk/vlm_events
  -> original future_policy + GUI + logger
```

`vqa_input_mode:=single_now_lstm` 改為每人只送一張 NOW crop；`future_vqa` 從同一 ID、影像時間之前的 track report 讀取可靠 LSTM 預測，放進 prompt 的 `trusted_temporal_prediction`。這個模式需要 `temporal_mode:=lstm` 與已訓練 checkpoint，自動採 `fusion_mode=lstm_future` 作最終 NEXT。`metadata.configured_storyboard_mode=current_crop`、`sample_count_per_track=1`、`metadata.temporal_priors`、`metadata.temporal_fusion` 可查實際輸入與來源。詳見 [單張設計及訓練](BASELINES.md)。

原套件無需修改。不要同時在同一 domain 跑兩套 publisher。`/future_risk/sam3_status` 與 `/future_risk/{left,right}/sam3/compressed` 保留歷史 topic 名稱以相容既有工具，實際 backend 是 `yolo_bytetrack`；新 UI 會顯示正確的 detector 名稱。

## 主要輸入輸出

2026-10-22：資料缺失、過期、VQA schema／worker 失效輸出 `DATA_LOSS`，`data_available=false`，UI 顯示灰色「UNABLE TO ASSESS」。直接 temporal 模式的低可靠性亦屬無法判斷；有效人物接近等語意警告仍為 `DANGER_WARNING`。原 VQA policy 的 unavailable 分支亦同步修改，資料失效不冒充人物危險。

| Topic | Type | 說明 |
|---|---|---|
| launch 指定的 left/right image topics | `sensor_msgs/msg/Image` 或 `CompressedImage` | 兩路需選同種 encoding；compressed/raw 是兩種不同 ROS message types |
| `/future_risk/pipeline_paused` | `std_msgs/msg/Bool` | 重用 transient-local pause QoS，影片 UI 的 pause/resume 沿用 |
| `/future_risk/person_tracks` | `std_msgs/msg/String` JSON | bbox、footpoint、ID、age、visible、timestamp、timing、temporal prediction |
| `/future_risk/sam3_status` | `std_msgs/msg/String` JSON | 相容 topic；backend=`yolo_bytetrack`，state=`ready/stale/error` |
| `/future_risk/{left,right}/sam3/compressed` | `sensor_msgs/msg/CompressedImage` | 相容的 detector debug preview，含 timestamp |
| `/future_risk/vqa_input/{left,right}_now/compressed` | `CompressedImage` | VQA 模式由舊 VQA 節點發布同步 evidence；VQA disabled 時由新 detector 發布 live preview |
| `/lightweight_risk/vlm_events_raw` | `std_msgs/msg/String` JSON | 原 VQA 解析／tracking-hint 處理後、LSTM 融合前的事件；不是原文字串 |
| `/future_risk/vlm_raw_response` | `std_msgs/msg/String` | 原 VQA 節點原始模型回覆；per-track 解析失敗時也發布目前已取得的回覆 envelope，便於定位是哪個 ID 出錯 |
| `/future_risk/vlm_events` | `std_msgs/msg/String` JSON | 融合後，同一既有 schema，新增時序 audit 欄位 |
| `/future_risk/decision`／`action` | `std_msgs/msg/String` | VQA 路徑沿用 policy；預設由獨立 temporal analysis 直接產生，純預覽才是 observe_only |

## 與 SAM3 的差異

沒有 mask、floor/walkway 分割或 pixel stop-zone overlap。腳點改為 bbox 底邊中央，遇遮擋／半身框可能偏離真正地面。新設定一律關閉 approximate STOP 與 metric zone/TTE；要做安全區比較需先另行驗證。保留 normalized_debug geometry 只供畫面與 track 對齊；目前 homography 假設 960×540 debug 影像，不代表真實距離，真相機尺寸不同時也不能拿其座標作公尺評估。

ByteTrack 在內部保留 lost tracks 以重接 ID；輸出只標記當前可見人物，不把保留的 lost track 當成仍偵測到人。時間倒退或兩影像間隔超過 `max_gap_s` 重設該相機 tracker，但不重用輸出的 L/R ID。重複 timestamp 不會新增 LSTM observation。沒有 appearance ReID，嚴重遮擋、交叉與移動相機仍可能換 ID。

## 感知參數

| 設定 | 預設 | 用途 |
|---|---|---|
| `model_path`／`device` | `/workspace/yolov8n.pt`／`cpu` | launch 覆蓋 YAML；可換本地 COCO YOLO11n，或 GPU `0` |
| `allow_download` | false | 避免缺權重時偷偷下載；先自行取得相同 adapter 支援的權重 |
| `image_size` | 640 | YOLO resize 上限，需以遠處人 recall 比較 416/640 |
| `confidence` | 0.10 | 低分 proposal 保留供 ByteTrack 第二階段關聯 |
| `track_high_threshold` | 0.35 | 高信心／新 track 啟動閾值 |
| `track_buffer` | 15 | lost track 可保留的更新步數；5 Hz 約 3 秒，實際取決於處理率 |
| `inference_hz` | 5 | 兩相機成組處理的排程目標，忙碌時只取最新影像 |
| `max_gap_s` | 1.0 | 相機斷流／長時間隔時重設 tracker，避免誤接 |
| `max_image_age_s` | 2.0 | 以接收 wall monotonic time 檢查來源是否停止更新 |
| `cpu_threads` | 4 | 此感知 process 的 Torch CPU thread 數 |
| `record_tracks_path` | 空 | 選擇性 JSONL，要求新檔案；用於後續準備序列 |

## LSTM 與融合參數

| 設定 | 預設 | 用途 |
|---|---|---|
| `temporal_mode` | heuristic | heuristic／lstm／disabled；lstm 必須有已訓練相容 checkpoint |
| `sequence_length`／`sample_hz` | 16／5.0 | 因果固定頻率 grid，涵蓋 3 秒歷史；需與 checkpoint 相符 |
| `temporal_max_gap_s` | 0.6 | 格點只能拿時間之前且不超過 gap 的 observation；長缺口不可假裝有完整序列 |
| `temporal_confidence` | 0.6 | softmax threshold；不足則 UNCERTAIN。softmax 不是已校正準確率 |
| `fusion_mode` | diagnostic | diagnostic 保留原決策；lstm_future 改 NEXT，須 temporal_mode=lstm |
| `history_retention_s` | 180 | 保存 temporal 預測以對齊延遲的 VQA evidence，另有每相機 4096 筆上限 |
| `alignment_tolerance_s` | 0.6 | 對每個 camera snapshot 只選同 ID、時間不晚於 snapshot 的預測 |

輕量套件 VQA 模式為 `track_crops/per_track`、`motion_source: vlm`：NEXT 採用模型回覆。原 `future_risk_vqa` 套件自身的 YAML 仍可使用 `tracking_hint`；比較 VLM 與框變化時須固定其餘條件，並查看每個 assessment 的 `future_prediction.motion_source`。這份 checkpoint 目前觀察到輸出 `L864: AWAY FROM CAMERA` 等單行方向，偶爾只輸出 `L2`。per-track parser 僅接受與請求 ID 相符的明確方向或單獨 ID；單獨 ID 的 NEXT 為 `UNCERTAIN`，policy 警告。報告的 `motion_only_tracks`、`unanswered_tracks`、`quality_warnings` 和每次請求的 `response_format` 明示回覆範圍；姿態與活動未回答時均為 `UNCERTAIN`。它不代表完整 VQA 或已校正的準確率。

`lstm_future` 在 `future_prediction` 保存 `pre_temporal_future_motion`、`vlm_future_motion` 和 `motion_source`；每個 assessment 附 `temporal_prediction`，metadata 附 `temporal_fusion`。不會改 body posture／activity，或修補 invalid/unavailable VQA。空場景、資料過期與 safety 行為仍以原 policy 實作為準。
