# 三機導航隔離驗收｜2026-11-12

## 部署版本與分工

三台皆以候選 SHA `e699fc449d73db71029ee2afa4be5f915c4c31a0` 建置 Jazzy 容器。5070 使用 RViz 與英文操作視窗。THOR 使用 ARM64 Nav2 及任務節點。TidyBot 的 fake base、閘門和轉接服務使用 domain 86，與既有馬達控制 domain 0 分開。三機 DDS 使用 domain 85、UDPv4 與實驗室 Wi-Fi 上各台的靜態 peers。實際底盤服務另做唯讀 graph 檢查。

## 隔離閉環

從候選 checkout 執行 `scripts/accept_three_host_navigation.bash`。本機 Compose 使用隔離網路與 domains 105／106。地圖目標 `(0.8, 0.2)` 最終停在 `(0.741, 0.193)` m。直行位移 `1.971` m，兩項都由 odom 確認停妥。警告降速、STOP 鎖定、風險串流過期、取消、重複控制發布者、手把接管時的停妥交接、閘門重啟與 THOR 斷線均通過。

在三機部署的 5070 操作容器內，使用和 RViz 相同的 `/mission/goal_pose` 送地圖目標，THOR 規劃後由 TidyBot 假底盤停在 `(0.742, 0.195)` m。另送直行任務，最終位移 `1.971` m。合成 VQA 從 `CONTINUE` 切換到 `DANGER_WARNING` 時，閘門直行輸出由約 `0.18` 降到 `0.09` m/s。切換 `STOP` 後進入 HOLD，odom 確認停妥，後續 `CONTINUE` 仍拒絕未復歸的任務。

```bash
# 三機 fake profile 已啟動時，在 5070 執行
docker exec itri-navigation-fleet-panel-5070-1 /opt/itri/navigation/entrypoint \
  python3 /opt/itri/navigation/accept.py map
# 等面板回報 DONE 再執行下一項
docker exec itri-navigation-fleet-panel-5070-1 /opt/itri/navigation/entrypoint \
  python3 /opt/itri/navigation/accept.py straight
docker exec itri-navigation-fleet-panel-5070-1 /opt/itri/navigation/entrypoint \
  python3 /opt/itri/navigation/accept.py risk
```

Wi-Fi 測試曾出現 stamp 偏差與速度傳送間隔過長，當次任務撤回許可並停妥。後續候選版以 25 ms 的保守時鐘誤差預算檢查跨機 stamp，並分開可靠的任務事件和 best-effort 速度串流，控制器採 20 Hz。TidyBot 的速度與任務 watchdog 仍為 0.25 秒。

## 真實 VQA

THOR 原 Qwen 容器重啟時缺少 `/models/yolov8n.pt`。使用現有 Qwen checkpoint 和 THOR 上已核對的 `models/yolo/yolov8n.pt` 以唯讀方式掛載後，worker 的 `/health` 回報 `ready: true`。重啟 adapter 後，THOR 收到 D435 影像並連續處理 `/infer`。這次取樣的 Qwen 結果出現 motion schema 驗證失敗與擷取後超過 2 秒，因此 5070 收到 `DATA_LOSS`、`valid: false`、`synthetic: false`。同一段 5070 取樣未收到 JPEG；影像到 THOR 的路徑與 5070 Monitor 的 JPEG 顯示各自驗收。

短暫停止合成來源，在 5070 使用 relay 將 domain 84 的真實 VQA 回報送入隔離 domain 85。提交假底盤直行後，TidyBot 回報 `REJECTED`、`tidybot: invalid source`、`standstill: true`，速度輸出保持禁用。驗證後恢復隔離合成模式。THOR 的真實推論和 5070 觀測窗另行保留供檢查。

## 真機 graph

TidyBot domain 0 查到 `/cmd_vel` 與 `/odom`，本輪查無 `/scan`、`/tidybot/auto_interlock` 及底盤 TF。唯讀執行 `check_hardware_graph.py` 時，現場手把仍直接寫入 `/cmd_vel`，因此預檢正確拒絕接管。實體底盤切換、獨立互鎖、場地定位與實機停止距離仍待現場驗收。操作及回復方式見 [三機導航部署手冊](../THREE_HOST_NAVIGATION.md)。
