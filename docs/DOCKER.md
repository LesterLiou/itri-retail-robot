# Docker 開發與驗收

Docker Compose 是開發、模擬與三機部署的共同入口。目前提供 ROS 2 Humble smoke 映像、可編譯的 CPU 開發映像與開發工具鏈驗收。底盤、手臂及 Thor 工作負載的正式映像在查明套件與硬體版本後，依下列要求實作。本輪實測見 [驗收紀錄](validation/2026-11-10-dev.md)。

## 進入開發環境

在專案根目錄執行：

```bash
# 已有 .env 就保留；更換開發主機時檢查 UID/GID。
test -f .env || printf 'LOCAL_UID=%s\nLOCAL_GID=%s\n' "$(id -u)" "$(id -g)" > .env
docker compose config --quiet
docker compose build dev ros-smoke
docker compose run --rm dev
```

進入後在 `/workspace/ros2_ws`。宿主機整合資料夾掛到 `/workspace`，程式與 `build/install/log` 都會保留。開發使用者 UID/GID 由 `.env` 在建置時決定；改 UID/GID 後重建映像。`dev` 不提供 sudo。依賴應寫入 Dockerfile 並重建，避免手動修改容器造成無法重現。

```bash
# 宿主機：驗收工具鏈及 ROS 收發
docker compose run --rm dev-check

# 同一常駐開發容器可從多個終端進入
docker compose up -d dev
docker compose exec dev bash
# 工作結束
docker compose stop dev
```

互動 shell 與 entrypoint 會載入 `/opt/ros/humble/setup.bash`，有工作區 overlay 時再載入它。用 `docker compose exec dev bash -c '...'` 執行非互動指令時，需明確 `source /opt/itri/setup.bash`。

| Service | 用途／映像 | 網路與掛載 |
|---|---|---|
| `ros-smoke` | 最小 ROS 基底；兩個程序收發一個指定訊息 | `network_mode: none`、ROS localhost、無工作區掛載 |
| `dev`（dev profile） | 非 root Humble；MoveIt、Nav2、ros2_control、Cyclone DDS、colcon、vcs、rosdep、OpenCV、pytest、C++ 工具 | 一般 Compose bridge 可下載依賴；ROS localhost；掛載專案 |
| `dev-check`（test profile） | 同一 dev 映像；檢查介面 import、必要套件、ROS 訊息收發、暫存 ament／colcon 編譯 | `network_mode: none`；掛載專案，建置探針在 `/tmp`，結束清除 |

直接指定 service 會啟用對應 profile，無須額外 `--profile`；單純 `docker compose up` 只執行預設 smoke。[Compose profiles](https://docs.docker.com/compose/how-tos/profiles/)

三個 service 都沒有設備映射、host network、GPU、privileged 或 Docker socket，並移除 capabilities。開發容器的 localhost ROS graph 只用於單容器程序；跨容器模擬與三機 discovery 應另建配置。套件已包含 RViz，但沒有配置 X11／Wayland；無視窗開發可立即使用，視窗化驗證另加明確的 GUI profile。

## 映像與來源版本

Dockerfile 已鎖定官方 ROS 基底 manifest digest。`dev` 的 apt 相依從 ROS／Ubuntu repository 取得，**目前沒有 apt snapshot 鎖定**；新建置仍可能取得更新。正式發佈時保存映像 ID／registry digest 與 `dpkg-query -W` 清單，將已驗收映像推送 registry 後以 digest 部署，不宣稱不同日期的 apt build 位元完全一致。

```bash
docker image inspect itri-retail-robot:humble-dev --format '{{.Id}}'
docker compose run --rm dev bash -c 'dpkg-query -W > /tmp/packages.tsv; head /tmp/packages.tsv'
```

外部來源清單與取得方式見 [DEPENDENCIES](DEPENDENCIES.md)。模型／CUDA／相機 SDK、TidyBot Phoenix runtime 不在通用 CPU 環境中；先確認主機與功能路徑，再做各自運行映像。

## 先在 Docker 主機驗證

從解壓後的目錄執行以下指令。主機需具備 Docker Engine、Compose V2 與拉取 ROS 基底映像的存取權。 [ROS 映像](https://github.com/osrf/docker_images/blob/master/ros/humble/ubuntu/jammy/ros-base/Dockerfile) · [Compose 指令](https://docs.docker.com/reference/cli/docker/compose/run/)

```bash
docker version
docker compose version
docker compose config --quiet
docker compose build --pull ros-smoke
docker compose run --rm ros-smoke
```

最後一行應顯示 `PASS: ROS 2 message received between two container processes`，並以狀態碼 0 結束。收不到預期訊息時，測試應失敗退出。`ros-smoke` 不掛載機器人設備或工作區，且在隔離網路環境內使用本機 ROS 2 通訊。這項測試只涵蓋 ROS 2 容器啟動與訊息收發，三機 DDS、MoveIt 2、Nav2、GPU 與真機啟動各有後續驗收。 [Compose 網路設定](https://docs.docker.com/reference/compose-file/services/#network_mode)

## 常見環境問題

- `permission denied ... docker.sock`：本次開發工具的沙箱需用已核准的 daemon 執行通道；這不代表宿主機 Docker 壞掉。使用一般宿主機終端檢查 `docker version`。不要把 socket 改成全員可寫。
- `.env` UID/GID 與宿主機不同：更新後 `docker compose build dev`。不要在宿主機整份工作區做遞迴 chown。
- `colcon list` 沒有套件：起始 `ros2_ws/src` 本來只有佔位檔，按下一階段的套件選集取得來源；dev-check 只驗收工具鏈，沒有假裝執行機器人任務。

## 專案容器交付表

| 階段 | 應交付的容器能力 | 通過條件 |
|---|---|---|
| **P0** | 記錄三台主機 Docker Engine、Compose、OS、架構及 Thor 的 JetPack／Jetson Linux、GPU runtime。 | 在可用 Docker 主機執行本頁的 smoke 指令，保留實際輸出。查不到或無法執行的項目標為待驗證。 |
| **P1** | 在 CPU 映像內建立 `sim` 服務，納入 ROS 套件建置、假底盤與 OpenArm fake hardware。 | 乾淨 checkout 可用 Compose 建置並完成導航→停妥→鎖定→擺放→收回，包含取消、逾時及互鎖測試。測試容器無實體設備映射，使用隔離的 ROS graph。 |
| **P2 起** | 在功能接入時依次建立 `base-nuc`、`arm-nuc`、`thor` 的 Dockerfile 與 Compose 設定，並保留 `sim` 驗收。 | 兩個 NUC 在 x86_64 驗證各自映像與驅動相依。Thor 在實機 ARM64 上驗證 GPU 存取與至少一段模型推論。三機部署前驗證 DDS discovery、TF、時間同步與斷流反應。 |

正式部署記錄基底映像 digest、架構、上游 SHA 與套件版本。CAN／udev 驅動配置由宿主機管理，模型、校正與 rosbag 使用明確的外部掛載路徑。真機 Compose 獨立設定設備、網路與運動授權，避免把通用測試容器的隔離設定套到部署環境。Thor 的映像與 GPU runtime 依實機 JetPack／Jetson Linux 版本選定。如果 GPU 相依和 ROS 2 發行版需要不同環境，在 Thor 分成 GPU worker 容器與 ROS adapter 容器，並驗證本機 IPC。 [NVIDIA Thor 容器說明](https://docs.nvidia.com/jetson/agx-thor-devkit/user-guide/latest/setup_cuda.html)
