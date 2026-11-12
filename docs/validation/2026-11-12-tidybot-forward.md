# TidyBot 兩公尺與程式授權驗收

在 5070 的 ITRI 根目錄建置 Jazzy 控制映像。以下驗收使用隔離容器和假底盤，不映射硬體設備。

```bash
docker compose -f compose.robot-standby.yaml build tidybot-drive
docker run --rm --network none --ipc private \
  -e ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST \
  -e FASTDDS_BUILTIN_TRANSPORTS=LARGE_DATA \
  -v "$PWD/scripts/check_tidybot_forward.py:/test/check_tidybot_forward.py:ro" \
  itri-retail-robot:jazzy-tidybot-navigation python3 /test/check_tidybot_forward.py
```

程式測試確認先呼叫本機授權，再送 THOR 目標，收到對應任務的 DONE 與停妥回饋後撤權。模擬互鎖拒絕時，未送出目標。

跨機介面用 5070 本機的 Compose internal 網路驗收，任務域使用 105，假底盤使用 106。以下案例也可透過 scripts/accept_three_host_navigation.bash 重現。

| 測試 | 實際結果 |
|---|---|
| 兩公尺直行 | 1.970 m，里程計確認停妥 |
| VQA 速度 | CONTINUE 為 0.072 m/s，DANGER_WARNING 降為 0.018 m/s |
| STOP 與資料斷流 | 進入 HOLD、確認停妥，拒絕新任務直到本機復歸 |
| 取消與第二個控制發布者 | 撤回運動許可，量測停妥 |
| ROS launch 故障 | 隔離容器注入非零退出後保留 FAULT 狀態，手動停止可正常結束 |
| 手把停止與導引鍵 | 六項 Jazzy 回歸案例通過 |

現場最後一筆底盤錯誤記錄為 11.30 V，觸發原有 11.5 V 啟動保護。實體行駛與現場停止距離留待供電恢復及實體互鎖驗收後量測。
