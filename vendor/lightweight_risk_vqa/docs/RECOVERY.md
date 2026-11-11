# Recovery 資料與重現操作

本文件只保留可執行的資料準備、複核及訓練流程。**模型設計、dataset 數量、微調模組、結果與完整 prompt 的主要入口是 [整合報告](MODEL_DATASET_REPORT.md)**；日常啟動見[套件 README](../README.md)。歷史對照見 [RESULTS](RESULTS.md)。

已完成的本機資產以 `robot_vqa_recovery_v1` 命名。新資料或新訓練請另存版本目錄；以下包含重建命令，不代表新 clone 自帶圖片、權重或教師模型。所有方向／碰撞真值限制見整合報告第 2、4 節。

## 資料準備、教師與分階段訓練

以下在現有容器執行；資料輸出與 checkpoint 已存在時請使用新的版本目錄，訓練器不覆蓋既有實驗。這組腳本是本輪固定 pilot，擴大資料後應另存新版本再調整預算。

```bash
cd /workspace
export PYTHONPATH=/workspace/ros2_ws/src/lightweight_risk_vqa:${PYTHONPATH:-}

# 可重用下載；COCO zip 檢查 SHA256，教師固定 Hugging Face revision
python3 ros2_ws/src/lightweight_risk_vqa/scripts/prepare_recovery_assets.py \
  --root /workspace/datasets/robot_vqa_recovery_v1/source \
  --teacher /workspace/models/Qwen3-VL-2B-Instruct

# 以下輸出已經備妥；重建時先換一個新的 root
python3 -m lightweight_risk_vqa.recovery_data \
  --base /workspace/datasets/light_foundation_vqa/person_state_expanded_v1 \
  --instances /workspace/datasets/grounded_vlm_v2/data/grefcoco/source/instances.json \
  --captions /workspace/datasets/robot_vqa_recovery_v1/source/captions_train2014.json \
             /workspace/datasets/robot_vqa_recovery_v1/source/captions_val2014.json \
  --output /workspace/datasets/robot_vqa_recovery_v1/grounding

python3 -m lightweight_risk_vqa.prepare_retail_recovery --phase extract \
  --output /workspace/datasets/robot_vqa_recovery_v1/retail
python3 -m lightweight_risk_vqa.prepare_retail_recovery --phase teacher \
  --output /workspace/datasets/robot_vqa_recovery_v1/retail
python3 -m lightweight_risk_vqa.teacher_person_masks \
  --dataset /workspace/datasets/robot_vqa_recovery_v1/retail \
  --output /workspace/datasets/robot_vqa_recovery_v1/retail/sam3
python3 -m lightweight_risk_vqa.prepare_retail_recovery --phase assemble \
  --output /workspace/datasets/robot_vqa_recovery_v1/retail
python3 -m lightweight_risk_vqa.prepare_causal_motion \
  --source /workspace/datasets/light_foundation_vqa/grounded_vqa_v1 \
  --jaad /workspace/datasets/external/JAAD \
  --retail /workspace/datasets/robot_vqa_recovery_v1/retail \
  --output /workspace/datasets/robot_vqa_recovery_v1/causal_motion
python3 -m lightweight_risk_vqa.prepare_recovery_stages \
  --root /workspace/datasets/robot_vqa_recovery_v1 \
  --source /workspace/datasets/light_foundation_vqa/person_state_mevis_v2

# 準備完成後：依序 A、同預算 control、B、C
bash ros2_ws/src/lightweight_risk_vqa/scripts/train_recovery.sh all
# 或單獨執行 train_recovery.sh a|control|b|c

# GPU 沒有其他工作時，依序評估；避免污染 latency
bash ros2_ws/src/lightweight_risk_vqa/scripts/evaluate_recovery.sh
```

這些 JSONL 只將 query 與 ≤NOW 的 frames 輸入模型；answer、event_label、mask 是訓練 target。causal export 重新從過去框產生方向，不沿用舊資料中的 future labels。`frame_indices` 是模型步數；原影片時間另放 `capture_timestamps_s`／`source_frame_indices`，不能把它誤當公尺速度。

若要從本輪 C 繼續較大預算訓練，已有入口；以下會讀相同資料，以較小 LR、最多 6 epochs × 12,000 draws 續訓，patience=2。它不是本輪已執行的結果，請先補入複核過的新片段再跑，避免只重複弱標籤。輸出必須是新目錄。

