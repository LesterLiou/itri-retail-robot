# TidyBot × OpenArm ROS 2 整合系統

**想知道畫面要按哪裡：先看 [RViz 操作＋每個程式功能＋完整整合架構](docs/OPERATIONS.md)。** 已加入 Retail Task Controls；導航工具與 MoveIt 面板都保留可見。

**想看手臂 3D、或不懂 MoveIt 勾選項：看 [視角切換與 MoveIt 面板逐項教學](docs/RVIZ_MOVEIT_GUIDE.md)。**

**模型已複製進本專案：看 [短片對照、DATA_LOSS 診斷與模型位置](docs/VQA_DIAGNOSTICS.md)。** VQA 顯示固定四選項與證據，資料健康另列，不會隨機輪播或捏造百分比。

人體佔滿畫面的 STOP 與 Nav2 繞路，見 [近身 guard／costmap 接法](docs/PERSON_GUARD_NAVIGATION.md)：這是下一階段設計，80%／90% 閘門與即時人體深度尚未接入。

**你的 6.60 × 4.40 m 場地設計圖已轉成 Nav2 地圖：看 [地圖檔案、LiDAR／定位需求與操作](docs/NAVIGATION_MAPS.md)。** [下載影像＋YAML 整包](exports/store_corridor_nav2_map.zip)。這是未實測校正的設計圖版本。

目前可用：合併的 TidyBot／OpenArm 模型、Nav2 假底盤、MoveIt 雙臂假硬體、RealSense RGB、VQA／YOLO-assisted 推論、原版監看畫面的 ROS 2 移植，以及任務／本地運動閘門。RViz 與 VQA UI 直接開在本機桌面；開發、編譯、推論和控制都在 Docker。

目前控制使用 **fake hardware**；裝配外參為示範配置。宿主需 Docker Compose、桌面 DISPLAY／xauth，GPU 推論需 NVIDIA Container Toolkit。

第一次使用 Docker，先看 **[完整操作教學：在哪裡輸入、怎麼啟動、Ctrl+C／Ctrl+D、怎麼停止、何時 build](docs/DOCKER_USAGE.md)**。真相機導航需要 3 個主要容器：sim、cameras、vqa-worker；RViz 與 VQA UI 都在 sim 裡。

準備提交程式或換電腦：看 [Git 提交範圍、push 指令與資產還原](docs/GIT_WORKFLOW.md)。模型、影片、本機相機設定與 `external/` 不隨 Git 上傳。

## 常用入口

在本機桌面的終端機執行：

```bash
cd /home/lester/obstacle_avoid
make help
```

| 要做什麼 | 宿主機指令 |
|---|---|
| 看合併機器人與 CPU 模擬 | `make up` |
| 進入目前容器 | `make shell` 或 `docker compose exec sim bash` |
| 關閉／崩潰後重開 RViz | `make rviz`，保留現有 ROS 系統 |
| 使用 MoveIt 控制假手臂 | `make moveit`，再 `make shell` |
| 原版 VQA UI＋影片 | `make vqa-ui-video` |
| 原版 VQA UI＋RealSense | `make vqa-ui` |
| 真相機／VQA＋Nav2 簡單地圖測試 | `make nav-vqa`，另一個終端 `make shell` |
| 先練習 Nav2，不受真模型回答影響 | `make nav-demo`（合成感知／假硬體） |
| 使用你提供的尺寸圖練導航 | `make floorplan`（合成感知／假硬體） |
| 尺寸圖＋真相機 VQA | `make floorplan-vqa`（仍是假底盤） |
| 相機／VQA 後台，不另開監看 UI | `make vqa` |
| 可寫、可編譯的整合環境 | `make dev` |
| 相機／VQA 可寫開發環境 | `make vqa-dev` |
| 無視窗 CPU 模擬 | `make up GUI=0` |
| 停止本專案服務 | `make stop` |

切換模式前先完成／取消當前任務。Makefile 會處理相機與 sim 網路 namespace 的啟動順序。`make shell` 只進入目前容器，不切換模式。關閉 VQA 監看視窗只關閉 UI，ROS 感知會繼續運作。

## 合併 TidyBot 與 OpenArm

