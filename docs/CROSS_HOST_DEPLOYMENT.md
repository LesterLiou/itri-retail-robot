# TidyBot 與 THOR 部署

兩台主機使用同一個整合 repo 版本。以下相機與 VQA 的 Compose profile 專用於觀測驗收，預設 ROS domain 為 84，Fast DDS 使用 `LARGE_DATA`。TidyBot 既有底盤容器在 domain 0。相機 profile 以既有 Jazzy 映像安裝 JPEG image transport，且只映射 D435 的 V4L 裝置。THOR 用 Jazzy adapter 及獨立的 NVIDIA ARM64 GPU worker。

各台從自己的 checkout 啟動 Compose，ROS 服務用 host network 參與同一個 DDS graph。Compose 預設用 `SUBNET` 自動探索。若網路上的 multicast 探索不穩，可在各台的本機 `.env` 設定 `OBSERVATION_PEERS`，多台以分號分隔。現場已設定此備援，平常啟動直接執行下方指令。機器換 IP 時更新本機 `.env`。

若目前終端先前已執行過 `export OBSERVATION_PEERS=...`，請先執行 `unset OBSERVATION_PEERS`。Compose 會優先使用終端環境變數。填入說明用的字串會使 ROS 節點啟動失敗。

## Clone 後補相依

| 主機 | 安裝／來源 | 指令或檢查 |
|---|---|---|
| 5070 | Docker Compose、整合模擬用的四個鎖定上游 | `docker compose build dev && docker compose run --rm dev python3 /workspace/scripts/fetch_sources.py && docker compose build sim` |
| TidyBot | 既有 `tidybot_ros2_control:jazzy-noble` 映像（含 `realsense2_camera`）、D435 | `docker image inspect tidybot_ros2_control:jazzy-noble`。原始碼在 `/home/hrc/itri-retail-robot/tidybot_ros2_control`，重建時用其原有 Compose 指令，先核對 `dependencies.repos` 的版本。 |
| THOR | Docker Compose、NVIDIA Container Toolkit、Jetson 相容 PyTorch、VQA checkpoint | Compose 建置 Jazzy ROS 介面及 Python 相依，模型由 `VQA_CHECKPOINT_DIR` 掛載。已確認遠端 `models/vqa/best` 有 `manifest.json`、`model.pt`、`tokenizer/`、`language_config/`。 |

`external/` 由 5070 的來源取得腳本補齊。這次兩個遠端的觀測服務只用表列相依，THOR 的 VQA 原始碼已隨整合 repo 的 `vendor/lightweight_risk_vqa` 取得。模型權重需要使用團隊既有的 checkpoint 或另行複製。

## TidyBot 啟動相機

在整合 repo 根目錄執行。若從 5070 登入，指令為 `ssh TidyBot`。

```bash
cd /home/hrc/itri-retail-robot
ls -l /dev/v4l/by-path
docker compose --project-directory . -f docker/compose.cross-host.yaml up -d --build tidybot-camera
docker compose --project-directory . -f docker/compose.cross-host.yaml exec tidybot-camera \
  bash -c 'source /opt/ros/jazzy/setup.bash; ros2 topic echo /cameras/tidybot/color/image_raw/compressed --once --field header --no-daemon --qos-reliability best_effort'
```

若 D435 枚舉順序改變，在本機 `.env` 更新 `RS_VIDEO0` 至 `RS_VIDEO5` 的實際 `/dev/video*` 路徑。TidyBot 已設定 `RS_VIDEO4=/dev/video6` 和 `RS_VIDEO5=/dev/video7`。確認只有一個程序擷取相機。driver 會查詢深度介面的 profile，觀測配置只啟用 RGB 串流。

## THOR 啟動 VQA

在 THOR 本機終端或 `ssh -t THOR` 的終端操作。當前 `hrc` 帳號需要使用有權限的 Docker 操作方式。以下用互動式 `sudo`，需要主機操作者輸入自己的密碼。

```bash
cd /home/hrc/itri-retail-robot
sudo docker info --format '{{json .Runtimes}}'
sudo docker compose --project-directory . -f docker/compose.cross-host.yaml \
  up -d --build --wait vqa-worker thor-adapter
curl -fsS http://127.0.0.1:8090/health
```

確認影像和推論輸出（兩個 `exec` 在 THOR）：

