# 歷史比較與研究結論

本文件保留目前模型繼承的歷史 baseline、失敗結論與實驗證據；**不是目前模型排行或日常啟動指南**。最新 A／B／C、dataset、prompt 和效能表格以 [整合報告](MODEL_DATASET_REPORT.md) 為主要入口。現行 UI、分割開關見[套件 README](../README.md)，重現步驟見 [RECOVERY](RECOVERY.md)。

2026-10-22 的區域／框 UI、反覆 prompt 修改與當時測試數量已不再逐段重複列出；相關原始輸出仍留在本機 `results/camera_policy_ui_v1/` 等結果目錄。現行 robot 模式沒有固定 ROI／框繪製，舊規則僅保留顯式 legacy 比較入口。

## 保留的歷史權重與外接 baseline

本機容器 `/workspace` 對應主機 `/home/lester/VLMotion`。以下只是開發機資產清單，新 clone 需自行提供 bundle。

| 用途 | 本機 bundle |
|---|---|
| 原始單張分割參考 | `/workspace/light_foundation_vlm_deployment_v1/models/segmentation_best` |
| 串接 baseline 的 ConvLSTM 分割 | `/workspace/light_foundation_vlm_deployment_v1/models/temporal_best` |
| 外部回答模型 | worker 註冊的 `llava-v1.5-7b` 或本地 RoboPoint checkpoint |
| JAAD VQA 初始化 | `/workspace/checkpoints/temporal_vqa_jaad_weak_v1/best` |
| 原五方向整合式研究模型 | `/workspace/checkpoints/grounded_vqa_v1/train_memory/best` |
| 同預算凍結記憶 control | `/workspace/checkpoints/grounded_vqa_v1/frozen_memory/best` |

原始 segmentation `model.pt` SHA256：`88b9132ccab41a5360a2d793e8a2471585096f07109f86919618ff9c3efd1ed4`。
原始 temporal：`98f72afa0aef97d24eff3255d4097b71751c6d51578823d29de80c76143e4905`。
JAAD 初始化：`81365d9ea47797b1189adf4680214de6a8d9d2248d5f9066734082eef656a184`。

## 早期 grounded_vqa_v1 分割／VQA 對照

資料：gRefCOCO 512 train／64 val、JAAD 613 train／119 val、店內 54 train／8 val。兩組同一起點、seed 42，各 480 updates；相同實際抽樣 123 mask、179 JAAD、178 store。凍結 MobileNet，更新 FPN、mask heads、projectors、Qwen 最後一層；唯一組間差異是 ConvLSTM 是否更新。

Checkpoint 用兩場域 validation language loss 等權平均選擇；表格是所選 checkpoint，不是各任務分開挑最高數字。方向指標是與 bbox 幾何弱標籤的符合率，並非人工風險真值。

| 模型／輸入 | JAAD motion acc | JAAD macro-F1 | Store motion acc | Store macro-F1 | Mask mIoU |
|---|---:|---:|---:|---:|---:|
| 原始單張 segmentation | — | — | — | — | 31.7% |
| JAAD VQA 起點／8 張 | 42.9% | 18.1% | 0/8 | 0% | 12.7% |
| 凍結記憶／8 張，epoch 1 | 37.0% | 19.0% | 0/8 | 0% | 14.2% |
| 同 checkpoint／只看末幀 | 45.4% | 20.1% | 0/8 | 0% | 未測 |
| 解凍記憶／8 張，epoch 2 | 31.1% | 18.5% | 1/8 | 20.0% | 32.8% |
| 同 checkpoint／只看末幀 | 26.9% | 16.0% | 2/8 | 10.0% | 未測 |

Mask mIoU 只計 41 筆有目標 references；其餘 23 筆無目標，解凍組的 any-pixel FP 仍為 100%。32.8% 主要恢復 VQA 微調後流失的分割能力，和原始分割基線相近；不是店內 mask 品質證據。官方 val 曾用於模型開發，並非新盲測。

解凍組完整歷史 schema 有效率 100%，末幀-only 63.8%；無效生成算錯，所以 full/last 分數差也受格式穩定性影響，不能全歸因於時間推理。店內 val 僅 8 筆、單一 seed，不足以證明泛化。