提供的模型目前位於 `external/tidybot_isaac_local_2/`，是 **USD**，沒有 URDF，且含 Kinova 手臂。轉換工具只抽取實際底盤幾何，排除 Kinova，產生：

- `ros2_ws/src/retail_description/urdf/tidybot.urdf`
- `ros2_ws/src/retail_description/meshes/tidybot_chassis.stl`
- `ros2_ws/src/retail_description/source.json`：來源 SHA、尺寸與座標轉換紀錄。

底盤尺寸約 0.548 × 0.508 × 0.316 m。`retail_bringup/launch/sim.launch.py` 組裝這份底盤 URDF 與上游 OpenArm xacro，產生**同一份** `/robot_description`，由同一個 robot_state_publisher 發布 TF：

```text
map → odom → base_link
               ├─ tidybot_chassis
               └─ openarm_body_link0
                    ├─ OpenArm 左臂／夾爪
                    └─ OpenArm 右臂／夾爪
```

OpenArm 固定裝在底盤上方。假裝配的 0.34 m 根座偏移在 `retail_description/config/assembly.example.yaml`，由底盤高度＋轉接間隙組成，`calibrated: false`；不是實測安裝外參。底盤保留 collision，沒有以關閉碰撞來避開模型重疊。

重新轉換及建置：

```bash
make model-convert
make build
make up
```

既有生成的 URDF／STL 可直接建置；只有重新轉換時才需提供這份本機 USD 來源，它不在 Git 上游清單中。來源目錄不同時，在 model-tools 容器執行 `python3 /workspace/scripts/convert_tidybot.py --source /workspace/your_usd_folder`。

容器內可唯讀檢查合併樹與目前碰撞狀態：

```bash
python3 /workspace/scripts/check_robot_model.py
```

## MoveIt 控制

導航模式與手動手臂執行分開，兩種畫面都保留導航工具、Motion Planning 和 Retail Task Controls。`make nav-vqa`／`make nav-demo` 預設俯視；`make moveit` 預設 3D。導航模式可做 MoveIt 預覽，直接 Execute 仍被拒絕。切換模式後必須退出舊 shell，再重新 `make shell`。

### ROS 2 指令控制

```bash
# 宿主機：獨立手動模式，不啟動 Nav2 或任務管理器
make moveit
make shell

# 以下在容器內：右臂第 4 軸移到 0.3 rad，其餘為 0
ros2 run retail_core manual_control move \
  --group right_arm --joints 0 0 0 0.3 0 0 0

# 從目前回饋重新規劃回到收納姿勢
ros2 run retail_core manual_control move \
  --group right_arm --joints 0 0 0 0 0 0 0

# 左臂使用 --group left_arm
# 取消／停止，等待關節回饋確認停妥；HOLD 保持
ros2 run retail_core manual_control stop
```

每次 move 都會取得唯一手動控制 lease、禁動底盤、確認靜止、更新場景並綁定 revision，再送出新的 MoveGroup goal。路徑經 MoveIt → arm gate → ros2_control，完成後撤回手臂許可，底盤仍禁動。獨立 watchdog 監看控制程序心跳；失聯、取消、過期或場景改變會停止，舊 goal 不會自動重送。

`make moveit` 重新建立一個收納／停妥的假硬體工作階段；任務模式中的 manual_control 會拒絕操作。

### RViz MoveIt 面板

RViz 已載入 **MoveIt Motion Planning**，預設群組 `right_arm`。原本 MISSION／SCENE／HAND／VQA 的三維文字疊圖已從預設畫面移除，診斷資料仍保留於 ROS topics。

Planning Group 選 `right_arm`（右臂）或 `left_arm`（左臂），Start State 選 `<current>`。Goal State 的 `<current>` 代表目前姿態，本身不會移動；到 **Joints** 分頁設定該群組目標，例如右臂 joint4=0.3 rad、其餘維持 0，再依下列窗口步驟操作。不熟悉面板時，先用上面的 CLI move 驗證整條控制鏈。

