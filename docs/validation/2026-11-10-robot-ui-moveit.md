# 合併模型、原版 ROS UI、MoveIt 與相機導航驗收

日期：2026-11-10，x86_64／ROS 2 Humble／Cyclone DDS。本輪延續既有整合工作區，未提交 Git、未修改上游或權重。整理期間目前整合 HEAD 為 `6175dd57485ae5354691cbda445c1d6f7076d2f0`；改動仍在工作樹。

## 已確認的實作

- 提供的 USD 現在放於 `external/tidybot_isaac_local_2`。只轉換底盤網格，排除 Kinova articulation；原始九個檔案 hash 與生成紀錄逐一核對。底盤 0.548×0.508×0.316 m，62144 個三角面。
- 單一 robot_description／robot_state_publisher 組合底盤與 OpenArm。保留底盤碰撞體；0.34 m 裝配偏移在 example 配置中明示未校正，沒有作為真機外參。
- 移除預設 RViz 3D 文字顯示，保留 ROS 診斷；加入 MoveIt 面板和合併模型。
- 原版 VLMotion Monitor 版面移植到 ROS UI。影像／結果經 ROS，推論沿用原 YOLO detector、temporal hints、Qwen、schema 和 constrained decoder。無 segmentation；訓練相容 prompt 固定。
- MoveIt 手動模式與任務／Nav2 模式分開。手動控制使用唯一 lease、獨立心跳 watchdog 和真實 fake-controller 關節回饋；正常完成撤回許可，取消／失聯維持 HOLD。
- RViz 2D Goal Pose 發往 `/mission/goal_pose`，由任務管理器持有 Nav2 goal。新目標需 map frame、有效平面姿態與新鮮 stamp，拒絕執行中衝突、重複與重啟前目標；取消／重設不重送舊 goal。

## 實際測試結果

| 測試 | 結果／證據 |
|---|---|
| 最終容器內單元測試 | 22 passed；含失效／過期、gate policy、UI 來源配對與 map goal 驗證；[log](2026-11-10-nav-goal.log) |
| Nav2 地圖目標 | 實測位移、拒絕衝突／重複／錯 frame、取消 HOLD、停止後不重播、reset 後只接受新 goal 並完成；[log](2026-11-10-nav-goal.log) |
| 合併模型與任務 | 單一樹、無 Kinova、目前姿態 collision-free；固定 Nav2＋MoveIt 完整假任務 DONE；任務模式拒絕手動控制；[log](2026-11-10-composed-full.log) |
| MoveIt 手動 | 連續兩次新規劃／執行，revision 更新、實測關節停止；[log](2026-11-10-manual-lease-normal.log) |
| MoveIt 取消／衝突 | 控制權衝突拒絕且不取消原 owner；Ctrl+C 後 HOLD／實測停止／不重播；[log](2026-11-10-manual-lease-cancel.log) |
| MoveIt 程序失聯 | SIGKILL owner，由獨立 watchdog 撤回許可，實測停止且 HOLD；[log](2026-11-10-manual-lease-loss.log) |
| RViz 執行窗口 | prepare 維持 15 秒後撤回許可，下一個全新 move 可完成；[log](2026-11-10-manual-prepare.log) |
| 原版 ROS UI＋影片 | YOLO-assisted、無 segmentation、真模型 CONTINUE、source epoch／capture stamp 配對；[log](2026-11-10-vqa-ui-video.log)、[畫面](2026-11-10-vqa-ros-ui.png) |
| 真 RealSense＋ROS UI | 有效非 synthetic 結果、YOLO metadata、原生 Qt 視窗；同次取得 OBSERVE；[log](2026-11-10-live-vqa-nav.log) |
| 真相機／真 VQA → Nav2 假底盤 | 明確 reset 已停妥的假底盤，給一個新目標；實測移動 **0.0884 m**，收到 CONTINUE／DANGER_WARNING／DATA_LOSS，最後因 interlock lost 取消導航、停妥並維持 HOLD。**此測試沒有到點成功**，沒有改模型結果或強制 clear；[log](2026-11-10-live-vqa-nav.log) |
| Compose／建置 | root、headless、VQA UI、MoveIt 組合解析通過；sim 10 套件建置成功，無馬達設備映射 |
| 最終映像本機桌面 | 重新啟動 RealSense／RViz／ROS UI，合併模型 collision-free，真相機有效 CONTINUE，推論 943.28 ms；唯讀檢查未發移動命令；[log](2026-11-10-final-desktop.log) |

