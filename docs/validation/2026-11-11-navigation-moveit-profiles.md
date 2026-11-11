# 導航／MoveIt 操作模式與單一 RViz 修正驗收

日期：2026-11-11（Asia/Taipei）。整合起始 HEAD `6175dd57485ae5354691cbda445c1d6f7076d2f0`，既有未提交內容保留，未提交／推送，未修改上游或 checkpoint。

## 原問題與查到的事實

1. `make nav-vqa` 正確啟動 3 個必要容器。手動手臂 CLI 拒絕是因為 CONTROL_MODE=mission，與手動 MoveIt 模式互斥；不是漏裝 ROS package。
2. 查到同一 sim 內有兩個 RViz：一個由 sim.launch 直接啟動，另一個由補開 helper 啟動。先前只對 helper 加鎖，未涵蓋 launch，確實存在競爭。
3. 原圖的白灰區域已是地圖，但 Orbit 視角、點雲、相機和 MoveIt 面板混合，導航操作不清楚。
4. 查證當時使用者目標狀態已是 HOLD，最後原因為 operator cancel。沒有完整歷史證據可斷言該次最初沒移動的根因；Accepted map goal 本來就僅代表任務接收。

## 實作

- sim 自動啟動與 `make rviz` 統一走 image 內 `/opt/itri/open_rviz.py`，共用檔案鎖，取得模型後才開 viewer。Dockerfile 和 .dockerignore 納入必要檔案，不依賴宿主臨時工具。
- 導航預設 `navigation.rviz`：TopDownOrtho、map、robot、scan、plan，無 MoveIt／貨架點雲／相機 dock。手動 MoveIt 模式保留 `retail.rviz`。
- 增加 `make nav-demo`（既有 CPU fixture 模式入口），與真相機／真模型結果分開；未覆寫真 VQA 決策。
- `demo_control status` 唯讀輸出任務、gate、VQA 年齡與 odom；`goto --wait` 監看最多 120 秒。這是 CLI 監看時間，不是運動許可時效；退出不自動取消任務。
- manual_control 在錯誤模式提供中文切換提示、exit 2；原假硬體限制、控制 lease、watchdog、場景 revision 和本機運動閘門規則保留。
- [Docker 教學第 10–11 節](../DOCKER_USAGE.md#10-nav2-與-moveit-到底先開哪一個) 補上導航／MoveIt 選擇、錯誤判讀、來源位置與 colcon 操作。

## 建置與實測

最終 sim image：`sha256:9d94693ddb3cf78a1c8ed5ad133873ce328d35269d2a30d3df36e08d54b69e32`。GPU 沿用 `sha256:617fda6af1090069cef9591fa5090d2deb31afb23763ef5901f08e6287cc13c3`。Docker 建置成功；本輪 CPU image 的 apt／pip 相依有重新安裝，並非只重用舊 image。沒有 apt snapshot，不宣稱跨日期重建位元一致。

本輪程式／建置設定的 [SHA256](2026-11-11-navigation-source-sha256.txt) 已保存；四個上游版本仍以 dependencies.repos 為準。

| 驗收 | 結果 |
|---|---|
| 容器內單元與導航生命週期 | 22 passed；拒絕衝突／錯 frame／重複目標，取消、HOLD、reset 不重播，fresh goal DONE；[log](2026-11-11-navigation-profile.log) |
| CPU 模式新 CLI | `goto --x 0.6 --y 0.2 --yaw 0 --wait`，WAIT_NAV_READY→NAVIGATE→DONE，最後 x=0.5429、y=0.1898 m，速度歸零；[log](2026-11-11-nav-cli-wait.log) |
| RViz 真實視窗 | 自動啟動後再次 `make rviz`，仍只有 1 個程序；已擷取並檢視俯視地圖，見 [畫面](2026-11-11-navigation-profile.png) |
| MoveIt 模式 | 合併樹、collision-free；右臂 joint4=0.3 rad，然後全零收納，兩次 fresh plan／execute 成功、實測停止；[log](2026-11-11-moveit-profile.log) |
| 導航模式拒絕手動接管 | exit 2，提供切換方式，未發手臂 goal；[log](2026-11-11-manual-mode-refusal.log) |
| 真相機／真模型模式 | RealSense→YOLO-assisted VQA UI 有效 CONTINUE，927.53 ms；source 非 synthetic、schema／prompt 保留；[log](2026-11-11-live-navigation-profile.log) |
| 真 VQA→Nav2 假底盤 | 明確 reset 已停妥的假底盤，給一個新目標；實測移動 **0.3463 m**，本次 action 均為 CONTINUE，結果 **DONE**，速度歸零、許可撤回。沒有強制 clear 或改模型輸出；同上 log |
| 最終桌面 | 真相機模式只有 1 個 navigation RViz 和 1 個 ROS VQA Monitor，3 個主要常駐容器 |

CPU 目標有既有 0.06 m 到點容差，所以回報 DONE 的實測位置不是指定座標的逐位相等。這是隔離假硬體測試，相機是真硬體；沒有啟動實體馬達或 Isaac。先前真 VQA 測試因 DATA_LOSS 停機的紀錄仍有效，本次一次成功不代表任何畫面都可持續通行或模型準確率已驗收。

## 重現

宿主機建置與 CPU 回歸：

```bash
make build
docker compose --project-directory . -f docker/compose.yaml run --rm --no-deps \
  sim bash /workspace/scripts/accept_navigation.bash
```

桌面操作（所有運動為假硬體）：

```bash
make stop
make nav-demo
make rviz       # 重複呼叫仍只有 1 個 RViz
make shell
# 容器內
ros2 run retail_core demo_control goto --x 0.6 --y 0.2 --yaw 0 --wait
ros2 run retail_core demo_control status
exit
# 宿主機；切換前取消未完成任務並確認停妥
make moveit
make shell
# 容器內
ros2 run retail_core manual_control move --group right_arm --joints 0 0 0 0.3 0 0 0
ros2 run retail_core manual_control move --group right_arm --joints 0 0 0 0 0 0 0
exit
# 宿主機切回真模型；另一個終端 make shell
make nav-vqa
```

真模型驗收在 `make nav-vqa` 完成啟動後，另於宿主機執行：

```bash
docker compose exec -T sim bash -ec \
  'source /opt/itri/setup.bash; python3 /workspace/scripts/check_vqa_ui.py --source external; python3 /workspace/scripts/check_live_nav.py; ros2 run retail_core demo_control status'
```

`check_live_nav.py` 只允許 fake_tidybot＋simulation runtime，會明確 reset 停妥的假底盤再送新 goal；真模型的畫面／時效不同會得到不同結果，不保證每次到點。

本輪未操作 RViz GUI 按鈕自動化；手臂執行鏈由相同 MoveGroup action 的 CLI 驗證，面板的群組／Joints／prepare 窗口步驟已記錄。底層 RViz segmentation fault 尚未在本輪重現定位；單一啟動修正解決的是競爭，不宣稱根治所有 renderer 問題。