1. 選 `right_arm` 或 `left_arm`，Start State 使用目前狀態，在 Joints 設定目標關節。
2. 可先按 **Plan** 預覽；這不會取得執行許可。
3. 在 Retail Task Controls 按 **MoveIt: Arm execution window (15 s)**，等待底部顯示 GUI execution window。
4. 在 **15 秒執行窗口** 內按 **Plan & Execute**，重新規劃並執行；不要重用先前快取的 Execute 路徑。
5. 窗口結束會撤回許可並確認停止。按 **End window / stop owned motion** 可取消此面板持有的窗口。已進入 HOLD 的假硬體工作階段用 `make moveit` 重新開始。

也可在另一個 shell 使用 `manual_control prepare`，但不要與 UI 同時取得 lease。兩者沿用同一後端。

也可明確撤回許可：`ros2 run retail_core manual_control disarm`。純指令控制建議使用上面的 move，會自動完成規劃與許可生命週期。

## 原版 VQA UI，改用 ROS 2

對應原本的 `run_vqa_ui.sh yolo-assisted --no-segmentation`：

```bash
# 新主機先建立 GPU 基底；本機已有 vqa-worker 映像可略過這行
make vqa-build
# 建立 YOLO-assisted 依賴映像
make vqa-ui-build

# 同一份 checkpoint／同一部影片；預設就是這個檔名
make vqa-ui-video VIDEO=/data/video/0610_take3n4.mp4

# 改用 RealSense 即時 RGB
make vqa-ui
```

監看畫面沿用 VLMotion 的 Monitor 版面：影像、VQA 建議、YOLO 人數、延遲、理由與完整 raw 回答。UI 現在是 `ros2 run retail_core vqa_ui` 節點，訂閱 ROS Image、RiskAdvisory 與帶 capture stamp 的推論細節，不自行開影片或相機、不發運動命令。

影片由 ROS camera_source 發布；RealSense 由 ROS driver 發布。GPU worker 沿用原本 PersonBoxDetector、PersonMotionHints、ROBOT_QUERY、schema 驗證與 constrained decoder；YOLO 只提供過去到 NOW 的數值證據，沒有 bbox action 規則。保留 no-segmentation，prompt 固定為 checkpoint 訓練相容版本。

影片播放完會回報 DATA_LOSS，不把跨次播放的記憶接在一起；再次執行 `make vqa-ui-video` 重新播放。Pause 只暫停 UI 影像顯示，ROS 感知持續運作。無效／過期回答維持 DATA_LOSS，先前 raw 僅留作 debug。

