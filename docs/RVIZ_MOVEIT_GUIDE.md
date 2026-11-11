# RViz 視角與 MoveIt 面板逐項操作

完整架構與導航／手臂按鈕流程見 [系統操作指南](OPERATIONS.md)。本篇對照你截圖中的 MoveIt Planning 面板，說明「欄位是什麼」與「本專案先選什麼」。

## 1. 不要一直俯視：切換到看手臂的 3D 視角

在 **Retail Task Controls**：

- **View: Navigation (top)**：地圖俯視，適合點 Nav2 目標。
- **View: Arm (3D)**：Orbit 3D，跟隨 base_link、聚焦手臂附近，適合看關節運動。

這兩個按鈕只改相機視角，不切換控制模式、不建立運動 goal。

手動調整也可以：

1. 找到 **Views** 面板；若關閉，Panels → Add New Panel → Views。
2. Type 選 **Orbit** 看 3D；選 **TopDownOrtho** 看地圖。
3. 上方選 **Move Camera**，再在 3D 區域操作：左鍵拖曳旋轉、中鍵拖曳平移、滾輪縮放。RViz 下方狀態列會顯示目前工具的滑鼠操作。
4. Orbit 的 Distance 是觀看距離；Focal Point 是注視中心。看手臂可用 Target Frame=`base_link`，Distance 約 3 m，Focal Point 約 `[0,0,0.9]`。
5. TopDownOrtho 的 Scale 是像素／公尺：數值越大，地圖看起來越大。導航按鈕會重設為 map、正上方與預設中心。

切到 3D 後可以用 Interact 操作模型／標記；Move Camera 操作的是觀看位置。改視角不會動機器人。每次重開 viewer 載入預設配置；固定修改要改 `retail_bringup/rviz/*.rviz` 再重新 build。

## 2. 第一次操作先用這組設定

宿主機啟動：

```bash
make stop
make moveit
```

先取消舊任務並確認停妥，再切模式。RViz 顯示 MODE: MoveIt manual 後：

| 欄位 | 第一次先設 |
|---|---|
| Planning Group | `right_arm` |
| Start State | `<current>` |
| Goal | 在 Joints 設 `openarm_right_joint4=0.3 rad`，其餘 0 |
| Planning Time | `5.00 s` |
| Planning Attempts | `10`，先保持原值 |
| Velocity Scaling | `0.10` |
| Accel. Scaling | `0.10` |
| Use Cartesian Path | 不勾 |
| Collision-aware IK | 使用末端 IK 時勾選；直接設定 Joints 不依賴此功能 |
| Approx IK Solutions | 不勾 |
| External Comm. | 不勾 |
| Replanning | 不勾 |
| Sensor Positioning | 不勾 |
| Path Constraints | `None` |

**Plan 預覽 → Task Controls 的 Arm execution window → 等待 ready 訊息 → 確認目標關節仍正確 → Plan & Execute。** 窗口更新場景可能刷新 Query，若目標回到當前值，在窗口內重新設定目標。只有 15 秒執行窗口，逾時就重新確認條件再開新窗口，不反覆 Execute 舊路徑。

回收納姿態，把七軸設 0，重新完成同樣流程。Plan 的影子動畫只是預覽，真正執行要看當前 robot／joint_states 改變。

## 3. Commands：四個主要按鈕

| 按鈕 | 意義 | 目前怎麼用 |
|---|---|---|
| Plan | 計算路徑並顯示預覽，不送控制器執行 | 可以先按；仍可能因碰撞／關節限制而失敗 |
| Execute | 執行上一筆已規劃結果 | 本整合流程避免重用快取；改用新窗口內 Plan & Execute |
| Plan & Execute | 用當前狀態重新規劃並嘗試執行 | 先取得手動執行窗口，且必須是 MoveIt manual 模式 |
| Stop | 向 MoveGroup 請求停止 | 要停止面板擁有的整個窗口，另按 Task Controls 的 End window，並確認停止回饋 |
| Clear octomap | 清除 MoveIt 的 OctoMap 佔據資料 | 現在沒有正式接入 OctoMap，所以可能灰色；不是用來忽略貨架或消除未知障礙 |