選模後才測固定 `0626x9.mp4`：**方向 0/16，全部回答 LATERAL**；action 全 CONTINUE，11/16 只等於多數類基準。0626 已多次看過，現在是固定回歸，不是新盲測。

本機證據：`results/temporal_vqa/grounded_vqa_v1/` 的 `comparison.json`、`dataset_audit.json`、`inspection_summary.json`、`masks_*/index.html`、`selected.store_test.html` 與 `selected.0626_vqa.mp4`。後者是離線重播，不是即時效能測試。

## 早期實驗保留的結論

JAAD-only 的 token accuracy 曾達 96.85%，但自由生成方向只有 42.9%，低於 val 多數類 53.8%；高 token accuracy 大部分來自固定句型。JAAD/store 70/30 domain replay 在 store val 得 2/8，但 0626 仍 0/16。僅增加相同弱標籤或延長訓練，未修復本場域效果。中間 sweep 啟動器已移除，這些判讀結論保留。

早期 bbox／ROS 基準也保留：YOLO＋ByteTrack＋規則在 CPU 每張影像 p50 約 28.31 ms，其中 ByteTrack 約 0.58 ms；無人工行為真值，並非 LSTM/VQA 準確率。接相機／Isaac 的測試使用影片發布 fixture，不等於已驗證真實相機或原生 Isaac 場景。

## 延遲的量測邊界

外接 LLaVA 的早期 VQA round trip 約 1.9–2.6 秒；embedded 的固定 0626 八張 clip 編碼＋生成 warm p50 約 302 ms、p95 約 327 ms。兩者設定與量測邊界不同，不算受控倍數加速。後者排除磁碟讀取／resize、第一次 warmup，也不是單張串流 end-to-end latency。

線上保持小模型架構，離線老師不加入 runtime。下一輪優先補本場域 paired masks、可靠的已觀察移動標註、轉向／起步／停止事件及新錄影盲測；訓練步驟見 [TRAINING](TRAINING.md)。

## 2026-10-22 人物／MeViS 聯合訓練對照

**結論：MeViS 本輪只改善部分指標，尚未整體更好，不替換原 baseline。**本輪確實下載資料、執行訓練及自由生成評估；不是只準備腳本。兩條模型架構和舊權重都保留。

共同資料：COCO人物 masks 2,504 train／263 val、JAAD人工活動1,270 train／243 val、54個店內SAM3時序mask弱標註train。MeViS另加入64個train影片／12個valid_u影片，共211／45個原始描述與mask樣本；train正mask112、空目標99，val正mask36、空目標9。加入後共4,039 train／551 val。MeViS描述沒有被偽造為活動／風險答案。

兩組同起點 `/workspace/checkpoints/person_state_teacher_v1/train_memory/best`、seed42、2 epochs × 1,200 replacement draws＝2,400更新、相同LR與模組設定。加入MeViS後佔20%抽樣，其他來源按比例減少；是固定更新次數的對照，並非固定FLOPs或相同舊任務曝光量。共同segmentation/state validation loss等權平均選best，without選epoch2、with選epoch1；不拿最後一輪或各指標最高輪拼成結果。

| 完整歷史驗證指標 | 不加MeViS | 加MeViS |
|---|---:|---:|
| COCO人物正例 mIoU（204張） | 32.8% | 48.0% |
| COCO空目標 any-pixel FP（59張，越低越好） | 11.9% | 81.4% |
| MeViS正例 mIoU（36筆／12來源影片子集） | 15.2% | 12.2% |
| MeViS空目標 any-pixel FP（9筆，越低越好） | 55.6% | 88.9% |
| JAAD活動 macro-F1 | 44.6% | 47.7% |
| JAAD時間變化 macro-F1 | 26.7% | 20.1% |
| State schema成功率（243筆） | 95.5% | 99.2% |

Mask threshold固定0.5、320×320；any-pixel FP是空真值上至少一個像素預測為人物，不等於已發出人物危險警報。COCO空目標平均預測面積比例為 0.7%／1.3%。不以正例mIoU的提高掩蓋FP增加。