```bash
bash ros2_ws/src/lightweight_risk_vqa/scripts/continue_recovery.sh \
  /workspace/datasets/robot_vqa_recovery_v1 \
  /workspace/checkpoints/robot_vqa_recovery_v1/c_policy/best \
  /workspace/checkpoints/robot_vqa_recovery_v2/continued 6 12000
```

要增加本輪原始零售 clips，在 `prepare_retail_recovery.py` 的錄影清單加入**新的錄影日**，依日期分開 train、development val、未看過的 test，再用新的 `--output` 重跑教師與資料合併。現有四支影片很短，單純降低 clip stride 只會增加重疊樣本；優先新錄遠離、橫越、起步、停止、遮擋、機器人自身移動，並複核失敗案例。現在這些情境尚沒有完整的人工覆蓋統計，`camera_motion=unverified` 不能當相機運動真值。

## 人工複核接回續訓

目前CSV全為pending，沒有假造人工驗收。本輪依你的授權用機器弱標註訓練。後續可複製已備妥完整欄位的`retail/review_template_v2.csv`成`review_human_v1.csv`，看`review.html`的8張歷史，再將完成的列設為`status=accept`或`reject`，填`reviewer`；accept必須明填`motion_override`，即使同意候選也要填其enum。`observation_override`可改描述。

可額外加`person_count_override`欄位改人數。若填`collision_label`，須另加並填`explanation_override`，且按明確定義核對可見風險；轉換器不會由框大小自動新增碰撞真值。原SAM3 mask仍是弱標註，不因接受一列motion就升格成人工mask。

```bash
python3 -m lightweight_risk_vqa.apply_recovery_review \
  --data /workspace/datasets/robot_vqa_recovery_v1 \
  --reviews /workspace/datasets/robot_vqa_recovery_v1/retail/review_human_v1.csv \
  --output /workspace/datasets/robot_vqa_recovery_reviewed_v1

bash ros2_ws/src/lightweight_risk_vqa/scripts/continue_recovery.sh \
  /workspace/datasets/robot_vqa_recovery_reviewed_v1 \
  /workspace/checkpoints/robot_vqa_recovery_v1/c_policy/best \
  /workspace/checkpoints/robot_vqa_recovery_v2/reviewed 6 12000
```

這兩行是**未執行的人工複核後流程**，不是本輪結果。轉換器產生新`stages/c_train.jsonl`、`c_val.jsonl`，不搬影像、不改原始資料；以`human_reviewed_fields`記錄各欄來源，其他欄保持弱標註。全pending、缺reviewer、改split或無理由的碰撞標籤會明確拒絕。現有5類motion head仍只會方向，不會因新增Qwen的STOP答案就成為碰撞分類頭。

## Balanced 與評估重跑

```bash
python3 -m lightweight_risk_vqa.prepare_recovery_stages \
  --root /workspace/datasets/robot_vqa_recovery_v1 \
  --source /workspace/datasets/light_foundation_vqa/person_state_mevis_v2 --balanced-state
bash ros2_ws/src/lightweight_risk_vqa/scripts/train_recovery.sh balanced
bash ros2_ws/src/lightweight_risk_vqa/scripts/evaluate_balanced_recovery.sh
```

`train_recovery.sh all` 依序執行 A、control、B、C；balanced 另外執行。`RECOVERY_DATA_ROOT`、`RECOVERY_MODEL_ROOT`、`RECOVERY_RESULT_ROOT` 可分別指定新路徑。只換資料路徑不會自動搬移 JSONL 引用的圖片。

## 證據與檢查工具

| 要檢查的內容 | 保留工具 |
|---|---|
| 資料資產、group 拆分、target 契約 | `prepare_recovery_stages --audit-only` |
| 模組實際是否更新 | `audit_finetune` |
| 換圖／黑圖、free／finite、history ablation | `audit_visual_grounding` |
| 8／16／32 步批次與串流一致性 | `audit_streaming_memory` |
| 原生 VLM 參考 | `audit_native_teacher` |
| 小型分類頭與弱方向代理 | `evaluate_fast_motion` |
| 相同影像的 prompt 配對 | `audit_robot_prompt`；詳見 [PROMPT](PROMPT.md) |
| 本機帶圖片報告 | `render_recovery_report` |
| 真實影片＋Qt offscreen 功能測試 | `scripts/smoke_recovery_ui.py` |

完整結果留在 `/workspace/results/robot_vqa_recovery_v1/` 與 `/workspace/results/robot_vqa_prompt_v2/`，不進 Git。保留失敗 raw／消融供後續開發，不用 schema 成功率或 token accuracy 代替理解能力。