原生 RViz 合併模型／MoveIt 面板見 [畫面](2026-11-10-robot-moveit.png)。影片 UI 圖片為影片來源，不冒充真相機結果。上述運動都是隔離 fake hardware；相機是唯一實際接入的硬體。

## 重現方式

所有命令從專案根目錄的宿主終端執行。隔離驗收容器使用 localhost DDS，不對外連接馬達。

```bash
# CPU 單元＋地圖導航生命週期
docker compose --project-directory . -f docker/compose.yaml run --rm --no-deps \
  sim bash /workspace/scripts/accept_navigation.bash

# 分別測正常、取消、失聯和 GUI 授權窗口
docker compose --project-directory . -f docker/compose.yaml -f docker/compose.moveit.yaml \
  run --rm --no-deps sim bash /workspace/scripts/accept_manual.bash normal
# 將最後的 normal 改為 cancel、loss、prepare 各跑一次

# 原版 UI 影片；原生桌面終端，另一個終端執行唯讀驗收
make vqa-ui-video
docker compose exec -T sim bash -ec \
  'source /opt/itri/setup.bash; python3 /workspace/scripts/check_vqa_ui.py --source video'

# 真相機 UI／Nav2；另一個終端驗收
make nav-vqa
docker compose exec -T sim bash -ec \
  'source /opt/itri/setup.bash; python3 /workspace/scripts/check_vqa_ui.py --source external'

# 此腳本會明確 reset 停妥的 FAKE 底盤並給一個新目標，模型輸出不變
docker compose exec -T sim bash -ec \
  'source /opt/itri/setup.bash; python3 /workspace/scripts/check_live_nav.py'
```

真相機測試取決於當時畫面與推論時效；可能因 OBSERVE／DATA_LOSS 全程拒絕運動，不能保證每次都有 CONTINUE 或到點。CLI 成功接受 reset 不代表重設程序已完成，需查看 `/mission/state` 和 gate 回饋。

## 版本與重建邊界

| 映像 | 本機 image ID |
|---|---|
| 最終 humble-sim | `sha256:8404d48455f95fe8b668b8dab84b255904d62ae55c9addc18c90387a4af76e58` |
| vqa-yolo | `sha256:617fda6af1090069cef9591fa5090d2deb31afb23763ef5901f08e6287cc13c3` |
| model-tools | `sha256:568da371d2af81542942ea37f9f1b1ea21bb0aaf5abf3898a38b165d5ac6d881` |

真相機／導航及手動功能測試主要使用本輪的 `4e76c00ee3e762fa1eb83f442fa2f8fe4a62546c57b1328590ba628d97cb3f9b` 或較早的同輪映像。最後重建只同步 USD 來源搬到 external 後的 source.json，控制程式不變；最終映像另重跑 22 測試和導航生命週期。映像為本機 ID，未發布 registry digest；apt 未使用 snapshot，不聲稱跨日期位元重建一致。

四個上游 exact SHA 在 [dependencies.repos](../../dependencies.repos)，目前 checkout 均乾淨；VQA 為 `889558ff0ff5f43c818ce82aa39b7b95734e7e6e`。模型 ID `30a2397b628c81721afde15c690122887b5a9709a9fa1bac0f7bb73f7403e619`，prompt hash `d02131eab27f7639a650c3bcca26baae02ad80fd3d0f3558f8ed14e2ce070f21`，沿用既有 checkpoint。YOLO 與 runtime 依賴鎖於 requirements；模型、影片與相機序號走忽略的本機掛載。

本輪整合程式／建置設定的檔案 hash 見 [source SHA256](2026-11-10-source-sha256.txt)；USD 來源檔 hash 另存 `retail_description/source.json`。

## 尚未驗收

Isaac 只完成 [接口契約](../ISAAC_INTERFACE.md) 和配置範本，沒有安裝／啟動 Isaac 或宣稱橋接可用。6×4 m 簡圖、scan 和底盤是 CPU 模擬，沒有將真相機環境重建為地圖。Thor／NUC 多機部署、真機馬達、裝配標定、MASt3R 尺度／融合、真人共域防護與模型準確率未在本輪驗收。
