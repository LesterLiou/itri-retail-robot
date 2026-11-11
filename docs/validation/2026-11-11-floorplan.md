# 2026-11-11 尺寸圖導航驗收

## 查證事實與範圍

- 整合 repo HEAD：`6175dd57485ae5354691cbda445c1d6f7076d2f0`，工作樹含先前與本輪未提交修改，沒有改上游／權重。
- Docker ROS 2 Humble／Cyclone DDS／x86_64，sim 映像：`sha256:e02a7fbba85cd18ac334c68bfbf1cdb1d416db7e67f797967a039a44dac262b0`。
- 按標註尺寸建圖，不按圖片像素縮放。幾何、occupancy 與假雷射共用來源；室內 6.6×4.4 m，中央障礙 x=[1.6,6.6]、y=[1.4,3.0]。這是未校正設計圖。
- 保留原 0.8×0.9 m footprint、速度與全部 watchdog／有效期。加入 DWB ObstacleFootprint critic，將全域 inflation 0.5→0.65 m，以涵蓋矩形約 0.602 m 外接圓半徑。局部 inflation 仍為 0.5 m；未放寬防護。
- 2D profile 不沿用舊貨架 3D fixture，不授權手臂執行。無新增馬達設備映射。

## 重現與實際結果

專案根目錄先 `make build`。生成地圖：`make floorplan-generate`，輸出見 [generation.log](2026-11-11-floorplan-generation.log)。

完全無外部網路、獨立 ROS domain 的一次性驗收：

```bash
docker compose --project-directory . \
  -f docker/compose.yaml -f docker/compose.floorplan.yaml \
  -f docker/compose.acceptance.yaml run --rm --no-deps sim \
  bash /workspace/scripts/accept_floorplan.bash

docker compose --project-directory . \
  -f docker/compose.yaml -f docker/compose.acceptance.yaml \
  run --rm --no-deps -e NAV_MAP_FILE= -e SIM_WORLD_FILE= sim \
  bash /workspace/scripts/accept_navigation.bash
```

兩次各 33 個測試通過。新圖單一任務目標由 (0.8,0.7) 到 (5.6,3.7)，確實經左通道；4408 個回饋樣本的完整旋轉矩形 footprint 未與幾何牆相交，最終 (5.552,3.669)，DONE、速度停妥、gate disabled。[完整輸出](2026-11-11-floorplan-navigation.log)。原地圖控制衝突、重複／錯誤 frame 目標拒絕、取消／HOLD、reset 不重送舊 goal、新目標抵達均通過：[回歸輸出](2026-11-11-floorplan-default-regression.log)。這是取樣的假運動驗證，不等於連續時間或實機安全認證。

初次測試捕捉到原 critic 設定下角落擦牆，加入完整 footprint critic 後曾安全中止；保留 [初次失敗](2026-11-11-floorplan-navigation-first-attempt.log) 與 [中止紀錄](2026-11-11-floorplan-navigation-safe-abort.log)，不把它們算成通過。

獨立無網路臨時容器疊加 local 顯示設定、RVIZ_ENABLED=1，確認原生 X11 RViz 顯示完整地圖、起點、雷射與 Nav2／MoveIt 面板：[截圖](2026-11-11-floorplan-rviz.png)。驗收後移除臨時容器，沒有切換原有使用中服務。`git diff --check` 通過。[地圖／規劃設定 SHA256](2026-11-11-floorplan-sha256.txt)。

## 尚未驗證

`make floorplan-vqa` 提供重用既有 RealSense／VQA 管線的 Compose 入口，本輪未另外驗收此組合。沒有啟動 LiDAR、SLAM Toolbox、AMCL、Isaac 或真馬達。實機需校正地圖、定位、odom／TF、即時障礙感知與完整載物外廓；目前 map→odom 是 fake 模擬對齊。
