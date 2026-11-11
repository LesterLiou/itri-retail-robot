# 資料工具與保留的訓練對照

**目前 C 模型的 dataset、參數、結果與 prompt：先讀 [整合報告](MODEL_DATASET_REPORT.md)。新 A→B→C 資料準備／複核／重跑：讀 [RECOVERY](RECOVERY.md)。**

本文件保留 JAAD／COCO、舊 future-motion、人物 state、SAM3 複核和 MeViS 的獨特操作流程，供重建舊 baseline 或增加資料；以下歷史權重與小規模預算不代表目前預設。狀態問答、未來方向和當前弱幾何 policy 是不同任務，請核對 query 與 target 契約後使用。

## 1. 環境與現有資料

從套件根目錄執行；先完成 README 的安裝。若不安裝 editable package，也可 `export PYTHONPATH="$PWD:${PYTHONPATH:-}"`。所有輸出用新的目錄／實驗名，避免覆寫。

| 資料 | Train / val / test | 標註 |
|---|---|---|
| gRefCOCO 子集 | 512 / 64 / 未另建 | 人工 referring mask，train 74、val 23 筆無目標 |
| JAAD 衍生 VQA | 613 / 119 / 453 | 官方 split、人工 bbox 推導一秒方向弱標籤 |
| 店內舊 A+B／C | 54 / 8 / 0626 固定回歸 16 | 追蹤 bbox 推導方向；action/explanation 模板 |
| 最新 grounded mix | 1179 / 191 / 不混入 test | 上述 mask＋VQA，多任務 |

開發機資料根目錄是 `/workspace/datasets/`，主機對應 `/home/lester/VLMotion/datasets/`。JAAD 在 `light_foundation_vqa/jaad_temporal_vqa_v1/manifests/`，店內在 `light_foundation_vqa/splits_schema_v1/`，混合 VQA 在 `light_foundation_vqa/mixed_jaad_store_v1/`，最新多任務在 `light_foundation_vqa/grounded_vqa_v1/`。它們未加入 Git。測試結果不能從檔名推定已人工複核。

## 2. 先固定資料契約

每筆是同一指定人物、8 個採樣步、2 Hz，影像截止 NOW；未來一秒影像只供離線標註。影像有綠框指定目標；prompt 要和測試／部署一致。單張分割使用原始 referring query，不強行套長 VQA 問題。

```json
{"id":"day1_left_L1_0003","group_id":"day1","frames":["/data/000.jpg","/data/001.jpg"],"time_unit":"frames","frame_indices":[0,1],"query":"Your target-specific question","answer":"{\"motion\":\"STATIONARY\",\"action\":\"CONTINUE\",\"explanation\":\"The target remains still.\"}"}
```

上例為契約示意，正式 VQA converter 輸出八張。Mask row 改成 `mask` 路徑＋`mask_encoding: binary_255`，不一定有 answer。Mask 必須對應最後一張影像，0=背景、255=前景；loader 的 ignore value 契約與 encoding 不可混淆。不同任務可各有監督，也可同一筆同時提供 mask＋answer。

先按獨立錄影／連續事件切 train、val、test，再產生 windows。同一事件的左右視角歸同一組。不能把相鄰影格、改名副本或同片不同裁切當成獨立驗證集。Manifest 會包含絕對路徑，換機器／mount 要重新轉換。

## 3. 準備公開資料

```bash
# 固定 JAAD revision 與影片 ZIP checksum；約 3.08 GB 的影片壓縮檔。
bash scripts/prepare_jaad_vqa.sh \
  /workspace/datasets/external/JAAD \
  /workspace/datasets/light_foundation_vqa/jaad_temporal_vqa_v2

# 有實際 pixel masks 的靜態分割，固定上游 revision。
python3 -m light_foundation_vlm.download_data \
  --root /workspace/datasets/grounded_vlm_v2 grefcoco \
  --train-refs 512 --val-refs 64 --workers 6
```

JAAD 輸出 manifests、review panels、`review_queue.csv` 和來源／稽核紀錄。`apply_jaad_review` 只接受明確 approved 的人工複核項目，不會自動把 weak label 標成 human truth；用 `--help` 看 CSV 欄位與輸出參數。換 dataset output 名稱不代表新增獨立影像。

