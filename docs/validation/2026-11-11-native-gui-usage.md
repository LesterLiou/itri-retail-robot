# Docker 操作手冊與視窗重開驗收

2026-11-11（Asia/Taipei）。保留既有未提交內容，整合 HEAD `6175dd57485ae5354691cbda445c1d6f7076d2f0`，未修改上游或權重。

## 查到的事實

- `make nav-vqa` 的前景程式是 VQA UI，等待視窗關閉才返回 shell，QStandardPaths／XDG_RUNTIME_DIR 訊息不是失敗證據。
- 起初 VQA UI 有原生 X11 視窗，真相機 valid CONTINUE，推論 877.84 ms。
- 原本同一 sim 的 RViz 已退出，日誌有 exit code -11；其他 ROS 程序仍運行。再次 up 不會重建未變更的容器，也不會重新啟動這個已退出的子程式。未在本輪定位底層 segmentation fault 的根因。
- 查證時運行 4 個容器：sim、cameras、vqa-worker 和額外保留的 dev；真相機導航的必要常駐服務為前 3 個。

## 改動與驗證

- 增加 [初學者操作手冊](../DOCKER_USAGE.md)，主 README 加上入口，說明宿主／容器、build、各模式、退出／停止和資料保留。
- 增加 `make rviz` 和唯讀取得目前 MoveIt model 參數的 `scripts/open_rviz.py`；只開 viewer，不重啟 ROS graph、不建立任務、不發運動命令。鎖和 process 檢查避免重複 RViz。
- `make nav-vqa` 加上這個重開入口及前景 UI 的操作提示。
- 以上使用現有 sim `sha256:8404d48455f95fe8b668b8dab84b255904d62ae55c9addc18c90387a4af76e58`、vqa-yolo `sha256:617fda6af1090069cef9591fa5090d2deb31afb23763ef5901f08e6287cc13c3`。新 launcher 從掛載的 /workspace 執行，本輪無需重建 image。
- 實際從桌面執行 `make rviz`，成功開啟 RViz；再執行 `make nav-vqa`，仍只有 1 個 RViz，兩個原生視窗同時存在。`check_vqa_ui.py --source external` 通過，真相機 valid CONTINUE，1021.1 ms。驗收[輸出](2026-11-11-native-gui-usage.log)。
- `/mission/state` 保留既有 HOLD、mission_id 為空，未自動 reset／發送移動目標。沒有進行本輪運動測試。
- README／手冊的本地檔案連結、Python 語法、Make dry run 與 `git diff --check` 通過。未新增運動控制或介面變更，因此未重跑先前的導航／手臂回歸。

視窗紀錄不代表底層 RViz 崩潰已根治；新增入口能在不重啟整套系統的情況下重開。若再次退出，查看容器 `/tmp/retail-rviz.log`。