```bash
sudo docker compose --project-directory . -f docker/compose.cross-host.yaml exec thor-adapter \
  bash -c 'source /opt/ros/jazzy/setup.bash; ros2 topic echo /cameras/tidybot/color/image_raw/compressed --once --field header --no-daemon --qos-reliability best_effort'
sudo docker compose --project-directory . -f docker/compose.cross-host.yaml exec thor-adapter \
  bash -c 'source /opt/ros/jazzy/setup.bash; source /opt/observation/install/local_setup.bash; ros2 topic echo /perception/vqa/tidybot --once --no-daemon'
```

`/health` 應回報 `ready=true`。有有效推論時應觀察到 `valid=true`、`synthetic=false` 和原始影像擷取時間。停止相機後 adapter 應回報 `DATA_LOSS`。如果相機本機有訊息、THOR 看不到，先在兩端核對 `ROS_DOMAIN_ID`、`RMW_IMPLEMENTATION`、`ROS_STATIC_PEERS`、時鐘以及 Wi-Fi 網段對 DDS UDP 的允許範圍。用 `ros2 topic list --no-daemon` 排除 CLI daemon 的舊 domain。

## 5070 監看回傳

在 5070 的桌面終端開啟既有 VQA Monitor，讀取 domain 84 的 TidyBot JPEG、THOR 判斷、理由、耗時及原始回傳：

```bash
make gui-setup
docker compose --project-directory . -f docker/compose.cross-host.yaml \
  run --rm --no-deps --build monitor-5070
```

若要從終端直接看 ROS 訊息：

```bash
docker compose --project-directory . -f docker/compose.cross-host.yaml \
  run --rm --no-deps --build observer-5070 \
  ros2 topic echo /perception/vqa/tidybot --no-daemon
```

## Topic 與自動控制接點

| 方向 | ROS 介面 | 作用 |
|---|---|---|
| TidyBot → THOR，domain 84 | `/cameras/tidybot/color/image_raw/compressed`（`sensor_msgs/msg/CompressedImage`），另有 `/cameras/tidybot/color/camera_info` | RGB、擷取時間、光學 frame 及相機內參。跨機用 JPEG，影像訂閱使用 Sensor Data QoS。本機亦可訂閱原始 `/cameras/tidybot/color/image_raw`。 |
| THOR adapter → 本機 GPU worker | `http://127.0.0.1:8090/infer` | adapter 壓縮當前影像並請求推論，保留擷取時間。 |
| THOR → TidyBot，domain 84 | `/perception/vqa/tidybot`（`retail_interfaces/msg/RiskAdvisory`） | 風險建議，含擷取時間、有效期及來源。兩端使用同版訊息定義。 |
| TidyBot 驅動 → TidyBot 控制程式，domain 0 | `/odom`（`nav_msgs/msg/Odometry`） | 直行距離及實際停妥回饋。 |
| TidyBot 現有控制輸入，domain 0 | `/joy`，及程式所定義的 `/teleop/cmd_vel`、`/docking/cmd_vel` | 原手把／停靠控制，須核對實際啟動模式後再做仲裁接入。 |
| TidyBot 仲裁 → 既有驅動，domain 0 | `/cmd_vel`（`Twist`）、`/tidybot/enable`（`Bool`）、`/tidybot/control_frame`（`String`） | 由 TidyBot 本機的單一控制鏈寫入。 |

下一階段在 TidyBot 加入明確的自動任務入口和獨占控制權，並只把 `RiskAdvisory` 從觀測 domain 引入本機控制端。以初始朝向與 `/odom` 回饋完成 2 公尺。收到有效 `DANGER_WARNING` 降速，`CONTINUE` 維持仍獲許可的任務，`STOP`、失聯或里程計逾時撤銷許可並確認停妥。需先在 fake hardware 驗證手把接管、取消、重啟和停止鎖定，再由負責人確認真機控制設定。本階段沒有真機自動行走指令。

查詢現有底盤 graph（在 TidyBot 執行，只讀）：

```bash
docker exec tidybot_ros2_control bash -c 'source /opt/ros/jazzy/setup.bash; ros2 topic info /cmd_vel --verbose --no-daemon; ros2 topic echo /odom --once --no-daemon'
```

## 2026-11-11 實測