本輪已加入人物子集的來源是 [MeViS v2](https://github.com/henghuiding/MeViS)：提供動作語句與影片 masks，比靜態圖更適合時間分割。既有 downloader 可轉換小子集：

```bash
python3 -m light_foundation_vlm.download_data \
  --root /workspace/datasets/mevis_motion_v1 mevis \
  --train-videos 8 --val-videos 4 --expressions-per-video 8 \
  --clip-length 8 --clip-stride 8 --expression-policy diverse
```

使用 `train.jsonl` 與有真值的 `valid_u.jsonl`；官方 `valid` 沒公開 masks。串流 tar、mask JSON 的下載流量可能遠大於最後子集。MeViS 的整段描述不是未來 VQA 真值，不能將後半事件寫成 NOW 已知事實。多幀 MeViS 可用 `--stage temporal --time-unit frames --max-frames 8`；舊 `prepare_grounded_vqa` 的 mask 入口只收單張 gRefCOCO；本輪另以 `prepare_mevis_person`／`mix_mevis_person` 對接真正多幀 masks，見第13節。

## 4. 用自己的影片建立標籤

先用 `video_benchmark` 或 ROS `record_tracks_path` 產生真的 `tracks.jsonl`，指令見 [BASELINES](BASELINES.md)。`day1` 只是範例名稱，不會自動存在。

```bash
python3 -m lightweight_risk_vqa.make_label_template \
  --tracks /workspace/results/lightweight_risk_vqa/day1/tracks.jsonl \
  --session day1 --horizon-s 1.0 \
  --output /workspace/datasets/lightweight/day1_labels.csv
```

CSV 的 `start_s=end_s=NOW` 是輸入截止時點；`review_until_s=NOW+1` 提醒回看之後一秒。**這是空白候選表，不是訓練真值。**填入五類之一：TOWARD_CAMERA、AWAY_FROM_CAMERA、LATERAL_ACROSS_VIEW、STATIONARY、UNCERTAIN。

```csv
session,camera,track_id,start_s,end_s,label,review_until_s
day1,left,L1,3.0,3.0,TOWARD_CAMERA,4.0
```

可用既有外部 VLM 作離線候選老師：

```bash
python3 -m lightweight_risk_vqa.label_with_llm \
  --template /workspace/datasets/lightweight/day1_labels.csv \
  --tracks /workspace/results/lightweight_risk_vqa/day1/tracks.jsonl \
  --left-video /workspace/my_recordings/day1_left.mp4 \
  --right-video /workspace/my_recordings/day1_right.mp4 \
  --output /workspace/datasets/lightweight/day1_teacher.csv \
  --audit /workspace/results/day1_teacher.jsonl \
  --panels /workspace/results/day1_teacher_panels \
  --model-name llava-v1.5-7b
```

老師看 NOW／未來影格，學生只看過去。此工具只接受老師與幾何同意的非 UNCERTAIN 類別，因此困難／不確定樣本仍須另外複核，不能全丟。先前老師曾全部猜 AWAY，不能未抽查就大量當真值。

以 `make_temporal_vqa_dataset --help` 依 tracks、左右影片及 labels 匯出綠框歷史影像；按 session 合併成 store 的 `train.jsonl`／`val.jsonl`／獨立 test manifest。`--pseudo-labels` 只作弱標籤基準。每筆保留 label_source 與 horizon，不把人類 walking/standing 和相機座標移動混為同一真值。

## 5. 混合 JAAD、店內與 masks

```bash
python3 -m lightweight_risk_vqa.prepare_mixed_vqa \
  --jaad-root /workspace/datasets/light_foundation_vqa/jaad_temporal_vqa_v1/manifests \
  --store-root /workspace/datasets/light_foundation_vqa/splits_schema_v1 \
  --store-test /workspace/results/temporal_vqa/store_jaad_v1/0626_samples/samples.jsonl \
  --output /workspace/datasets/light_foundation_vqa/mixed_v2

python3 -m lightweight_risk_vqa.prepare_grounded_vqa \
  --mixed-root /workspace/datasets/light_foundation_vqa/mixed_v2 \
  --mask-root /workspace/datasets/grounded_vlm_v2/data/grefcoco \
  --output /workspace/datasets/light_foundation_vqa/grounded_v2
```

以上 store-test 是開發機已有的固定回歸示例；新研究請換未看過的獨立影片。兩個 converter 都拒絕覆寫，檢查 ID、group、影像路徑的 train/val/test 洩漏。Grounded converter 不讀 test。靜態 mask 成為單步 `[0]`，不複製成假動態。

抽樣期望 mask 30%、JAAD 35%、store 35%，VQA 各場域內均分五類。Replacement sampling 只是重播，不能當成新增事件。店內接近類原本只有 3 筆，多抽仍會過擬合。

## 6. 續訓與凍結記憶對照

```bash
bash scripts/train_grounded_vqa.sh \
  /workspace/datasets/light_foundation_vqa/grounded_v2 \
  /workspace/checkpoints/temporal_vqa_jaad_weak_v1/best \
  /workspace/results/experiments/grounded_v2 \
  2 240 --dry-run
```

檢查命令後移除 `--dry-run`。參數依序為 dataset、初始化完整 bundle、新 experiment、epochs、每 epoch draws。腳本各跑 frozen_memory／train_memory，資料抽樣與更新預算相同，另重新生成 baseline，不依賴舊 sweep 的結果檔。

輸出為 `EXPERIMENT/checkpoints/{frozen_memory,train_memory}/best/` 與 `EXPERIMENT/results/`。結果含訓練 log、baseline／full／last-only 逐筆生成、`comparison.json/md` 與選出的 validation candidate。只使用 validation，不自動跑 test 或替換 UI 模型。

`--stage joint --freeze-encoder` 更新 FPN／mask heads／projectors／ConvLSTM；`--freeze-memory` 是 control。Qwen 只解凍最後一層，LR 2e-6，其他新模組 LR 2e-5。若直接使用 `--stage temporal_vqa`，ConvLSTM 本體會凍結，`memory_grad_norm=0` 是預期行為。`--init` 重建 optimizer，不是恢復 optimizer/scheduler 狀態。

單獨訓練可直接使用 `python3 -m light_foundation_vlm.train --help`。初始權重可從既有完整 temporal bundle 載入；若從組件重建，`download_data weights` 取得 pinned Qwen3-0.6B／MobileNet 檔案，再提供 `--language-path` 與 `--vision-checkpoint`，不能把 pretrained Qwen 宣稱為已完成 VQA 對齊。

## 7. 評估與看圖

```bash
bash scripts/evaluate_vqa.sh \
  /workspace/checkpoints/grounded_vqa_v1/train_memory/best \
  /workspace/datasets/light_foundation_vqa/grounded_vqa_v1/segmentation_val.jsonl \
  /workspace/datasets/light_foundation_vqa/mixed_jaad_store_v1/val.jsonl \
  /workspace/results/evaluation/new_run
```

```
cd /workspace/ros2_ws/src/lightweight_risk_vqa

bash scripts/run_vqa_ui.sh embedded \
  --checkpoint /workspace/checkpoints/person_state_mevis_ablation_v1/with_mevis/best \
  --video /workspace/example/video/0610_take3n4.mp4 \
  --query "$(cat /workspace/datasets/light_foundation_vqa/person_state_mevis_v2/state_query.txt)" \
  --max-new-tokens 128
```

輸出 masks 的 RGB／真值／預測 HTML、逐筆 IoU 與 FP，以及自由生成 VQA 的 JSONL／HTML。Mask threshold 固定 0.5，負樣本另外看誤分割；VQA 無效 JSON 算錯，不靠修補隱藏失敗。

`temporal_vqa_infer --last-frame-only` 用同一 checkpoint 重新初始化 state、只給末幀。完整歷史要在多個 seed／新錄影上穩定較好，才有記憶有效的證據；schema 有效率差異也要一起看。`compare_vqa_runs --prediction NAME=FILE` 可比較任意多個 run，但每個必須包含同一 manifest 的完整 IDs。

## 8. 已準備的人物分割與狀態資料（2026-10-22）

以下從容器內的套件目錄執行；主機 `/home/lester/VLMotion` 對應 `/workspace`。舊模型、外部 LLaVA baseline 與五方向資料均保留。本輪先訓練現在的可見活動，不把方向模板當成活動或安全真值。

```bash
cd /workspace/ros2_ws/src/lightweight_risk_vqa
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
```

| 資料集／任務 | train | val | 真值／限制 |
|---|---:|---:|---|
| `person_state_v1` COCO 人物 union mask | 504 張（360 正／144 負） | 63 張（44 正／19 負） | 原有圖片，重新由 COCO person annotations 合併，非新增獨立圖片 |
| `person_state_v1` JAAD 活動＋時間變化 | 1,270 clips | 243 clips | 人工 walking／standing 標註；其中 836／156 clips 為新擷取影像 |
| 店內 state 待複核 | 54 clips | 8 clips | `store_review.csv`；未填寫的活動不進入訓練 |
| SAM3 店內 mask 候選 | 54 clips | 8 clips | 62 張末幀 masks、overlay、來源與版本，未人工複核 |
| `person_state_teacher_v1` 弱監督版本 | 上述 train＋54 時序 mask clips | 與 clean 版本相同 | 只加入 SAM3 train masks，20% sampling；不使用 8 筆教師 val 作評分真值 |

資料根目錄是 `/workspace/datasets/light_foundation_vqa/`。每個完成的版本有 `READY`、`summary.json`、`sampling_weights.json`、train/val manifests、各任務 manifests 與 `state_query.txt`。資料不進 Git；保留原資產路徑，搬機器時需一起搬原資料並重建路徑。

JAAD 活動 train 分布：WALKING/STEADY 937、WALKING/STARTED_WALKING 222、STANDING/STEADY 75、STANDING/STOPPED_WALKING 36；val 為 191、36、9、7。新窗口仍來自原有錄影，不能宣稱增加了同等數量獨立事件。按官方 recording split 分組；COCO 按 image ID 分組；沒有讀 test。已被舊模型使用的 val 不算新盲測。

### 8.1 三個任務如何監督模型

- **人物 mask：**query=`the people`，標註整張最後影格中所有人物的 union。COCO 的原本「找不到某個人」不當作整張圖無人。COCO 真正沒有人物標註的圖片才作此任務負例。
- **現在活動：**綠框指定一個人；JAAD 已知的 walking／standing 轉為 `current_activity`。沒有人工活動標註的軌跡排除，不用框移動推測動作。
- **時間變化：**只看過去到 NOW 的八個樣本，標 `STEADY / STARTED_WALKING / STOPPED_WALKING`。未知或多次變化的序列不強迫轉成單次事件；語意是「取樣歷史中的變化」。

```json
{"current_activity":"WALKING","observed_transition":"STARTED_WALKING","motion":"UNCERTAIN","action":"OBSERVE","explanation":"The target is walking; sampled activity transition: started_walking."}
```

這個任務的 `motion=UNCERTAIN`、`action=OBSERVE` 是明確的任務契約：沒有教未來方向或安全決策。Explanation 由已知活動產生短模板，尚非自由描述真值。拿取商品、伸手、彎腰等欄位可由 reviewed 店內資料擴充；目前 JAAD 不提供這些訓練答案。不得用 token accuracy 證明已學會動作或安全判斷。

### 8.2 從本機來源重建資料

輸出目錄必須尚不存在；失敗的匯出不會有 `READY`。

```bash
python3 -m lightweight_risk_vqa.prepare_person_state \
  --mask-root /workspace/datasets/grounded_vlm_v2/data/grefcoco \
  --mixed-root /workspace/datasets/light_foundation_vqa/mixed_jaad_store_v1 \
  --jaad-root /workspace/datasets/external/JAAD \
  --output /workspace/datasets/light_foundation_vqa/person_state_v2 \
  --per-video 24 --seed 42
```

`--per-video` 是每個 train/val 影片額外輸出的上限；提高可擷取更多窗口，但不會新增人物／錄影。候選按活動與變化類別輪流挑選；來源 annotation SHA256 存於 `sources.jsonl`。要改善店內泛化，仍應新增不同日期、人物、遮擋、相機位置的獨立錄影。

### 8.3 擴大真實人物圖片

額外 COCO 匯出器沿用官方 S3 HTTPS 圖片端點與原始人工標註，排除既有567個 COCO image IDs，只從 gRefCOCO 官方 train／val 對應影像取樣，不讀 test。新增比例80%有人、20%無人物標註，原始 train/val 界線不變。

```bash
python3 -m lightweight_risk_vqa.expand_person_masks \
  --dataset /workspace/datasets/light_foundation_vqa/person_state_teacher_v1 \
  --coco-root /workspace/datasets/grounded_vlm_v2/data/grefcoco \
  --output /workspace/datasets/light_foundation_vqa/person_state_expanded_v1 \
  --additional-train 2000 --additional-val 200 --workers 24
```

完成後為 **3,828 train / 506 val**：2,504張真實 mask train（1,960正／544負）、263張真實 mask val（204正／59負），另有1,270／243 state clips，以及54個 train SAM3時序 mask clips。每張新圖片記錄 URL／SHA256，整批保留 export plan、標註檔 SHA256 與 READY。中斷可加 `--resume`，必須相同資料與取樣計畫；已完成版本不能續寫。

這是擴大的驗證協定，不能拿263張 mask數字與舊63張直接說模型進步；應把待比較模型全部重跑同一份 manifest。新 val 也會用於選模，後續仍要另建盲測。

## 9. 離線教師與店內人工複核

SAM3 只在資料準備時使用；輕量推論不載入它。既有 62 筆候選已在 `person_state_v1_sam3_candidates/`；包含 `masks/`、`overlays/`、`candidates.jsonl`、`review.csv`。`index.html` 可瀏覽遮罩。僅使用本機 HF 快取，不隱式下載模型。

```bash
python3 -m lightweight_risk_vqa.teacher_person_masks \
  --dataset /workspace/datasets/light_foundation_vqa/person_state_v1 \
  --output /workspace/datasets/light_foundation_vqa/person_state_v2_sam3_candidates
```

已另備妥可直接訓練的弱監督版本 `person_state_teacher_v1`。其建法為：

```bash
python3 -m lightweight_risk_vqa.mix_person_teacher \
  --dataset /workspace/datasets/light_foundation_vqa/person_state_v1 \
  --teacher /workspace/datasets/light_foundation_vqa/person_state_v1_sam3_candidates \
  --output /workspace/datasets/light_foundation_vqa/person_state_teacher_v2 \
  --train-weight 0.2
```

這是**顯式弱監督**，不是人工真值；`human_reviewed=false` 保留。八張歷史餵給學生，只有最後一張的教師 mask 作分割 loss，所以能監督時序 mask 路徑。未自動加入教師 val，避免拿同一老師的錯誤當驗證成績。降低／排除弱監督的對照用原 `person_state_v1`。

### 9.1 複核遮罩

打開教師 `index.html`，檢查頭、手、腳、遮擋、小人物與背景誤分割。所有人物 union，與綠框單人的 state 是不同 target。必要時修正 PNG；0=背景、255=人物。

在教師 `review.csv` 逐筆填 `review_status=accepted`、`reviewer`，可改 `mask_path` 指向修正檔。未確認保留 pending。匯入**乾淨版**的新版本：

```bash
python3 -m lightweight_risk_vqa.apply_person_mask_review \
  --dataset /workspace/datasets/light_foundation_vqa/person_state_v1 \
  --teacher /workspace/datasets/light_foundation_vqa/person_state_v1_sam3_candidates \
  --reviews /workspace/datasets/light_foundation_vqa/person_state_v1_sam3_candidates/review.csv \
  --output /workspace/datasets/light_foundation_vqa/person_state_reviewed_v1
```

### 9.2 複核活動與文字

打開 `person_state_v1/store_review.html`，按歷史順序查看綠框目標，填 `store_review.csv`：

- `current_activity`：WALKING、STANDING、SITTING、BENDING、REACHING、PICKING_ITEM、PLACING_ITEM、OTHER、UNCERTAIN。
- `observed_transition`：STEADY、STARTED_WALKING、STOPPED_WALKING、UNCERTAIN；不是判斷未來一秒。
- `explanation`：一句看得到的證據；不要臆測顧客意圖或機器人安全。
- `review_status=accepted`、`reviewer` 必填；split 不可更動。
- `mask_path` 選填，**必須是同一綠框目標的單人 mask**。教師的所有人 union 不可填在這欄。提供後該筆會同時監督 mask＋answer；mask評估會明確移除答案輸入，避免把真值提供給模型。

```bash
python3 -m lightweight_risk_vqa.apply_person_state_review \
  --dataset /workspace/datasets/light_foundation_vqa/person_state_v1 \
  --reviews /workspace/datasets/light_foundation_vqa/person_state_v1/store_review.csv \
  --output /workspace/datasets/light_foundation_vqa/person_state_store_v1
```

匯入器排除 pending、拒絕重複 ID、跨 split 來源及無 reviewer 的 accepted 資料。若要同時加入 reviewed mask/state，可把第一個匯入結果當下一個匯入器的 `--dataset`；每一步用新目錄。老師 VLM 可輔助建議活動，但當前未自動產生這些店內活動真值，仍需複核後匯入。

## 10. 聯合訓練、模組更新與對照

新增入口是 `scripts/train_person_state.sh`，舊 `train_grounded_vqa.sh` 留作原五方向對照。

| 模組 | 本輪設定 |
|---|---|
| MobileNetV3 encoder／BN | 凍結，減少成本與小資料過擬合 |
| FPN、visual projector、visual positions／seg embedding | 更新 |
| ConvLSTM、memory projector／position、temporal-to-pixel | 更新；frozen 對照只凍結 ConvLSTM |
| mask query、pixel decoder、mask bias、presence head | 更新；BCE＋Dice＋presence loss |
| Qwen3-0.6B | 只更新最後一層，LR 2e-6；其餘凍結 |
| 其他 trainable modules | LR 2e-5；答案 token cross entropy |
| 外部 LLaVA／SAM3 教師 | 不參與此訓練、不在輕量推論載入 |

Clean sampling：mask 正／負各25%；四組 activity/transition 各12.5%。Teacher 版本：上列合計80%、SAM3 train 時序 masks20%。這是抽樣比例，不是新增樣本數。

Train-only augmentation：同一 clip 共用水平翻轉、亮度與對比；mask 同步翻轉。只有 `augmentation_safe_flip=true` 的人物 union／無方向 state records 啟用；不翻轉任意含 left/right 的 referring query。Val 不增強。

第一輪 clean 實驗為三個 epochs、每 epoch600 draws，共1,800更新；不是走完1,774筆資料三次。比較應保持相同 init、seed、draws、epochs。

```bash
# 人工標註 clean 對照；NEW_RUN 不可已存在
bash scripts/train_person_state.sh \
  /workspace/datasets/light_foundation_vqa/person_state_v1 \
  /workspace/checkpoints/grounded_vqa_v1/train_memory/best \
  /workspace/checkpoints/person_state_v1/train_memory 3 600 train

# 加入店內 SAM3 弱監督，相同初始化與預算
bash scripts/train_person_state.sh \
  /workspace/datasets/light_foundation_vqa/person_state_teacher_v1 \
  /workspace/checkpoints/grounded_vqa_v1/train_memory/best \
  /workspace/checkpoints/person_state_teacher_v1/train_memory 3 600 train

# 凍結 ConvLSTM 對照；其餘設定相同
bash scripts/train_person_state.sh \
  /workspace/datasets/light_foundation_vqa/person_state_teacher_v1 \
  /workspace/checkpoints/grounded_vqa_v1/train_memory/best \
  /workspace/checkpoints/person_state_teacher_v1/frozen_memory 3 600 frozen
```

末尾加 `--dry-run` 前需填滿三個選項，例如 `3 600 train --dry-run`。正式擴大可先改成 `5 2400 train`，但先看生成式驗證，再增加資料；不是 epochs 越多越好。`--init` 是繼承權重、建立新 optimizer，不是精確中斷恢復。

本輪更新 16,762,882／599,781,426 個參數（約2.8%）；encoder 和大部分 Qwen 凍結。輸出有 `best/` 完整 bundle、`training_args.json`、`trainable_parameters.json` 和訓練指標。Log 的 joint `memory_grad_norm` 應在有監督的步驟出現非零；frozen 對照為零。只有最後一層 LLM 更新，不代表 ConvLSTM 可以凍結不學時間。Best 依分組驗證 loss 選，仍要另看 mask 和生成式 state 指標。

### 10.1 評估與 UI

```bash
bash scripts/evaluate_person_state.sh \
  /workspace/checkpoints/person_state_teacher_v1/train_memory/best \
  /workspace/datasets/light_foundation_vqa/person_state_teacher_v1 \
  /workspace/results/person_state_teacher_v1/evaluation
```

輸出 `masks/` 的 HTML／IoU／空目標誤分割，以及 `state.full.jsonl`、`state.last.jsonl`、`state.repeat.jsonl`。三者都有 activity／transition accuracy、macro-F1、schema 成功率和 latency；無效或缺欄的分類答案算錯。**motion/action 在 state 任務是固定值，不能當準確率進步。**最後一張 ablation 測是否需要歷史；repeat-last 保留八步但重複 NOW，控制 recurrent warmup／步數差異，再比較 frozen-memory；泛化還需新錄影與不同 seed。

UI 已支援 state 欄位與 OBSERVE，測試指令：

```bash
bash scripts/run_vqa_ui.sh embedded \
  --checkpoint /workspace/checkpoints/person_state_teacher_v1/train_memory/best \
  --video /workspace/example/video/0610_take3n4.mp4 \
  --query "$(cat /workspace/datasets/light_foundation_vqa/person_state_teacher_v1/state_query.txt)" \
  --max-new-tokens 128
```

Raw-video UI 沒有自動指定綠框目標；state 訓練有綠框，正式評分仍用 manifest。可確認即時記憶、mask、回答與 data health，但不能將這段 raw demo 當成已解決店內準確率的證據。state 預設現在自動分開 query：mask 用 `the people`，VQA 用 state 問題；復用同一次 forward 的80個 visual/memory prefix tokens，不重跑影像 encoder、不重複更新 ConvLSTM。camera 決策模式的其他 query 也固定 mask 用 `the people`；model 決策模式的其他 query 沿用原行為。

## 11. DATA_LOSS 與人物危險分開

已實作 `availability.py`，整合／外接 UI 共用資料健康檢查；ROS bbox baseline 的 `direct_temporal.py` 與原 `future_risk_vqa` VQA policy／GUI 也已修改。可靠幾何已確認的 STOP 保留優先權。

| 條件 | 輸出 |
|---|---|
| 影像缺失／過期、模型／worker 失效、回答 JSON/schema 無效 | **DATA_LOSS — UNABLE TO ASSESS**，灰色；不顯示舊答案為現在決策 |
| VQA 對應舊 loop／query，或 evidence 已過期 | **DATA_LOSS**；保留 raw response 供 debug |
| ROS temporal input 缺失／過期／對不齊或低可靠性 | **DATA_LOSS**，`data_available=false` |
| 暫停／影片播完，或 temporal history 暖機 | OBSERVE，不能當成可通行 |
| 新鮮、schema 正確的模型人物危險建議 | 保留 DANGER_WARNING／STOP，標為 model research hint |
| 本輪 state-only 回答 | OBSERVE；不宣稱安全決策 |

UI 預設 `--max-frame-age-s 2`、`--max-result-age-s 8`，後者按**輸入 evidence 的年齡**計算，包含 inference 等待時間。可依 val 實測調整。外部 worker 現在收到同一分割影格的 RGB＋overlay，避免不同時間畫面混配。

DATA_LOSS 代表系統無法判斷，不是「沒有人」，也不是危險事件真值。它不寫進 activity 訓練答案；這是執行環境狀態，不能靠模型猜。原五方向 checkpoint 的語意 UNCERTAIN／DANGER_WARNING 仍是原模型建議；資料健康層不會把有效語意答案任意改成故障。

尚未實作機器人的實體路徑碰撞 policy。要訓練／決定 STOP 或 CONTINUE，需新增機器人計畫、校正區域和人物關係的標註，並放進實際 query/context；不能由「站立／側移／接近相機」直接推出可通行。

## 12. 程式入口與擴大資料

| 入口 | 用途 |
|---|---|
| `prepare_person_state.py` | COCO person unions、JAAD 人工活動、新歷史片段、複核清單 |
| `expand_person_masks.py` | 額外官方 COCO 圖片、人工人物遮罩、可驗證續傳 |
| `person_state.py` | state prompt、活動／轉換標籤與 schema |
| `teacher_person_masks.py` | 快取 SAM3 離線人物 mask 候選 |
| `mix_person_teacher.py` | 僅 train 的教師弱監督版本 |
| `apply_person_mask_review.py` | 已複核所有人物 mask 匯入 |
| `apply_person_state_review.py` | 已複核單人活動、可選 paired mask 匯入 |
| `light_foundation_vlm/data.py` | 真實 clip／mask／answer loader、同步 augmentation |
| `scripts/train_person_state.sh` | joint 更新與 frozen-memory 對照 |
| `scripts/evaluate_person_state.sh` | 人物分割＋生成活動／時間變化評估 |
| `summarize_person_state.py` | 僅接受相同完整 validation IDs 的跨 run 比較 |
| `availability.py` | 資料故障與人物危險分離 |

接下來最有價值的是補店內新錄影與62筆候選複核。若擴大公開資料，可再下載 COCO 人物圖片、加入 MeViS 中人物相關的真實時序 masks；本輪已額外下載2,200張 COCO 圖片；MeViS 人物時序子集已另外匯入並建立第13節的對照。沿用 `ClipDataset` 的時間、目標與 mask 契約，與現有 COCO image IDs、JAAD video IDs 統一查洩漏。MeViS 動作分割不等於未來安全標註。


## 13. MeViS 人物時序分割＋相同預算對照

**資料血緣分開記錄：**原始 `temporal_best/manifest.json` 顯示舊 temporal 階段曾以 MeViS 訓練；此前 COCO／JAAD／店內混合集本身沒有 MeViS。本輪明確下載固定 revision `ed94977691d86cb670c47815488db6f46709a40d`，加入新的 train manifests。舊權重曾看過哪些影片未附完整清單，因此不能將這份 val 宣稱為全新的盲測。

資料來源：[MeViS 官方說明](https://github.com/henghuiding/MeViS)、[固定版本 MeViSv2](https://huggingface.co/datasets/FudanCVL/MeViSv2/tree/ed94977691d86cb670c47815488db6f46709a40d)。使用有真實 masks 的 train／valid_u；不拿官方無真值的 valid 來做本機評分。

```bash
python3 -m lightweight_risk_vqa.prepare_mevis_person \
  --root /workspace/datasets/mevis_person_v2 \
  --train-videos 64 --val-videos 12 --expressions-per-video 8

python3 -m lightweight_risk_vqa.mix_mevis_person \
  --dataset /workspace/datasets/light_foundation_vqa/person_state_expanded_v1 \
  --mevis /workspace/datasets/mevis_person_v2/data/mevis \
  --output /workspace/datasets/light_foundation_vqa/person_state_mevis_v2 \
  --weight 0.2
```

本輪64個 train影片匯出211筆（112正 mask／99空目標），12個valid_u影片匯出45筆（36正／9空目標）；混合後共4,039 train／551 val。實際影片清單與 annotation SHA256 見 `mevis_person_v2/data/mevis/report.json`。第一批8／4影片的小型匯出僅作流程確認，實驗使用擴大版本。按描述的主詞保守選 man／woman／person 等人物語句，避免把「走向人的狗」當人物；這是文字篩選，不冒充人工重新確認的分類真值。保留原 query 與對應 annotation IDs，不能改成泛用 `the people` 卻沿用原單人 mask。

每個描述使用八張均勻取樣、涵蓋整段已觀察前綴的圖片，NOW 為影片最後一格；mask 對應末格。原描述可能提到整段事件，因此不把它轉成 NOW 活動答案或未來一秒標籤。原生 frame indices 另存，模型輸入改為0..7 frame steps；未知 FPS 不冒稱秒數，也避免大 frame gap 讓 ConvLSTM 每步重置。動作語句可能含 left/right，這組不啟用水平翻轉。

### 13.1 實際控制組與 MeViS 組

兩組以 `/workspace/checkpoints/person_state_teacher_v1/train_memory/best` 起始，seed42、2 epochs×1,200 draws=2,400更新，batch1；同樣更新第10節的16.76M參數。不是全量模型重訓，也不是只更新 LSTM。每 epoch 的1,200次 replacement draws 不等於走完4,039筆資料；這是固定更新次數的初輪實驗。

```bash
# 不加入 MeViS
bash scripts/train_person_state.sh \
  /workspace/datasets/light_foundation_vqa/person_state_expanded_v1 \
  /workspace/checkpoints/person_state_teacher_v1/train_memory/best \
  /workspace/checkpoints/person_state_mevis_ablation_v1/without_mevis 2 1200 train

# 加入 MeViS
bash scripts/train_person_state.sh \
  /workspace/datasets/light_foundation_vqa/person_state_mevis_v2 \
  /workspace/checkpoints/person_state_teacher_v1/train_memory/best \
  /workspace/checkpoints/person_state_mevis_ablation_v1/with_mevis 2 1200 train
```

MeViS佔20% sampling，其餘來源比例乘0.8；更新次數相同，**不是完全相同 FLOPs 或相同來源曝光次數**。每輪實際抽樣存於 metrics。兩組都只依共同 `segmentation,state` 驗證群的等權 loss 選 best，MeViS額外的 val loss 不改變其中一組的選模目標。

MeViS 本身沒有本專案 VQA 的活動／風險答案；這組透過真實 masks 訓練動作語句的 grounding、ConvLSTM與 mask decoder；JAAD 的答案 loss 持續教 Qwen 回答。外部 LLaVA和SAM3不參與梯度更新。

### 13.2 如何判斷是否更好

對每個 arm 執行共同 COCO／JAAD 驗證：

```bash
bash scripts/evaluate_person_state.sh \
  /workspace/checkpoints/person_state_mevis_ablation_v1/with_mevis/best \
  /workspace/datasets/light_foundation_vqa/person_state_expanded_v1 \
  /workspace/results/person_state_mevis_ablation_v1/with_mevis

for history in full last repeat; do
  python3 -m lightweight_risk_vqa.evaluate_grounded_masks \
    --checkpoint /workspace/checkpoints/person_state_mevis_ablation_v1/with_mevis/best \
    --manifest /workspace/datasets/light_foundation_vqa/person_state_mevis_v2/mevis_val.jsonl \
    --output "/workspace/results/person_state_mevis_ablation_v1/with_mevis/mevis_$history" \
    --device cuda:0 --history "$history"
done
```

把 checkpoint／output 的 `with_mevis` 換成 `without_mevis` 重跑；兩組使用完全相同評分 IDs、query、mask與320×320／threshold0.5。比較 MeViS正例mIoU、空目標FP、COCO人物mIoU／FP、JAAD activity與transition macro-F1、schema成功率，不能只挑改善的一個數字。MeViS full／last／repeat mask結果及 state同樣三種歷史控制，分開看記憶是否有幫助。

本輪品質評估曾與訓練並行，所記錄 latency 不作受控速度比較。要量正式推論延遲，停止其他GPU工作後用同一預處理、prompt、輸出token與硬體重新量測。

重新完整重現兩組的單一入口（新的 experiment 目錄）：

```bash
bash scripts/run_mevis_comparison.sh \
  /workspace/datasets/light_foundation_vqa/person_state_expanded_v1 \
  /workspace/datasets/mevis_person_v2/data/mevis \
  /workspace/checkpoints/person_state_teacher_v1/train_memory/best \
  /workspace/checkpoints/person_state_mevis_reproduce_v1 2 1200
```

實驗結果與是否適合當新預設請看 [RESULTS](RESULTS.md) 的本輪記錄；若分割改善、活動或誤分割退步，應保留兩模型並針對弱項補資料，不直接取代原 baseline。


### 13.3 實際權重更新稽核

```bash
python3 -m lightweight_risk_vqa.audit_finetune \
  --initial /workspace/checkpoints/person_state_teacher_v1/train_memory/best \
  --checkpoint /workspace/checkpoints/person_state_mevis_ablation_v1/with_mevis/best \
  --output /workspace/results/person_state_mevis_ablation_v1/with_mevis/finetune_audit.json
```

除了 `requires_grad` 清單，直接比較初始和最佳 bundle 的 tensors，列出各模組實際變動、宣告可訓練但未變動、以及意外變動的 frozen tensors。FPN 中未被 loss 使用的分支及部分低精度 norm tensors 可能沒有實際數值變動；不能把16.76M可訓練參數解讀為每個元素都已改值。實際驗證結論存於 RESULTS 與 audit JSON。


### 13.4 測試 MeViS 微調模型的 UI

```bash
bash scripts/run_vqa_ui.sh embedded \
  --checkpoint /workspace/checkpoints/person_state_mevis_ablation_v1/with_mevis/best \
  --video /workspace/example/video/0610_take3n4.mp4 \
  --query "$(cat /workspace/datasets/light_foundation_vqa/person_state_mevis_v2/state_query.txt)" \
  --max-new-tokens 128
```

不加MeViS的控制組可把 checkpoint 中的 `with_mevis` 換成 `without_mevis`。兩者都是 ConvLSTM＋內建Qwen完整bundle，不啟動SAM3或外部LLM worker。state query 下的 mask 自動使用 `the people`，與 VQA 共用視覺／記憶 prefix。Raw demo 仍有綠框目標指定的 domain gap；可靠準確率以固定 manifest評估為準。

上述舊 state 任務預設採 model action，仍為 OBSERVE。要測「模型判讀人物移動／接近意圖／風險」的兩種新接口，改用 `run_vqa_ui.sh vqa-only` 或 `run_vqa_ui.sh yolo-assisted`，**不傳 state query 或 ROI 設定**；完整指令與新 robot schema 的訓練資料需求見 [INTERFACES](INTERFACES.md#convlstm-vqa-ui無固定區域的兩種模式)。新接口的 action 由 Qwen 回答，YOLO 只作輔助證據；舊 state checkpoint 可能無法遵守新 schema，屬待續訓能力，不能當作已完成意圖／風險訓練。
