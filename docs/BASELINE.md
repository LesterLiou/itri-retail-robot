# 整合開發基線

## 2026-11-11 TidyBot／THOR 連線與單相機階段

已從 RTX 5070 主機透過 SSH alias `TidyBot` 與 `THOR` 做唯讀查證。Codex 遠端專案分別為 `ITRI_tidybot` 和 `ITRI_THOR`。整合 repo 與 THOR 的 `/home/hrc/itri-retail-robot` 均在乾淨的 `main`，HEAD 為 `7cc710dd3c3b24abb8ce1b084647c11e67d7ba49`（文件更新前）。

| 主機 | 本次實際結果 |
|---|---|
| TidyBot | `x86_64`。USB 枚舉到 RealSense D435（`8086:0b07`）。運行中的底盤容器映像為 `tidybot_ros2_control:jazzy-noble`。控制 checkout 為乾淨的 `main`，SHA `195a46e5770e56ac35fb7fa437bb7fbad7d434b1`。Git 的既有 worktree 路徑失效，以 checkout 的 `GIT_DIR` 與 `GIT_WORK_TREE` 在單次查詢中指定。 |
| THOR | `aarch64`，Jetson Linux R38.2.2，已有 `/opt/ros/jazzy`。目前 SSH 帳號查詢 Docker daemon 時回報權限不足，容器與 GPU worker 狀態待查。 |

程式接點以本次唯讀查閱為準。底盤的 `ros2_base_node.py` 使用 `Twist`、`Bool`、`String` 及 `Odometry`，`ros2_control_mux.py` 的速度輸入為 `/teleop/cmd_vel` 和 `/docking/cmd_vel`。實際 runtime topic、ROS domain、RMW、QoS、D435 影像串流、THOR 推論與停止回饋仍待驗證。階段範圍和驗收順序見 [AGENTS.md](../AGENTS.md)。

## 2026-11-11 VQA 資產與短片診斷更新

依授權把完整 checkpoint／tokenizer、YOLO、VQA 固定來源、原測試影片與 prompt 複製到本專案，逐檔 hash 核對，實際 Docker 資產掛載皆在 repo 內。VQA UI 固定四個模型選項，健康另列，不編機率或輪播。已分析原始影片與 45 秒 raw 回答：失效有欄位混入、語義矛盾和擷取過期，不是 UTF 編碼損壞。新受限文字 profile 與實驗 KV-cache 引擎保留原 schema／TTL；11 筆配對的決策欄位同、9 筆文字同，16 個 CPU 分塊案例通過。真相機 45 秒 median 424 ms、無過期，仍拒絕 1 筆語義矛盾。詳細限制與資產位置見 [VQA_DIAGNOSTICS](VQA_DIAGNOSTICS.md)。

## 2026-11-11 RViz 操作面板與 VQA 顯示更新

目前五個整合套件新增 `retail_rviz_plugins`，RViz 同時保留 Nav2 目標工具、MoveIt 與任務按鈕。面板重用 task reset/cancel、唯一手動 owner 與 watchdog，不發 raw velocity。VQA 完成時以 guard condition 立即通知 ROS，UI 保留 Last valid 年齡供歷史參考，capture stamp／2 秒 TTL／失效 HOLD 不變。操作與實際功能分工見 [OPERATIONS](OPERATIONS.md)，視角和 MoveIt 欄位見 [逐項教學](RVIZ_MOVEIT_GUIDE.md)。

## 2026-11-11 操作模式更新