- 5070 的 Jazzy adapter 映像建置成功，`VQAAdapter` 與 `RiskAdvisory` 匯入成功。THOR 以原生 Jazzy 另建置兩個 ROS 套件成功。
- TidyBot 使用現有 Jazzy 映像與六個 V4L 裝置啟動 D435，實際輸出 RGB8 640×480／15 FPS，且本機讀到 `tidybot_color_optical_frame` 的影像 header。該映像未安裝 JPEG transport，部署使用原始 RGB topic。
- 雙向 UDP 短封包測試成功。THOR 使用原生 Jazzy 在 domain 84 測試子網探索和雙向靜態 peer，跨機 RGB 訂閱逾時。切換正式相機服務時，暫存容器仍佔用相機，D435 重新枚舉。移除暫存容器並更新裝置映射後，已在正式服務本機重新收到 RGB。
- THOR 的 Docker socket 對目前 SSH 帳號拒絕存取，`sudo -n` 要求密碼。THOR GPU worker 尚未驗收。


## 後續 JPEG 與 THOR 診斷

- 5070 Jazzy 觀測映像完成建置。Humble 隔離模擬的導航驗收通過，另有四個 raw／JPEG 解碼與結果有效期測試通過。
- TidyBot 已建置 `ros-jazzy-compressed-image-transport` 並在本機收到 JPEG header。UDP 訂閱逾時。兩端切換 `LARGE_DATA` 後，THOR 原生 Jazzy 訂閱收到 `tidybot_color_optical_frame` 與擷取時間。
- THOR 與 5070 Compose observer 都以 best-effort QoS 收到 JPEG。TidyBot 的 domain 0 實際由 `tidybot_gamepad_teleop` 直接發布 `/cmd_vel`，由 `tidybot_base_node` 訂閱。自動控制接入前須先切換成單一仲裁出口。
- 5070 的隔離假底盤以 `demo_control goto --x 2.0 --y 0.0 --wait` 完成前方目標，最終里程計約 `(1.943, 0.007)` m，線速度及角速度為零。使用模擬 Nav2 到達容許誤差與 VQA fixture。
- 假底盤故障測試通過 STOP 鎖定、後續 clear／CONTINUE 無法續跑和重複底盤發布者觸發 HOLD。
- THOR HTTP health 回報模型已就緒，PyTorch 為 `2.10.0a0+b558c986e8.nv25.11`，回報的模型 SHA-256 與 THOR 的 checkpoint 相同。舊 worker 拒絕 `tidybot` 相機 ID，已同步修正到 THOR。使用既有 `chest` ID 的 HTTP 測試中，預設解碼 25 秒未回應，另一個解碼測試回報 `Each query must contain exactly one attended <seg> token`。GPU 推論仍待驗收。
- THOR 更新後需重建 adapter 並重啟 worker，SSH 帳號需要互動式 Docker sudo。有效期維持 2 秒。

## 相機斷流與 CUDA 診斷

- TidyBot 的 DDS peer 曾被設成說明用的字串，驅動初始化時無法解析位址。修正本機 `.env` 並重建相機容器後，THOR 原生 ROS 重新收到新 JPEG。
- THOR 的 HTTP worker 直接測試回報 CUDA `device-side assert triggered`。診斷時以 `VQA_CUDA_LAUNCH_BLOCKING=1` 重建 worker 並查看 `vqa-worker` 的 traceback，以追蹤第一個失敗的 CUDA 操作。日常運行預設為 `0`。
- THOR 容器日誌確認舊 `25.11-py3-igpu` 映像只列出 `sm_87`，本機 GPU 為 `sm_110`。部署改用 NVIDIA 標準 `25.11-py3` 並鎖定 manifest digest。啟動時記錄裝置能力與 PyTorch 架構清單，通過一次 CUDA 計算才開啟 HTTP 服務。新映像仍需在 THOR 建置及完成實際推論驗收。
- 後續 THOR 已以新映像完成實際 GPU 推論。worker 修正 checkpoint 的 `<seg>` 編碼並在啟動時核對推論裝置上的 token。預設 HF 解碼測得 4.908 秒，超出 2 秒有效期。使用 `grammar_cached` 對實體 TidyBot 的一幀 JPEG 回傳 `valid=true` 和 `DANGER_WARNING`，推論 0.896 秒，從擷取到 HTTP 回傳 1.503 秒。該次單幀驗收以 SSH 傳送，DDS 串流與 5070 回傳仍待驗證。THOR Compose 預設改用 `grammar_cached`，更新後需重新建立 worker。
