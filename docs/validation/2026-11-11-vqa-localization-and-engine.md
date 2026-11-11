# 專案內 VQA、固定決策與推論時效驗收

日期：2026-11-11（Asia/Taipei）。整合起始 HEAD `6175dd57485ae5354691cbda445c1d6f7076d2f0`；保留既有修改，未提交／推送，未修改來源 VLMotion、上游或原 checkpoint。

## 本輪完成

1. 讀取使用者原片（66.198 s、720×1280），抽查 2／12／25／40／58 秒；確認固定活動類別、分數與證據、REVIEWING FOOTAGE 與 4× 片段。沒有從影片推定後端模型、校準機率或端到端時效。
2. 完整 VQA bundle／tokenizer（約 1.2 GB）、YOLO、固定來源、原測試影片、prompt 複製到 repo 內。159 檔逐一核對 hash；VQA 四個實際資產掛載全在專案內。[檢查](2026-11-11-vqa-local-assets-check.json)、[實際 mount](2026-11-11-vqa-local-mounts.json)。原檔未更動，模型／資料被 Git 忽略。
3. UI 固定四選項，不做隨機輪播、不捏造百分比，資料健康／證據／歷史參考分開。新 STOP 立即顯示，歷史不恢復許可；27 個 ROS／UI 測試與 RViz mock controls 通過。[log](2026-11-11-vqa-local-ui-tests.log)
4. RViz 明示 mission 模式 MoveIt 只是 preview。當時執行失敗有明確證據：Cartesian path 完成 100%／59 points，但 CONTROL_MODE=mission、authorized=False，trajectory 入口拒絕；不是調高 attempts 或速度可解決。

## 診斷與比較

| 階段 | 實際結果 |
|---|---|
| 原受限制解碼，45 s | 50 筆、JSON error=0、non-ASCII=0；3 筆敘述混入偽 JSON、1 筆 LOW／證據不足矛盾；另有過期。[統計](2026-11-11-vqa-output-audit-before.json) |
| sentence_safe 與 baseline 離線配對 | 固定 6 張真相機＋5 張裁掉別人 UI 的短片場景，兩者 11/11 schema 合法、action 無差異。這不是有標註的正確率驗收。[摘要](2026-11-11-decoder-comparison-summary.json) |
| sentence_safe＋HF 45 s | 43 筆、0 schema error，但 186 次明示過期＋32 次到期邊界採樣；僅收緊文字沒有解決延遲。[統計](2026-11-11-vqa-output-audit-after.json) |
| grammar_cached 對 HF 配對 | 11/11 motion／intent／risk／action 相同、9/11 raw 逐字相同，2 筆證據措辭不同；不是完全等價。median 825.19→268.85 ms。[摘要](2026-11-11-generation-engine-comparison.json) |
| 快取邊界 CPU 測試 | 16 個固定／自由 token、分塊、截斷與四動作分支通過；不載模型、不連 ROS。[log](2026-11-11-grammar-cache-tests.log) |
| 專案內模型＋實驗 cached 真相機 45 s | 86 筆，median 424.38 ms、max 799.99 ms；0 capture expiry、1 筆 LOW／理由矛盾仍被拒絕。健康採樣 882 valid／14 schema invalid／4 waiting。[統計](2026-11-11-vqa-output-audit-fast.json) |
| 原生桌面 | 真實非 synthetic RiskAdvisory、YOLO-assisted、原生 Qt；[log](2026-11-11-vqa-local-desktop.log) |

各 live 窗口的場景／負載不同，不能當受控準確率比較。原始資料在忽略的 `data/audits/`；paired 比較只有 11 張，不能外推所有輸入。模糊／弱證據如 noisy、nozzle 也有出現：格式合法不等於模型可靠。

## 引擎與保護邊界

`sentence_safe` 僅縮小敘述 token 標點集合；checkpoint、prompt、JSON enum、risk/action 對應、原 schema 保留。`grammar_cached` 將 grammar 已唯一決定的 token 合併 KV-cache 計算，自由 token 仍由原模型 logits 選。BF16 批次計算使少量文字措辭不同，所以只標為本機假硬體實驗 profile；hf／baseline 可回退。

沒有縮短證據後假裝驗證通過，沒有把 invalid 改成 CONTINUE、加長 2 秒 TTL、覆蓋擷取時間、停 watchdog、放行無回饋運動或發真機命令。最後仍有語義矛盾被拒絕，這個行為保留。

初次 CPU 測試的參考輸入用整個 JSON tokenize，邊界不符合原 grammar 的分段編碼，造成 assert；校正測試輸入後 16 案例通過。Transformers 5 未指定的 generation 參數為 None，原型檢查已按其 greedy 預設值處理。這些問題是在離線測試發現，不是隱藏失敗後改為 clear。

## 版本與重現

- 模型 ID：`30a2397b628c81721afde15c690122887b5a9709a9fa1bac0f7bb73f7403e619`。
- Prompt ID：`d02131eab27f7639a650c3bcca26baae02ad80fd3d0f3558f8ed14e2ce070f21`。
- Vendor 來源 SHA：`889558ff0ff5f43c818ce82aa39b7b95734e7e6e`。
- [最終 image／runtime 原始碼 hash](2026-11-11-vqa-local-runtime-manifest.json)；GPU image 沿用原已驗收 vqa-yolo，worker 檔案與 config 以 repo 唯讀掛載。
- `config/vqa/assets.manifest.json` 是全部資產逐檔 hash，`python3 scripts/verify_vqa_assets.py` 可重驗。

用法、配對測試與資產位置見 [VQA_DIAGNOSTICS](../VQA_DIAGNOSTICS.md)。重新評估引擎可把該頁的 compare_vqa_decoders.py 替換成 compare_vqa_engines.py；固定 data/evaluation 為本機資料，換機需自行準備並再驗證。

```bash
docker compose --project-directory . -f docker/compose.yaml -f docker/compose.vqa.yaml \
  -f docker/compose.ui.yaml run --rm --no-deps --entrypoint python3 \
  -v "$PWD/scripts/test_grammar_cached.py:/app/test_grammar_cached.py:ro" \
  vqa-worker /app/test_grammar_cached.py
```

## 本輪未完成／未宣稱

沒有真機安全、模型四決策準確率、校準信心、跨 GPU 完全等價或硬即時驗收。80%／90% 人體近身 guard 與 metric 人體 costmap 尚未加入 runtime；其 [獨立設計](../PERSON_GUARD_NAVIGATION.md) 明確與已實作功能分開。現有 Nav2 BT 已每秒重規劃，但 global costmap 仍只含靜態層，真實 RGB 人體不會自動變成地圖障礙。