現在 UI 額外保留 **Last valid** 的決策與年齡；當前失效時清楚標示 history only／CURRENT DATA_LOSS。這是歷史顯示，不是過期的移動許可。推論完成改為立即通知 ROS 更新，capture stamp／2 秒有效期不變，仍可能因推論抖動或 schema 拒絕而 HOLD。原因與量測方式見 [VQA 說明](docs/OPERATIONS.md#4-vqa-為什麼-data_loss現在怎麼延續決策)。

## 真相機 VQA＋Nav2 地圖導航

第一次練習建議先照 [Nav2／MoveIt 分模式教學](docs/DOCKER_USAGE.md#10-nav2-與-moveit-到底先開哪一個) 用 `make nav-demo` 完成一次 CPU 模擬導航，再切換真相機 VQA。這個合成模式與真模型測試分開，不會覆蓋真模型輸出。

```bash
# 宿主機桌面終端：開啟 RealSense、真模型、原版 UI 與 RViz
make nav-vqa

# 另一個宿主終端
make shell
```

目前 RViz 有 6×4 m 的簡單地圖，範圍 x=[-2,4]、y=[-2,2] m。**地圖、雷射和底盤位移是模擬；影像與 VQA 是真實來源**，相機看見的房間沒有自動重建為這張地圖。可先以近距離目標測試許可／停止流程。

等待 UI 顯示新鮮、有效的 `CONTINUE`，並確認雙臂收納、底盤停妥。在容器內明確重設啟動／資料來源切換造成的 HOLD：

```bash
ros2 run retail_core demo_control reset
ros2 topic echo /mission/state --once
# 等待 RESETTING 結束；成功為 DONE。若 HOLD，查看 reason，排除原因後才重設。

# 新目標（map 座標，公尺；yaw 為 rad）
ros2 run retail_core demo_control goto --x 0.6 --y 0.2 --yaw 0 --wait

# 不移動時，完整查看任務／閘門／VQA／里程計原因
ros2 run retail_core demo_control status

# 取消並維持 HOLD
ros2 run retail_core demo_control cancel
```

也可在 RViz 上方用 **2D Goal Pose** 在可行走區域按住拖曳朝向。它發布 `/mission/goal_pose`，由任務管理器持有 Nav2 goal；執行中、HOLD、過期、重複或錯誤 frame 的目標會被拒絕，不會排隊重送。導航完成後回到 DONE 並禁動。

現在也可直接用 Retail Task Controls 的 **Reset HOLD → Set goal on map → 在地圖拖曳 → Cancel task**，不必每次用 CLI；先看面板中的任務／Gate／VQA 原因。

導航預設是 **TopDownOrtho 俯視圖**，顯示 Navigation Map、機器人、紅色模擬雷射、綠色 Nav2 路徑。它不是另一個網頁或獨立小地圖程式，也不需要 AMCL 的 2D Pose Estimate：目前 fake_base 已提供 map／odom 對齊。`Accepted map goal` 只表示接收到目標；`--wait` 會顯示後續 DONE／HOLD，status 可查看實際原因。

UI 頂端顯示任務階段、閘門許可／HOLD 與底盤 x/y。`CONTINUE` 是通行建議，`DANGER_WARNING` 依現有策略降為半速；`STOP`、`OBSERVE`、`DATA_LOSS` 或過期會拒絕運動許可，行進中取消導航並維持 HOLD。即使後來恢復 CONTINUE，也要先排除原因、確認停止／收納，再明確 reset、重新給目標。冷啟動或推論延遲超過有效期也會停下，不延長有效期來維持移動。

Isaac 已整理好 topic／frame／時間與重置契約，見 [Isaac 接口](docs/ISAAC_INTERFACE.md) 與 [配置範本](config/interfaces/isaac.example.yaml)。目前 `runtime_enabled: false`，沒有安裝或啟動 Isaac bridge；後續以它取代 fake_tidybot 的回饋來源。

## RealSense、模型與本機配置

這台主機已在 `.env` 設定來源／權重，並將 D435 序號放於忽略的 `config/local/cameras.yaml`。新主機參考 `.env.example`：

```dotenv
LOCAL_UID=1000
LOCAL_GID=1000
VQA_CHECKPOINT_DIR=./models/vqa/best
VQA_SOURCE_DIR=./vendor/lightweight_risk_vqa
VIDEO_DIR=./data/video
YOLO_MODEL_FILE=./models/yolo/yolov8n.pt
VQA_DECODING_PROFILE=sentence_safe
VQA_GENERATION_ENGINE=grammar_cached
REALSENSE_CONFIG=./config/local/cameras.yaml
```

UID/GID 依 `id -u`／`id -g` 設定。模型與來源唯讀掛載，不複製權重進 Git。相機範本在 `retail_bringup/config/cameras.example.yaml`；預設胸口純 RGB 640×480／15 FPS，多相機需唯一序號並核對各自 UVC 裝置授權。

目前所有 VQA 資產掛載都在這個專案內，沒有連到另一個 VLMotion 資料夾。`models/vqa/best` 是完整模型＋tokenizer bundle，`config/vqa` 保存指令與逐檔 hash；模型／影片仍由 Git 忽略，搬機要另外備份。宿主機執行 `python3 scripts/verify_vqa_assets.py` 可檢查副本。

`grammar_cached` 是本機假硬體實驗引擎：批次計算固定 grammar token，保留完整 evidence 和原 schema／2 秒 TTL。11 張離線配對的四個決策欄位相同、9 筆文字逐字相同；不能宣稱所有輸入等價。`hf` 可回退原版；新主機 `.env.example` 預設 hf，需另外評估後再選快取引擎。

```bash
make camera-list
make camera-logs
make camera-stop
```

主要 ROS 接口：

| Topic／action | 用途 |
|---|---|
| `/cameras/chest/color/image_raw`、`camera_info` | RGB 與內參 |
| `/perception/vqa/chest` | 帶時間戳、來源、證據與 model/prompt hash 的 RiskAdvisory |
| `/perception/vqa/chest/details` | 對應 capture stamp 的 YOLO／延遲／模式資料 |
| `/perception/vqa/chest/annotated` | 決策影像 |
| `/joint_states`、`/odom` | 機器人回饋 |
| `/mission/goal_pose` | RViz／CLI → 任務管理器的 map PoseStamped 新目標 |
| `/mission/state` | 任務階段、目標與拒絕原因 |
| `/navigation/cmd_vel` → `/base/cmd_vel` | Nav2 建議速度 → 本機閘門輸出 → 假底盤 |
| `/move_action` | MoveIt 規劃／執行 action |
| `/base/gate_state`、`/arm/gate_state` | 本地運動閘門狀態 |

## 開發、來源與 Compose

自己的五個 ROS 套件集中在 `ros2_ws/src`：`retail_description`、`retail_interfaces`、`retail_core`、`retail_rviz_plugins`、`retail_bringup`。四個上游用 `dependencies.repos` 鎖完整 SHA，放 `external/`，不使用 submodule、不直接改上游。

VQA 運行核心另有本次授權複製的 `vendor/lightweight_risk_vqa` 固定副本；GPU 掛載與 sim image 都使用它。external 的 VQA checkout 保留作來源查證，執行不再依賴另一個專案目錄。

```bash
# 首次取得上游與建立映像
docker compose build dev
docker compose run --rm dev python3 /workspace/scripts/fetch_sources.py
make build

# 進入可寫 Docker 工作區；改程式後在宿主機編譯
make dev
# 退出容器後：
make dev-build
make dev-restart
make shell
```

開發模式載入 `/workspace/ros2_ws/install` overlay；標準使用載入映像內 `/opt/retail_ws/install`。相機運行時用 `make vqa-dev` 協調重建，不單獨重啟 sim。依賴改 `docker/Dockerfile`／requirements 後重建；不用在宿主機安裝 ROS。

| Compose | 功能／使用 |
|---|---|
| 根 `compose.yaml` | 共用＋本機顯示入口；`docker compose exec sim bash` |
| `docker/compose.yaml` | 共用服務、CPU 工具鏈、model-tools；`make up GUI=0` |
| `docker/compose.local.yaml` | 本機 DISPLAY／私人 Xauthority；make 預設加入 |
| `docker/compose.dev.yaml` | 可寫工作區；`make dev` |
| `docker/compose.vqa.yaml` | 真 VQA／MediaPipe；`make vqa` |
| `docker/compose.ui.yaml` | 原版 YOLO-assisted 後端與 UI 模式；`make vqa-ui`／`make vqa-ui-video` |
| `docker/compose.moveit.yaml` | 獨立假硬體 MoveIt 控制；`make moveit` |
| `docker/compose.floorplan.yaml` | 你的尺寸地圖＋匹配假雷射；`make floorplan`／`make floorplan-vqa` |
| `docker/compose.acceptance.yaml` | 無外部網路的合成感知驗收；非日常啟動入口 |
| `docker/compose.realsense.yaml` | 相機設備定義，已由共用服務 extends；`make camera-list` |
| `docker/compose.vqa-build.yaml` | 建置基本 GPU worker；`make vqa-build` |

手動組合時從根目錄使用 `docker compose --project-directory . -f docker/compose.yaml -f docker/compose.<mode>.yaml ...`。不要把根 include 入口與子目錄 overlay 混在同一條 `-f` 鏈。本機 GUI 授權由 `make gui-setup` 準備，沒有虛擬桌面或網頁服務。

Nav2／任務示範在 `make up` 模式中，容器內執行 `ros2 run retail_core demo_control navigate` 或 `run`；手動 MoveIt 模式不接受這些任務。最新 UI／VQA 歷史顯示／27 個測試見 [任務 UI 驗收](docs/validation/2026-11-11-rviz-vqa-operations.md)，先前模式／真 VQA 到點結果見 [11/11 驗收](docs/validation/2026-11-11-navigation-moveit-profiles.md)，開發規範見 [AGENTS.md](AGENTS.md)。
