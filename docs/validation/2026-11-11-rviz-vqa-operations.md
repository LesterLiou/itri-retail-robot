# RViz 任務 UI、視角與 VQA 歷史顯示驗收

日期：2026-11-11，Asia/Taipei。延續既有未提交工作樹，未提交／推送，未更改 upstream repo、checkpoint、訓練相容 prompt 或模型 schema。

## 實作與範圍

- 新增第五個整合套件 `retail_rviz_plugins`。RViz 同時顯示 Nav2 目標工具、Motion Planning、Retail Task Controls。目標走 `/mission/goal_pose`；Reset／Cancel 用 typed `/mission/reset`、`/mission/cancel`，沒有 raw velocity publisher。
- UI 的 Arm window 重用已安裝 `retail_core.manual_control.main`，子程序直接擁有 lease；End 以 SIGTERM 進入該 owner 的停止／清理鏈。後端仍確認 GenericSystem、唯一 owner、場景 revision、本地閘門與實測停止。
- 一鍵 Navigation top／Arm 3D 只切換 RViz ViewController。手動 Views 可從 Panels 加入。按鈕改成緊湊格狀，避免所有面板同開時超出螢幕；導航地圖縮放調為 90 px/m。
- VQA HTTP future 完成以 guard condition 通知 ROS executor，不必等待下一個 100 ms timer；維持單 pending、原 capture stamp 和 2 秒 TTL。
- VQA UI 保存上一筆有效 action／source／capture time；當前失效時明示 LAST VALID (history only)、CURRENT DATA_LOSS、motion denied。原 RiskAdvisory.action／valid、來源重啟和新 STOP 均不被歷史顯示覆蓋。
- 本機 Compose 明確指定 Qt xcb，沿用宿主 X11 socket／私人 Xauthority。沒有虛擬桌面或網頁服務。
- [完整功能／架構操作](../OPERATIONS.md)、[視角與 MoveIt 欄位教學](../RVIZ_MOVEIT_GUIDE.md) 已連到主 README。

## 測試

| 項目 | 實際結果／證據 |
|---|---|
| ROS 單元 | **27 passed**；含原 gate／導航、歷史顯示不恢復許可、新 STOP 即時替換、Qt 卡片標記、無 timer 時 completion 喚醒與過期 stamp 不重蓋；[log](2026-11-11-rviz-controls-final.log) |
| 已安裝 RViz plugin＋Qt mock | Pluginlib 真實載入，按 Reset／Cancel 對 mock task services 各呼叫一次；HOLD／stale 回饋停用新目標／Reset，手動／導航模式隔離；同上 log |
| 真 Qt Arm／End → 假硬體後端 | 另開隔離 compose.moveit container，取得新場景與 lease、窗口 ready、按 End、收到 measured standstill／HOLD／disabled；[log](2026-11-11-rviz-manual-buttons.log) |
| 開發中發現並修正 | 第一版 QProcess 啟動 ros2-run 包裝層，terminate 沒直接進 owner cleanup，測試失敗；改成直接 Python entry point，後續通過。原失敗[log](2026-11-11-rviz-manual-buttons-first-attempt.log) 保留 |
| Nav2 回歸 | 任務目標、衝突、取消、HOLD、reset 不重播與新目標完成；[log](2026-11-11-rviz-controls-navigation.log)；該次 26 個單元是新增最後一個 Qt 卡片測試之前的數量 |
| 本機桌面 | 查到 1 個 RViz、`/retail_rviz_controls`、原生 ROS VQA Monitor；[RViz 畫面](2026-11-11-rviz-task-controls.png) 已檢視。觀看／重開檢查未發移動目標 |

Qt 的 offscreen 只用於一次性單元／整合測試，不建立虛擬桌面。實際使用 xcb 直接在本機顯示。運動後端全部是 GenericSystem／fake_tidybot，沒有測試實體馬達或 Isaac。

## 真相機 VQA 健康度：未宣稱消除 DATA_LOSS

使用 `measure_vqa_health.py --seconds 30` 均勻每 50 ms 採樣，只訂閱、不發命令：

| 指標 | 修改前 | 完成通知更新後 |
|---|---:|---:|
| 原始有效採樣比例（含 startup waiting） | 87.50% | 90.33% |
| image/result expired 採樣 | 48/600 | 37/600 |
| 相機接收 FPS | 14.63 | 14.47 |
| 完成的模型結果 | 31 | 31 |
| 推論延遲 median | 932.16 ms | 928.11 ms |
| 推論延遲 max | 1268.65 ms | 1314.69 ms |

原始量測：[before](2026-11-11-vqa-health-before.json)、[after](2026-11-11-vqa-health-after.json)。after 額外保存實際觀察到 RViz node 的欄位。兩段畫面／負載不完全相同，僅為小樣本觀察，不是受控效能比較。before 也觀察到原 schema 拒絕 LOW risk 與證據不足理由矛盾的回答。

推論 0.9–1.3 秒加上等待下一結果可能跨過擷取後 2 秒，因此仍可能失效。歷史顯示不代表健康／通行。未加長 TTL、未停 watchdog、未把舊 CONTINUE 改成 fresh，也未修改 schema 來通過不合規回答。

## 重現命令

```bash
make build
# 27 cases 與 mock Qt UI，只連一次性容器的 localhost ROS 圖
docker compose --project-directory . -f docker/compose.yaml run --rm --no-deps \
  -e QT_QPA_PLATFORM=offscreen sim bash -ec \
  'source /opt/itri/setup.bash; ros2 run retail_rviz_plugins controls_smoke; python3 -m pytest -p no:cacheprovider -q /workspace/ros2_ws/src/retail_core/test'

# 真實按鈕到 fake backend 的 lease／停止測試
docker compose --project-directory . -f docker/compose.yaml -f docker/compose.moveit.yaml \
  run --rm --no-deps sim bash /workspace/scripts/accept_rviz_manual.bash

# 本機使用；切換前取消任務並確認停妥
make nav-vqa
# 等上面完成啟動後，另一個宿主終端量測
docker compose exec -T sim bash -ec \
  'source /opt/itri/setup.bash; python3 /workspace/scripts/measure_vqa_health.py --seconds 30'
```

本輪最後另調整了純視覺縮放；控制邏輯與已通過按鈕測試相同。最終 image ID 與 source hash 見 [manifest](2026-11-11-rviz-vqa-manifest.json)。最終原生桌面唯讀 [log](2026-11-11-rviz-controls-desktop.log) 另存，沒有以這個檢查發送 goal。

## 外部影片與未執行項目

Instagram 提供連結、去除 query 的一般頁與 embed 均無法取得影片本體；搜尋 reel ID 亦無結果。沒有聲稱已看過、識別模型或驗證其 FPS／中斷處理。收到原影片後才能進一步比較實際更新行為。

MoveIt 教學參考 Humble 官方 quickstart 和同分支 plugin 原始碼，來源已放在教學段落旁。本輪未做完整 IK 拖曳驗收、warehouse／MongoDB、SensorManager looking、MASt3R 融合、真機安全或模型準確率測試。