這些一般按鈕行為依 [MoveIt Humble RViz 官方教學](https://moveit.picknik.ai/humble/doc/tutorials/quickstart_in_rviz/quickstart_in_rviz_tutorial.html)；本專案另外有 lease／場景／本地閘門，Plan 成功不代表取得執行權。

## 4. Query：要規劃哪一組、從哪裡到哪裡

| 欄位 | 意義／設定 |
|---|---|
| Planning Group | `right_arm` 右臂七軸、`left_arm` 左臂七軸。上游另有左右 gripper 群組；初學先練手臂，本輪主要驗證也是手臂關節目標 |
| Start State `<current>` | 用目前關節回饋作為起點。執行時保持此選項，不任意假設機器人已在另一個起點 |
| Goal State `<current>` | 目標等於當前姿態。若未改目標，執行後可能沒有可見移動 |
| Goal State `<previous>`／named state | 先前或 SRDF 中的命名姿態；可選項依上游配置。名稱不代表當前裝配／場景一定可達或無碰撞 |
| Random／Random valid（若有） | 測試用隨機姿態；valid 也只是相應場景中的檢查。第一次不要用它找操作範例 |
| Joints 中的目標 | 指定每軸角度，比拖末端更容易驗證；設定角度只改 Query，需 Plan／Execute 才會運動 |

IK 是「由末端位置姿態求關節角」；FK 是「由關節角算末端姿態」。直接設定 Joints 的小角度範例不需要先讓 IK 拖曳可用。本專案未在本輪完整驗證末端拖曳；沒有互動標記時先檢查 solver／group，而不是取消碰撞檢查。

## 5. Options：時間、次數、速度

| 欄位 | 意義 | 目前建議 |
|---|---|---|
| Planning Time (s) | 給規劃請求的時間預算；不是路徑執行時間，也不是資料有效期 | 先 5 秒；不是規劃失敗就一直加長 |
| Planning Attempts | 請求規劃器嘗試多次；實際受 planner 與時間預算限制 | 保持 10 可先練簡單目標；不是保證取得 10 條路徑 |
| Velocity Scaling | 相對於配置關節速度上限的比例 | 0.10 表示 10%，不是 0.1 m/s；不用設 0 來代替停止 |
| Accel. Scaling | 相對於配置關節加速度上限的比例 | 先 0.10 |

官方範例同樣以 0.1 作為初始縮放，見 [Humble 的執行與速度說明](https://moveit.picknik.ai/humble/doc/tutorials/quickstart_in_rviz/quickstart_in_rviz_tutorial.html#executing-trajectories-adjusting-speed)。這些數字不取代本地閘門、場景新鮮度或真機限值驗證。

## 6. 每一個勾選項

| 選項 | 做什麼 | 本系統先怎麼設 |
|---|---|---|
| Use Cartesian Path | 嘗試讓末端在笛卡兒空間直線移動；可能無 IK 解、到不了完整路段 | 不勾；先用一般關節空間規劃 |
| Collision-aware IK | 在互動式 IK 找目標關節解時要求避開碰撞 | 有 IK 操作時勾；它不是整個 MoveIt 碰撞檢查的總開關 |
| Approx IK Solutions | 允許 solver 回傳近似的末端目標解 | 不勾，避免把近似目標誤當精確到點 |
| External Comm. | 允許外部 ROS 通訊觸發 RViz 規劃相關操作 | 不勾；本專案已有 Task Controls 與唯一 owner，不再增加另一個外部觸發入口 |
| Replanning | 允許 MoveGroup 的重新規劃能力 | 不勾；現階段取消／場景更新後明確送新 goal，不依這個勾選取代場景 revision 管理 |
| Sensor Positioning | 對應 MoveGroup 的 looking／感測定位能力，需要相關後端 | 不勾；不是啟動 RealSense，也不是目前固定候選腕部補拍的開關 |

Cartesian／IK 的一般機制見 [Humble RViz 教學](https://moveit.picknik.ai/humble/doc/tutorials/quickstart_in_rviz/quickstart_in_rviz_tutorial.html)；replanning／looking 與按鈕如何接到 MoveGroup 可對照 [Humble plugin 原始碼](https://raw.githubusercontent.com/moveit/moveit2/humble/moveit_ros/visualization/motion_planning_rviz_plugin/src/motion_planning_frame_planning.cpp)。本表的「不勾」是目前整合配置，不代表這些能力永遠不可使用。

## 7. Path Constraints

`None` 表示沒有額外整段路徑約束，仍有關節限制、碰撞檢查與控制閘門。其他約束可要求全程保持指定姿態等，但必須先配置可用的 constraint。第一次保持 None；不要把保持末端方向與只設定終點方向混為一談。

## 8. 其他分頁

| 分頁 | 用途 | 目前怎麼用 |
|---|---|---|
| Context | MoveGroup namespace、規劃環境／planner 等上下文 | 保持目前配置，不改 robot_description、服務 namespace 或 planning frame 來排除權限問題 |
| Planning | 本篇說明的規劃／執行欄位 | 主要操作頁 |
| Joints | 逐軸調整目標關節 | 初學主入口；右臂 joint4=0.3 rad 範例 |
| Scene Objects | 檢視或編輯 PlanningScene 物件 | 目前先檢視。場景變更統一交 scene_manager 更新 revision，不用 GUI 任意清掉貨架來讓路徑通過 |
| Stored Scenes | 儲存／載入場景，通常需 warehouse 後端 | 目前沒有配置資料庫服務，不能假定已可儲存 |
| Stored States | 儲存／載入機器人姿態，通常需 warehouse | 目前沒有配置；named SRDF state 與資料庫儲存不是同一件事 |
| Status | 觀察碰撞、限制、狀態和錯誤資訊 | 規劃／執行失敗時查看，搭配 Task Controls 的原因 |
| Manipulation（若顯示） | 通用物件 manipulation／抓放介面 | 尚未接成這個 GUI 的抓放流程；目前已知商品假任務由 mission_manager 編排 |

## 9. VQA 短片與本系統的連續顯示

先前 [Instagram 連結](https://www.instagram.com/reel/DfWJElGNZDz/) 無法取得本體，之後已收到你提供的 66 秒原片，抽查多個時間點。固定類別／分數／證據、REVIEWING FOOTAGE 與 4× 片段的實際觀察見 [短片與模型診斷](VQA_DIAGNOSTICS.md)。沒有據此宣稱知道其模型、機率校準或真正端到端 FPS。

依你要的連續觀察效果，本系統現在將即時影像、較慢的 VQA 回答與歷史決策分開顯示：影像持續更新，Last valid 不因等待下一筆就消失；當前健康／決策年齡仍獨立顯示。這項 UI 延續沒有重蓋擷取時間，過期／schema 無效也不冒充新 CONTINUE。

若要進一步提高**新判斷**更新率，下一步需量測相機→推論→ROS 的整段延遲、在相同資料上驗證更快的推論方案。這是待比較的改進方向，不是已從短片查到的實作。不能只靠延長上一筆移動許可宣稱達成。