修正自動 launch／手動補開 RViz 的競爭：兩者統一使用映像內 helper 與同一把鎖。導航使用獨立俯視地圖配置，MoveIt 模式才顯示 Motion Planning。新增唯讀 `demo_control status` 與 `goto --wait` 區分接收目標與實際結果；導航模式的 manual_control 提供模式切換提示。原始碼／colcon 工作流程及分模式操作見 [Docker 教學第 10–11 節](DOCKER_USAGE.md#10-nav2-與-moveit-到底先開哪一個)。

## 2026-11-10 合併模型、ROS UI 與導航更新

目前四個整合套件包含 `retail_description`。從提供的 USD 抽出 TidyBot 底盤，與固定 SHA 的 OpenArm 合併成單一 robot_description；0.34 m 裝配偏移是未校正的假硬體配置。原 VQA Monitor 已移植為 ROS UI，YOLO-assisted 使用原模型／prompt／decoder，支援影片與真實 D435。獨立 MoveIt 手動模式有控制 lease、心跳 watchdog、取消／失聯停止驗收。

RViz `/mission/goal_pose` 經任務管理器送至 Nav2。22 個單元測試、地圖目標／取消／重設／衝突／拒絕舊目標測試通過。真相機／真 VQA 曾允許虛擬底盤移動 0.0884 m，後因 DATA_LOSS 取消並維持 HOLD；不是到點成功。Isaac 僅有接口契約，runtime 暫緩。完整命令、映像及證據見 [本輪驗收](validation/2026-11-10-robot-ui-moveit.md)。以下為先前階段紀錄。

## 2026-11-10 本機顯示器更新

依目前需求已移除虛擬桌面、VNC、網頁服務與其 port，重建 dev／sim 映像。預設使用宿主 DISPLAY、私人 Xauthority 和唯讀 X11 socket，RViz 直接在 Ubuntu 桌面開視窗；純 CPU 模式不開任何顯示伺服器。各 Compose 用法見 README 第 7 節，本輪驗收見 [本機 RViz](validation/2026-11-10-native-display.md)。下面含網頁桌面的紀錄保留為歷史快照。

## 2026-11-10 相機與目錄更新

已透過 Docker daemon 實際找到 D435，啟動 RGB 640×480／15 FPS，並接上 VLMotion 的真實 checkpoint 與訓練相容 constrained VQA。代理環境的 /dev 與 sysfs 不能代表 Docker 主機的設備可見性，因此以 camera-probe／SDK 和實際 ROS 影像為準；序號存本機 config/local。Docker 定義移入 docker/，根 Compose 保留 include 入口。結果與版本見 [RealSense/VQA 驗收](validation/2026-11-10-realsense-vqa.md)。下面「未列出相機」保留為先前階段的觀察，不代表目前狀態。

## 2026-11-10 整合階段更新

Docker 使用／開發入口另完成 `DISPLAY` 修正、可寫 sim overlay 與 Makefile 操作。整合套件在同一 sim 映像內編譯及驗收；獨立 dev 工具鏈不自動載入 sim overlay。指令與本輪證據見 [Docker 工作流程驗收](validation/2026-11-10-docker-workflow.md)。

以下 P0 內容保留為歷史查證。本輪起始整合 Git 已存在，HEAD 為 `d01fae544ae0961335484d71a8d47d9f3b5a8a54`，origin 為 LesterLiou/itri-retail-robot；起始已有 README／BASELINE／DEPENDENCIES／P0 驗收紀錄修改及未追蹤輸出，均保留其內容。未 commit／push，未修改四個上游。

已依 `dependencies.repos` 取得四個固定 SHA；OpenArm sparse checkout，總來源約 196 MB。Docker 內已建置上游五個 OpenArm 套件、TidyBot 套件和三個整合套件。已完成真實 Nav2／MoveIt 的假硬體任務、取消與故障測試，及真實 VQA／MediaPipe 的影片 ROS 接口驗收。具體映像、指令與輸出見 [integration 驗收紀錄](validation/2026-11-10-integration.md)。

本機查到 RTX 5070（12 GB）、驅動 580.178.04。VQA 原始碼在 `/home/lester/VLMotion`，HEAD 與鎖定 SHA 相同且乾淨；checkpoint 為 `robot_vqa_recovery_v1/c_policy/best`，僅以本機 `.env` 唯讀掛載。這些路徑是本次測試資產位置，不是正式部署依賴。主機未列出 `/dev/video*`；沒有進行實體 RealSense 擷取。未找到 MASt3R 已安裝 runtime／checkpoint；未執行其自動下載／patch 腳本。

查證日期：2026-11-10（Asia/Taipei）。本次範圍是 **P0 CPU Docker 開發環境及來源接點查證**；下列「程式已確認」不等於硬體／模型 runtime 已驗收。原設計書是既有資料，不能以其快照取代本次查證。

## 已查證事實：本機

| 項目 | 實際結果 |
|---|---|
| 路徑 | `/home/lester/obstacle_avoid` |
| Git | `git status --short --branch` 與 `git rev-parse HEAD` 均 exit 128：not a git repository。沒有可記錄的整合 HEAD；未修改 `.git` |
| 原始檔案 | `AGENTS.md`、`CODEX_PROMPTS.md`、系統設計、Docker 驗收說明、VQA 候選 prompt、最小 Dockerfile／Compose／smoke；沒有 `BASELINE.md` 或 ROS 套件 |
| OS／架構 | Ubuntu 22.04.5 LTS，`x86_64`，kernel `6.8.0-138-generic` |
| ROS | 宿主機未找到 `ros2` 或 `/opt/ros`；本次採容器內 ROS，不安裝宿主 ROS |
| Docker | client／server Engine 29.1.3，API 1.52，linux/amd64；Compose v2.39.2 |
| 權限 | 沙箱直接存取 daemon 回報 permission denied；經工具執行權限核准後可用。未改 socket 權限／群組，未停用其他容器 |
| ROS 基底 | 官方 `ros:humble-ros-base-jammy` 已拉取並以 manifest digest `sha256:d9bcf2e6f72df127fdee07048c2e87b1b4b9a59a74af0592f4489fc2844aa855` 鎖在 Dockerfile |
| Python／RMW | 開發設計選 Humble 的系統 Python 3.10、`rmw_cyclonedds_cpp`；實際套件版本由容器驗收輸出記錄 |
| vcstool | apt 的 0.3.0 對完整 SHA 執行 `vcs validate` 觸發內部 `UnboundLocalError`；容器可連 GitHub，這不是網路問題。改用 YAML／SHA 格式檢查及取得後的 exact HEAD 對照，未修改工具上游 |
| 其他電腦 | 未連線 OpenArm NUC、TidyBot NUC 或 Thor；不能把本機規格視為三機規格 |

## 已查證事實：上游來源

透過 GitHub connector 讀取 branch API、recursive tree 及以下檔案，未修改上游。四個 SHA 已寫入 `dependencies.repos`。本次未 clone 四份來源進工作區、未下載權重、未執行上游硬體 launch。

| Repo／本次 SHA | 套件與入口 | 環境／需求與測試邊界 |
|---|---|---|
| [OpenArm](https://github.com/hrc-pme/openarm_dev/tree/3eaa3361d84866394ff5fa9865514aed80af4833) `3eaa3361d84866394ff5fa9865514aed80af4833` | `ros2_ws/src/openarm_ros2/openarm_bringup/launch/openarm.bimanual.launch.py`；雙臂 MoveIt 設定；`openarm_hardware` 依賴 `openarm_can` | README 指 Humble／RealSense 2.56.1／CUDA 12.8 PyTorch 開發映像；不表示這些 GPU 依賴是 fake hardware 必需。上游 `colcon_build.sh:9,15,18` 有固定路徑、清除產物、先建 CAN；本次不執行 |
| [避障](https://github.com/Laiting20021202/social-safety-decision/tree/783b5d986487cd66b3e6f333875c631bbe0f9b64) `783b5d986487cd66b3e6f333875c631bbe0f9b64` | `realtime_safety/pipeline/mast3r_slam_adapter.py`／worker；`openarm_isaac_sim_test/ros2_ws/src` 內 perception／safety／dynamic avoidance | README 明確區分 Gazebo 歷史驗證與待完成的 Isaac 手部閉環。本次僅讀碼；既有兩套 pytest 與 GPU 推論未執行 |
| [VQA LesterVQA](https://github.com/LesterLiou/VLMotion_VQA/tree/889558ff0ff5f43c818ce82aa39b7b95734e7e6e) `889558ff0ff5f43c818ce82aa39b7b95734e7e6e` | `ros2_ws/src/lightweight_risk_vqa` 的 `robot_vqa.py`、受限制解碼器、UI；`test/test_robot_vqa.py` | `docs/INTERFACES.md` 說明兩個新 robot 模式 `vqa-only`／`yolo-assisted` 和既有 detector ROS adapter 是不同路徑。需 checkpoint／Qwen bundle；三視角需隔離時間序列狀態。本文未認證模型準確率 |
| [TidyBot](https://github.com/Li-ChengWang/tidybot_ros2_control/tree/195a46e5770e56ac35fb7fa437bb7fbad7d434b1) `195a46e5770e56ac35fb7fa437bb7fbad7d434b1` | 根目錄 ROS 套件；`ros2_base_node.py`、`ros2_control_mux.py`、`launch/tidybot_docking.launch.py`；docking／battery tests | `ROS2_CONTROL.md` 提供 Humble／Python 3.10 路徑；Dockerfile 是 Jazzy／Noble。新 HEAD 相較既有設計書的 `207f07f…` 新增 battery monitoring。`requirements-ros2.txt` 鎖 numpy 1.26.4、threadpoolctl 3.5.0、ruckig 0.12.2、phoenix6 26.3.0。Humble 實際安裝仍待測 |

MASt3R wrapper 已在避障 repo 找到：`infer()` 將 RGB、frame index、source timestamp 送入子程序，再產生 `PointCloudFrame`（[adapter:150](https://github.com/Laiting20021202/social-safety-decision/blob/783b5d986487cd66b3e6f333875c631bbe0f9b64/realtime_safety/pipeline/mast3r_slam_adapter.py#L150)）。這不是已驗收的 ROS `PointCloud2` 契約。`scripts/setup_mast3r_slam.sh` clone 未鎖 SHA 的公開上游，建立 venv、下載 checkpoint 並套 PyTorch 相容 patch；本次不執行，後續需另鎖其來源與模型。

額外讀碼發現 `infer():164` 呼叫 `_receive(None)`，接收端只有 timeout 非空時才做 `select` 時限檢查；推論回覆未提供這個時限。後續整合提案是讓 ROS adapter 和健康監看維持獨立程序／有界佇列，外層以 deadline 拒絕過期結果、必要時終止 worker 並使場景失效；不能讓本機運動許可等待 GPU 返回。本次不修改上游實作，也不宣稱已驗證其卡住／重啟行為。

## 已查證事實：介面接點

下表是讀碼結果。QoS、逾時寫的是上游預設，**不是新的真機安全參數**。沒有 runtime 量測的停止時間全部待驗證。

| 介面／方向 | 完整型別、單位／frame／時間 | 程式來源與行為 |
|---|---|---|
| 仲裁→底盤 `/cmd_vel` | `geometry_msgs/msg/Twist`；linear m/s、angular rad/s；無 Header，frame 由獨立狀態指定 | [base:32–72](https://github.com/Li-ChengWang/tidybot_ros2_control/blob/195a46e5770e56ac35fb7fa437bb7fbad7d434b1/ros2_base_node.py#L32)：depth 10；預設 command timeout 0.30 s。callback 接收逾時用 monotonic time；不是擷取時間契約 |
| 仲裁→底盤 `/tidybot/enable` | `std_msgs/msg/Bool`；無 stamp | 同檔 61–66：RELIABLE、TRANSIENT_LOCAL、depth 1。上游已預設 require_enable=true；整合仍需本地租約／禁動邏輯，不能只信任殘留的 latched true |
| 仲裁→底盤 `/tidybot/control_frame` | `std_msgs/msg/String`；預設 `local`，無 stamp | 同檔 51、61–66；RELIABLE、TRANSIENT_LOCAL、depth 1。整合統一 local，保留 x/y/yaw 全向速度 |
| 底盤→系統 `/odom` | `nav_msgs/msg/Odometry`；預設 parent `odom`、child `base_link`，含 stamp；m／rad | 同檔 35–41、69–72、`_publish_odometry`；預設 30 Hz、depth 10。與 TF 的唯一發布者、共時偏移及可靠靜止判定需 runtime 核對 |
| 手把／停靠→現有 mux | `/teleop/cmd_vel`、`/docking/cmd_vel` 為 `Twist` depth 1；enable／active 為 `Bool` | [mux:20–65](https://github.com/Li-ChengWang/tidybot_ros2_control/blob/195a46e5770e56ac35fb7fa437bb7fbad7d434b1/ros2_control_mux.py#L20)：command timeout 0.25 s、joy timeout 0.30 s；預設 require_gamepad=true，無 Nav2 輸入 |
| 停靠↔mux `/docking/set_control` | `std_srvs/srv/SetBool`；grant topic `std_msgs/msg/Bool` | 同檔 55、64；控制權 lease 機制，不能視為已完成無人任務管理 |
| MoveIt→左右 trajectory controller | `/left_joint_trajectory_controller/follow_joint_trajectory`、右臂對稱；`control_msgs/action/FollowJointTrajectory`；rad 與 trajectory time | [moveit_controllers.yaml:24](https://github.com/hrc-pme/openarm_dev/blob/3eaa3361d84866394ff5fa9865514aed80af4833/ros2_ws/src/openarm_ros2/openarm_bimanual_moveit_config/config/moveit_controllers.yaml#L24)：配置推導的非 namespace 名稱；自訂 namespace 時需重查。QoS、cancel→停止回饋未測 |
| MoveIt→左右夾爪 | `/left_gripper_controller/gripper_cmd`、右臂對稱；`control_msgs/action/GripperCommand` | 同配置；名稱由 action_ns 推導。夾爪位置／力單位與硬體回饋需查 controller／URDF 並實測 |
| 新 VQA 推論→adapter | Python dict，`STOP/DANGER_WARNING/CONTINUE/OBSERVE`；健康層 `DATA_LOSS` | [robot_vqa.py:49,123](https://github.com/LesterLiou/VLMotion_VQA/blob/889558ff0ff5f43c818ce82aa39b7b95734e7e6e/ros2_ws/src/lightweight_risk_vqa/lightweight_risk_vqa/robot_vqa.py#L123)。新模型 schema 未列 `NO_DETECT`。函式預設 frame/result age 2/8 s 是 UI 健康門檻，不沿用為底盤運動允許門檻 |
| 既有 ROS VQA 路徑 | `sensor_msgs/msg/Image` 或 `CompressedImage` → `/future_risk/{decision,action}` `std_msgs/msg/String` | [INTERFACES.md](https://github.com/LesterLiou/VLMotion_VQA/blob/889558ff0ff5f43c818ce82aa39b7b95734e7e6e/ros2_ws/src/lightweight_risk_vqa/docs/INTERFACES.md)。需區分舊 temporal 與新 ConvLSTM robot 模型；新 adapter 未實作 |
| 避障→場景 | `moveit_msgs/msg/PlanningScene`，topic 名取 safety config；`/openarm/dynamic_avoidance/collision_object` `moveit_msgs/msg/CollisionObject` | [safety node:99–138](https://github.com/Laiting20021202/social-safety-decision/blob/783b5d986487cd66b3e6f333875c631bbe0f9b64/openarm_isaac_sim_test/ros2_ws/src/openarm_safety_bridge/openarm_safety_bridge/node.py#L99)：depth 10；planning frame 來自 config，來源／有效期及衝突處理須 adapter 驗證 |

OpenArm bringup 已確認 `use_fake_hardware` **預設 false**，controller 預設 `joint_trajectory_controller`（[launch](https://github.com/hrc-pme/openarm_dev/blob/3eaa3361d84866394ff5fa9865514aed80af4833/ros2_ws/src/openarm_ros2/openarm_bringup/launch/openarm.bimanual.launch.py)）。開發入口不能直接繼承真機預設。

## 設計決策／提案

- 這次開發容器選 Humble／Jammy、Python 3.10，和 OpenArm 既有路徑一致；Cyclone DDS 為本次開發選擇。三機統一發行版是後續驗收項目，尚未宣稱 TidyBot Humble 與 Thor GPU 全通。
- 先一個整合 repo，外部程式以 SHA 清單取得，模型核心保留原相依；整合只寫 ROS adapters、launch 和互鎖。
- OpenArm NUC 放任務管理與 MoveIt，TidyBot NUC 放導航／唯一速度出口，Thor 放推論與重建。GPU worker 與 ROS adapter 可分容器，以有界本機 IPC 連接。
- 本輪只提供 `ros-smoke`、`dev`、`dev-check`，不提供會被誤認成可部署的空殼 `sim`／`thor`／真機 service。下一輪 P1 才完成 fake hardware 功能閉環。

## 待驗證清單

1. 兩台 NUC 的 OS／架構／現有 checkout；Thor 的 JetPack、Jetson Linux、CUDA、Python、PyTorch、GPU runtime 與 extension 支援。依 [NVIDIA 官方容器說明](https://docs.nvidia.com/jetson/agx-thor-devkit/user-guide/latest/setup_cuda.html) 選匹配的映像，不能直接複用 x86 CPU 開發映像。
2. 四個相機型號、連接主機、影像 topic／QoS／頻率／擷取時間、內參、胸口／肩膀外參、左腕手眼標定。`pure_rgb` 保留；RGB-D 是獨立擴充。
3. OpenArm 五個套件的乾淨建置、fake hardware action 名稱、取消／停止／回饋、雙臂碰撞及相機裝配模型。
4. TidyBot Humble 的 Phoenix／ruckig 安裝、全向 Nav2 適配、導航／停靠／手把仲裁與本機許可；原 gamepad lease 不得當正式任務依賴。
5. MASt3R 尺度與相機座標轉換、追蹤品質、胸口／腕部配準、同步即時動態障礙的可信 3D。背景深度不得當手深度。
6. 模型／prompt／checkpoint hash、三視角狀態分離、錄包測資與離線準確率；新 `NO_DETECT` 契約需明確定義來源，不能強改 checkpoint schema。
7. 三機 DDS discovery、時鐘同步、TF 唯一來源、QoS、影像頻寬、壓力負載與逾時；本輪 localhost smoke 不涵蓋跨機。
8. 既有設計書中提及的計畫書頁碼、純 RGB 驗收條文及 2026-11-30 交期未附原始計畫書，本次沒有查證，不能作為已確認的需求證據。

## 實際驗收

2026-11-11 新增使用者尺寸圖 profile：室內 6.6×4.4 m、中央障礙 5×1.6 m，未現場校正。Docker 內 33 個單元測試、繞行閉環、原地圖取消／重啟回歸及原生 RViz 顯示通過；真機定位／LiDAR／AMCL 未測。見 [地圖驗收](validation/2026-11-11-floorplan.md)。

完整指令與輸出見 [開發環境驗收紀錄](validation/2026-11-10-dev.md)。未執行項目按上列清單保留，不以靜態讀碼替代功能測試。
