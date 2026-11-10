# 來源與套件取得

## 取得策略

外部 repo 使用根目錄 `dependencies.repos` 鎖定完整 SHA，不追隨浮動分支；VQA 指向 `LesterVQA` 的提交。這份清單是來源盤點，**不代表四個 repo 已經能一起編譯或執行**。上游有巢狀工作區、不同世代模型及重複機器人套件，不能全部直接塞進同一個 colcon 工作區。

進入 `docker compose run --rm dev` 後，可取得四個來源：

```bash
cd /workspace
mkdir -p external
vcs validate < dependencies.repos
vcs import external < dependencies.repos
vcs status external
vcs export --exact external > /tmp/itri-sources.repos
```

首次 import 可能取得較大的 Git 歷史與 mesh；OpenArm 當前 tree 約 520 MB，含 Isaac 與 RealSense 資產。不要自動更新已存在且有修改的 checkout。VQA 的 SAM3 submodule 先不遞迴下載，選定研究路徑需要時才依鎖定 gitlink 初始化。權重與 rosbag 不在這份清單中。

## 四個必要來源與選用範圍

| 來源 | 優先重用 | 不直接沿用 |
|---|---|---|
| `hrc-pme/openarm_dev` | `ros2_ws/src/openarm_can`、`openarm_description`、`openarm_ros2/{openarm_hardware,openarm_bringup,openarm_bimanual_moveit_config}` | Unity teleop、Isaac Lab、整份 RealSense 原始碼不作第一階段依賴；原 `colcon_build.sh` 寫死 `/root/openarm` 且清除 build，不從整合入口呼叫 |
| `Li-ChengWang/tidybot_ros2_control` | 根目錄 ROS 套件、base node、control mux、ArUco docking、相關測試 | 現有手把及 docking lease 不等於無人導航介面；Nav2 接入與最終閘門需另外設計。不要啟動原硬體 launch 做環境驗收 |
| `LesterLiou/VLMotion_VQA` | `ros2_ws/src/lightweight_risk_vqa` 的 ConvLSTM／Qwen 核心、`robot_vqa.py`、受限制解碼器、測試 | 舊 detector／temporal ROS 路徑與新整合模型分開。不可因 package 有 ROS launch 就宣稱新模型已支援 ROS 影像 |
| `Laiting20021202/social-safety-decision` | `realtime_safety/pipeline` 的手部／重建核心與 MASt3R adapter、模擬子目錄的 perception／safety 測試 | GUI 控制、Isaac `/joint_command` 和底盤退避原型不接正式執行鏈；OpenArm 定義避免與主來源重複 |

SHA 與來源證據見 [BASELINE](BASELINE.md)。

## ROS 套件從 apt 取得

開發映像已裝 Humble 的 `navigation2`、`nav2_bringup`、`moveit`、`ros2_control`、`ros2_controllers`、`rmw_cyclonedds_cpp`、`xacro`、`cv_bridge`、`image_transport`、`vision_msgs` 及 `tf2_geometry_msgs`。先用二進位套件，不額外 clone Nav2／MoveIt 全 repo。

相機驅動依實際型號選擇 `realsense2_camera` 或經驗證的 USB／廠商驅動；目前四個相機的型號、序號、內外參皆待確認。不能假設 OpenArm repo 的 RealSense 版本適合所有相機。

## OpenArm 第一階段工作區候選

只選五個來源，先檢查 `ros2_ws/src` 沒有同名套件，再在容器內建立 symlink：

```bash
cd /workspace/ros2_ws/src
ln -s /workspace/external/openarm_dev/ros2_ws/src/openarm_can openarm_can
ln -s /workspace/external/openarm_dev/ros2_ws/src/openarm_description openarm_description
ln -s /workspace/external/openarm_dev/ros2_ws/src/openarm_ros2/openarm_hardware openarm_hardware
ln -s /workspace/external/openarm_dev/ros2_ws/src/openarm_ros2/openarm_bringup openarm_bringup
ln -s /workspace/external/openarm_dev/ros2_ws/src/openarm_ros2/openarm_bimanual_moveit_config openarm_bimanual_moveit_config
cd /workspace/ros2_ws
colcon list
rosdep check --from-paths src --ignore-src --rosdistro humble
colcon build --packages-select openarm_can
source install/setup.bash
colcon build --packages-up-to openarm_bringup openarm_bimanual_moveit_config
```

這是 P1 的建置起點，尚未驗收；`rosdep check` 缺依賴時先把需要的 apt 項目加入 Dockerfile 並重建。非 root shell 不提供 sudo；不要為了補容器依賴改裝宿主機。此五套件選集保留 `openarm_can` 先建置的上游需求，P1 需驗證 CMake 相依排序。不要直接從整合根目錄執行無選集的 `colcon build`。

上游 bringup 的 `use_fake_hardware` **預設 false**。未加 `use_fake_hardware:=true` 的啟動指令不應用於開發驗收。P1 應新增強制 fake、關閉 GUI 且不對外提供實體設備的整合 launch，再驗證 action、取消和回饋。

## Thor 與模型：下一份獨立鎖定清單

避障來源已有 `mast3r_slam_adapter.py`／`mast3r_slam_worker.py`。其 `scripts/setup_mast3r_slam.sh` 會從公開 [MASt3R-SLAM](https://github.com/rmurai0610/MASt3R-SLAM) clone 浮動 HEAD、編譯 CUDA extension、修改相容性 patch 並下載 checkpoint。因此本次不執行它。

Thor GPU profile 啟用前，需另外記錄並鎖定：MASt3R-SLAM 實際 SHA、Eigen／其他子來源 SHA、PyTorch／CUDA 與 extension 架構、三個 MASt3R checkpoint 的 hash，以及 ConvLSTM＋Qwen bundle 和 tokenizer 版本。先驗證既有 adapter 能否在 Thor 以原核心執行；純 RGB 路徑與 RGB-D 擴充分開。模型以唯讀掛載提供。

不要求把模型研究 repo 全部改成 ROS。保留純 Python／CUDA 核心，以小型 ROS 2 adapter 收 `Image`、`CameraInfo`、TF，輸出帶時間戳和健康狀態的結果。這能避開 ROS Python 3.10 與 Thor worker 相依衝突。
