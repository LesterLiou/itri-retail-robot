# Changelog

## 0.3.0 — 2026-10-23（準備版本，尚未發布 tag）

### 功能

- 保留整合 ConvLSTM＋Qwen 與分割＋外接 LLaVA 兩條架構，以及 YOLO／bbox ROS baseline。
- 支援 region-free `vqa-only`／`yolo-assisted`；YOLO 只提供證據，沒有固定區域或框繪製。
- 新增 `--no-segmentation`，實際跳過分割解碼與專用 Qwen prepass，保留記憶與 VQA。
- UI 聚焦目前指令、人數來源、模型延遲與理由；底部完整 raw，技術資訊可展開。
- 區分 DATA_LOSS、OBSERVE 與人物危險；拒絕過期、context 不符、格式失效及已知文字矛盾。
- 保留 compatible 預設、compact 實驗選項及受限／自由解碼；不將縮短候選宣稱為品質改善。

### 訓練與證據

- Native attention LoRA、合併存檔、可選 event head；明確記錄解凍範圍與 tensor audit。
- COCO grounding、JAAD 狀態、MeViS masks、SAM3／YOLO／Qwen3-VL 離線教師與零售弱標註流程。
- A／control／B／balanced／C 訓練、資料複核匯入、續訓、影像與記憶消融、原生 VLM 比較及報告工具。
- 統一串流 NCHW 前處理，修正來源影片被 preview cap 放慢的問題；顯示 evidence age。
- 保留可重跑評估及失敗結論，權重／dataset／完整 results 不進 Git。

### 整理

- [整合報告](docs/MODEL_DATASET_REPORT.md)為模型、資料、結果、prompt 與輸出的主要入口，附 PNG／SVG 架構圖。
- Recovery 保留操作，Prompt 保留診斷，Results 保留歷史比較；移除重複實測表和逐次 UI 修改敘述。
- root／套件 README 更新入口與權重；Python、ament、source export 都攜帶文件圖片。
- 清除套件快取與舊 source archive；新增版號一致性及文件封裝檢查。

### 已知限制

COCO 人物分割與基本 VQA 改善，時間事件與方向／意圖／碰撞仍不可靠。Compact pilot 未改善延遲，fast head 不適合取代 VQA 決策；皆預設不啟用。原生相機／Isaac 的整合模型端到端連接尚未完成。詳見整合報告的任務別表格，不把 schema 通過當成準確率。

測試與版本控制狀態以本輪實際檢查及 `git status` 為準。這個版本號不代表已 commit、push 或建立 GitHub release。