JAAD活動val為227個WALKING、16個STANDING；時間變化為200個STEADY、36個STARTED_WALKING、7個STOPPED_WALKING。固定回答多數類的活動accuracy=93.4%、macro-F1=48.3%；時間變化accuracy=82.3%、macro-F1=30.1%。本輪狀態模型尚未穩定超過這個簡單基準。`motion=UNCERTAIN/action=OBSERVE`是state任務的固定契約，不把其高符合率稱為學會未來方向或安全判斷。

### 記憶對照

| 模型／MeViS mask mIoU | 完整8步 | 只看NOW | NOW重複8次 |
|---|---:|---:|---:|
| 不加MeViS | 15.2% | 14.7% | 15.3% |
| 加MeViS | 12.2% | 19.7% | 13.3% |

完整歷史沒有穩定勝過repeat-last。加MeViS組的state last-only schema成功率只有0.4%，所以不能把full／last的文字分數落差全歸因於時間理解；維持8步的repeat-last仍有活動macro-F1 48.7%、時間變化22.4%，都未低於完整歷史的47.7%／20.1%。記憶參數確實更新，不代表已學會有效動作判斷。

### 實際微調範圍

可訓練參數16,762,882／總參數599,781,426（約2.8%）；不是每個可訓練元素都產生數值變動。直接比對初始與最佳bundle：

| 模組 | 設定／實際變動 |
|---|---|
| MobileNetV3 encoder與BN | 凍結，308個state tensors均未變 |
| FPN | 可訓練；32個tensors中20個實際改變，未被loss使用的smooth分支未變 |
| ConvLSTM memory | 訓練，兩個gate tensors均改變 |
| Visual／memory projector、temporal-to-pixel | 訓練，均有實際權重變化 |
| Pixel decoder、mask query／bias、presence head | 訓練，均有實際權重變化 |
| Visual／memory position、seg embedding | 訓練，均有實際權重變化 |
| Qwen第28層（index27） | 允許微調，11個tensors中8個實際變動；部分norm未產生數值變化 |
| Qwen前27層、詞嵌入、其餘language tensors | 凍結，沒有變動 |
| SAM3、外部LLaVA | 不參與梯度更新；SAM3僅離線產生店內弱標註 |

稽核沒有發現意外變動的frozen tensors。更新範圍由 `light_foundation_vlm/model.py:apply_stage()` 控制，Qwen LR 2e-6、其他可訓練模組LR 2e-5。MeViS只有mask supervision，文字答案仍由JAAD state QA提供。

### 模型、結果與使用

- 不加MeViS：`/workspace/checkpoints/person_state_mevis_ablation_v1/without_mevis/best`
- 加MeViS：`/workspace/checkpoints/person_state_mevis_ablation_v1/with_mevis/best`
- 完整比較：`/workspace/results/person_state_mevis_ablation_v1/comparison.json`；彙整器要求完整相同驗證IDs、query protocol與匹配訓練參數。
- 各組內含 `masks/index.html`、`state.{full,last,repeat}.jsonl`、`mevis_{full,last,repeat}/index.html`。
- 實際權重稽核：`/workspace/results/person_state_mevis_ablation_v1/with_mevis/finetune_audit.json`。
- 真實checkpoint的Qt功能測試：同目錄 `ui_smoke.png`／`ui_smoke.json`；便利商店8步串流記憶＋80-token prefix＋合法VQA JSON。State QA與`the people`分割共用同次視覺記憶，不重複編碼影像。畫面仍可見漏分割／局部分割，raw demo沒有訓練用的綠框目標，不能當店內準確率證據。

資料與權重不加入Git。重現、擴大資料、CSV複核、訓練與UI指令見 [TRAINING](TRAINING.md) 第8–13節。原外接LLaVA與舊整合模型baseline仍保留。

本輪是單一seed、有限人物影片子集與固定draws；val用於模型開發及選模，不是新盲測。品質評估與其他GPU工作並行，記錄的latency不作受控速度比較，也不據此宣稱推論加速。

下一輪應先補人工複核的店內活動／paired target masks、改善短連續時序與長度混合訓練，並以生成式活動／時間變化和FP約束選模；不要只增加資料量或以token accuracy選模型。這些後續訓練尚未執行，不把本輪結果描述為已解決時間理解或實機決策。
