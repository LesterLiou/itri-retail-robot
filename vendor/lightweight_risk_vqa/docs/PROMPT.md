# Prompt 操作與配對檢查

完整兩份 prompt 原文、訓練／推論差異、六欄輸出及實測結果，集中在 [整合報告第 5 節](MODEL_DATASET_REPORT.md#5-實際-prompt-與訓練差異)。這裡只保留操作與診斷入口。

| 選項 | 狀態 |
|---|---|
| `--robot-prompt compatible` | 預設；與 C 的 weak_robot 訓練 query 逐字一致 |
| `--robot-prompt compact` | 短 prompt 實驗；尚未證明更好，影片 smoke 曾全部無效 |
| `--query TEXT` | 自訂問題優先；會改變輸出契約與資料分布 |
| `--robot-decoding constrained` | 預設；限制 JSON／合法欄位組合，不能保證影像事實正確 |
| `--robot-decoding free` | 自由生成對照；仍須事後驗證 |

使用[套件 UI 指令](../README.md)，在結尾加入所需選項；變更程式後關閉舊 UI 再重開。這裡的 query 是視覺／記憶 prefix 後的一般文字，不是額外 chat system role，無需在 UI 再貼一份 system prompt。

## 無效回答如何處理

已知 placeholder、敘述中混入 JSON 欄位、重複標點迴圈，以及 LOW 的文字卻明說方向證據不足，都會被拒絕。UI 顯示 DATA_LOSS、保留完整 raw；不偷偷改為 WARNING／STOP／OBSERVE。這是有限文字啟發式檢查，仍可能誤判／漏判，不能驗證方向、人物數與碰撞事實。

## 重跑配對實驗

相同 clips 的歷史／NOW，交替執行 compatible＋原 hints 和 compact＋精簡 hints，兩者使用同一更新後 decoder。這是 prompt／hints 配對，不是 decoder 因果對照；沒有人工真值，通過率不是準確率。

```bash
cd /workspace
export PYTHONPATH=/workspace/ros2_ws/src/lightweight_risk_vqa:${PYTHONPATH:-}
python3 -m lightweight_risk_vqa.audit_robot_prompt \
  --checkpoint /workspace/checkpoints/robot_vqa_recovery_v1/c_policy/best \
  --manifest /workspace/datasets/robot_vqa_recovery_v1/retail/clips.jsonl \
  --output /workspace/results/robot_vqa_prompt_new/paired.json --samples 12
```

預設從程式內的 compatible 讀取比較起點；若需重現其他舊 prompt，可指定 `--previous-prompt FILE.json`，格式為 `{"query":"..."}`。輸出檔必須尚未存在；模型與影片資料需另備。

既有證據留在 `/workspace/results/robot_vqa_prompt_v2/`：`paired_final.json` 是原始生成，`validation_final.json` 是相同 raw 經最後檢查的結果；`paired.json` 保留未採用候選，`ui_{compatible,compact}/` 保留真實 UI 截圖與逐筆時間。輸出不進 Git。

程式閱讀：`robot_vqa.py` → `robot_decoding.py` → `light_foundation_live_ui.py`；比較工具為 `audit_robot_prompt.py`。這輪 prompt 修改沒有重新訓練權重。
