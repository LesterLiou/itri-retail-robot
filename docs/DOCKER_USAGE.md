# Docker 使用教學：從開啟到關閉

想直接用 RViz 按鈕操作 Nav2／MoveIt，請先看 [最新 UI 操作與完整架構](OPERATIONS.md)。本篇保留 Docker 與 CLI 基礎。

這份教學適用於目前的專案。全部 ROS 2 程式在 Docker 裡執行，視窗直接顯示在 Ubuntu 本機螢幕。現在接的真硬體只有 RealSense，相機判斷影響的是假底盤／假手臂；Isaac 尚未啟用。

## 1. 先分清楚你在哪裡輸入指令

**宿主機**是你平常的 Ubuntu 終端機，提示符通常長這樣：

```text
(base) lester@hrc-MS-7D98:~/obstacle_avoid$
```

**容器裡**的提示符通常長這樣，實際目錄會依模式不同：

```text
developer@容器編號:/opt/retail_ws$
```

`make`、`docker compose` 指令都在**宿主機專案根目錄**執行；`ros2` 指令通常先進入容器再執行。不需要先啟動宿主機的 ROS，也不需要先進入容器才能啟動系統。

## 2. 最常用流程：真相機＋VQA＋RViz 導航

### 終端機 A：啟動系統

在**宿主機已登入桌面的終端機**輸入：

```bash
cd /home/lester/obstacle_avoid
make nav-vqa
```

它會依序準備顯示授權、啟動 GPU worker、啟動 ROS 系統、啟動相機、開啟 VQA 監看視窗。RViz 也會顯示在本機桌面。第一次載入模型可能需要等待。

**這個終端機不回到 `$` 是正常的**，因為它正在執行 VQA 視窗。沒有一直印文字也不代表卡住。請另開終端機 B 操作，不要為了拿回命令列反覆按 Ctrl+C。

### 終端機 B：進入 ROS 容器

在**宿主機**輸入：

```bash
cd /home/lester/obstacle_avoid
make shell
```

進入的是已存在的 `sim` 容器，不會新增一個容器。可以同時開多個終端機，各自 `make shell`。

在**容器裡**確認狀態：

```bash
ros2 node list
ros2 topic echo /mission/state --once
ros2 topic echo /base/gate_state --once
```

等 VQA 有新鮮有效的 CONTINUE，並確認底盤停止、雙臂收納。需要解除 HOLD 時，明確重設：

```bash
ros2 run retail_core demo_control reset
ros2 topic echo /mission/state --once
```

reset 的回覆只代表收到要求。等待 RESETTING 結束、確認為 DONE；如果仍為 HOLD，先看 reason 並排除問題。不要一直 reset。

用 RViz 的 **2D Goal Pose** 指定新目標，或在容器輸入：

```bash
ros2 run retail_core demo_control goto --x 0.6 --y 0.2 --yaw 0
```

座標是簡單地圖上的公尺，yaw 是 rad。這張地圖／雷射／底盤位移為模擬，真相機拍攝的房間沒有自動變成地圖。VQA 資料失效時會停止並維持 HOLD；恢復後需確認狀態、明確 reset，再給新目標。

### 測試結束：停止任務、退出、停止系統

若正在移動，先在**容器裡**取消：

```bash
ros2 run retail_core demo_control cancel
ros2 topic echo /base/gate_state --once
```

確認底盤停止並維持 HOLD，接著退出這個 shell：

```bash
exit
```

`Ctrl+D` 也能退出 shell。此時**容器仍在運行**。

回到**宿主機**停止整套專案服務：

```bash
cd /home/lester/obstacle_avoid
make stop
make status
```

`make stop` 先停相機，再停 sim、dev、vqa-worker，釋放相機與 GPU 程序。相關視窗也會關閉；終端機 A 可能因此印出連線中斷／make 結束訊息。`make status` 不應再列出這些正在運行的服務。

## 3. Ctrl+C、Ctrl+D、關閉視窗到底差在哪裡？

| 你在哪裡做什麼 | 結果 |
|---|---|
| `make shell` 進入後輸入 `exit` 或按 Ctrl+D | 離開這個 shell；ROS、相機和容器繼續跑 |
| 在 `ros2 topic echo`、`make logs`、`make camera-logs` 按 Ctrl+C | 停止這次觀看資料／追蹤 log；服務繼續跑 |
| 在 `make nav-vqa` 的前景 VQA UI 按 Ctrl+C | 要求結束這個 UI；後台 sim、相機、worker 繼續跑 |
| 關閉 VQA UI 視窗 | 只關監看 UI，不停止模型推論或導航 |
| 關閉 RViz 視窗 | 只關 RViz，不代表任務停止；要停任務用 cancel |
| 在宿主機執行 `make stop` | 停止本專案的 sim、相機、GPU worker 和 dev 服務 |
| `docker compose run --rm dev` 裡按 Ctrl+D | 因為這是一次性容器的主 shell，容器會結束並移除；不同於 `make shell` 的 exec |

**平常最容易記的方式：`exit` 離開容器 shell，`make stop` 關閉系統。** Ctrl+D 不會替你停止後台服務。也不要把關閉 UI 當成取消導航。

## 4. 總共有幾個 container？每一個做什麼？

完整真相機導航的主要運行容器是 **3 個**：

| Compose 服務 | 容器名稱 | 功能 | 使用的 image |
|---|---|---|---|
| `sim` | `itri-retail-sim-1` | ROS 2、Nav2、MoveIt、合併模型、假機器人、閘門、任務管理、感知 adapters、RViz 和 VQA UI | `humble-sim` |
| `cameras` | `itri-retail-cameras-1` | RealSense ROS 2 驅動，發布 RGB／內參；唯一取得相機設備的常駐服務 | 同一個 `humble-sim` |
| `vqa-worker` | `itri-retail-vqa-worker-1` | GPU YOLO／Qwen 推論，透過 HTTP 回傳結果給 ROS adapter | YOLO-assisted 模式用 `vqa-yolo` |

資料走法：

```text
cameras ──ROS RGB──→ sim 裡的 VQA adapter ──HTTP──→ vqa-worker
                             ↑                       │
                             └──────推論結果──────────┘
                             ↓
                       ROS 決策／閘門／UI
```

RViz 和 VQA UI 是 `sim` 裡的兩個程式，**各自不會新增容器**。OpenArm／TidyBot 的上游 repo 也是來源套件，不是一個 repo 就一個容器。

另有工具服務，日常導航不用全部開：

| 服務 | 用途／什麼時候用 |
|---|---|
| `dev` | 獨立 ROS 編譯工具箱、首次抓上游；不會自行開完整機器人。日常整合開發優先用 `make dev`，它使用的是可寫的 sim |
| `model-tools` | 重新從提供的 USD 轉出底盤模型，`make model-convert`；完成即移除一次性容器 |
| `camera-probe` | `make camera-list` 探測相機，完成即移除 |
| `dev-check` | 獨立工具鏈驗收，完成後結束 |
| `ros-smoke` | 最小 ROS 訊息收發測試，不是完整系統 |

2026-11-11 查證時有 **4 個正在運行的容器**，因為先前的獨立 `dev` 也還開著。它不是導航必要容器，不會每次自動多開。要只停這個工具箱，在宿主機輸入 `docker compose stop dev`。

| 你想做什麼 | 啟動命令（宿主機） | 最少需要的常駐容器 |
|---|---|---|
| CPU 模擬＋RViz，不用真相機／模型 | `make up` | sim：1 個 |
| 你的尺寸圖＋CPU 假導航 | `make floorplan` | sim：1 個 |
| 你的尺寸圖＋真相機 VQA＋假導航 | `make floorplan-vqa` | sim、cameras、vqa-worker：3 個 |
| 真相機＋VQA＋UI＋Nav2 | `make nav-vqa` | sim、cameras、vqa-worker：3 個 |
| 影片＋VQA＋UI | `make vqa-ui-video` | sim、vqa-worker：2 個 |
| 真相機／VQA，沒有額外 VQA UI | `make vqa` | 3 個；使用基本 vqa-only 後端 |
| MoveIt 假手臂控制 | `make moveit` | sim：1 個 |
| CPU 整合開發 | `make dev` | 可寫 sim：1 個 |
| 真相機／基本 VQA 整合開發 | `make vqa-dev` | 可寫 sim、cameras、vqa-worker：3 個 |

表格是最低需求。舊模式的 GPU worker 或工具箱可能還在背景，`make up` 不會把它們全部關掉。想乾淨切換模式，先取消任務、`make stop`，再開新模式。用 `make status` 查看實際運行數量；`docker compose ps -a` 也會列出停止的容器。

`compose.floorplan.yaml` 疊加到既有 sim，指定你的靜態地圖與匹配的假雷射幾何，不會新增常駐容器。尺寸、地圖檔與定位需求見 [地圖教學](NAVIGATION_MAPS.md)。一般直接使用上述 Make 入口；進入／退出 shell、停止服務的方式相同。

## 5. 什麼是 build？什麼時候需要？

**Image 是已安裝好套件的環境範本；container 是用 image 開起來的一個執行環境。** 一個 image 可以開多個 container，例如 sim 和 cameras 共用同一個 image。

- `build`：建立／更新 image，沒有因此開啟完整系統。
- `up`：用 image 建立或啟動服務，已存在的服務可能直接沿用。
- `exec`：進入正在跑的容器，不新增容器。
- `stop`：停掉容器，但保留 image 和宿主機程式檔案。

**這台電腦已有映像，平常啟動不需要 build。** 第二天使用通常直接 `make nav-vqa`。

| 變更／情境 | 要做什麼（宿主機） |
|---|---|
| 沒改程式，只是重新使用 | 直接啟動，不 build |
| 改整合 ROS 程式、launch、URDF 或 RViz 預設設定，想更新標準使用映像 | `make build`，再取消任務、`make stop`、重新啟動所需模式 |
| 開發模式改整合 ROS 程式 | 在下面第 6 節使用 `make dev-build`；不用每次重建完整 Docker image |
| 改 CPU 系統套件／Dockerfile | `make build` 更新 sim；獨立 dev 也需要時另外 `docker compose build dev` |
| 改 GPU 基底／Torch／CUDA 依賴 | `make vqa-build`，再 `make vqa-ui-build` 更新其衍生映像，然後停止／重新啟動 |
| 只改 YOLO requirements／Dockerfile.vqa-yolo | `make vqa-ui-build`，然後停止／重新啟動 |
| 只改 Compose 或本機 `.env` | 通常不用 build；取消任務、停止後以原 Make 模式重新啟動，讓設定重新套用 |
| 只改唯讀掛載的 `docker/vqa_worker.py` | 不需 build，停止並重新啟動原模式讓 Python 重載程式 |

如果換全新主機，設定好 `.env` 的權重／影片／原始碼路徑、GPU runtime 與相機配置後，首次建置順序為：

```bash
# 宿主機；本機已準備完成則不必重跑
cd /home/lester/obstacle_avoid
docker compose build dev
docker compose run --rm dev python3 /workspace/scripts/fetch_sources.py
make build
make vqa-build
make vqa-ui-build
make nav-vqa
```

權重、影片和提供的 USD 不是這些指令自動取得的資料，需依 [主 README](../README.md#realsense模型與本機配置) 配置既有檔案。`make build` 會編譯選定的上游與五個整合套件；不要手動修改 external 上游來修整合問題。

## 6. 我想改 ROS 程式，怎麼開發？

標準使用模式將 `/workspace` 唯讀掛載；要修改／編譯，切到可寫開發模式。

```bash
# 宿主機：先取消正在執行的任務，再停止
make stop
make dev
```

這會啟動可寫的 **sim** 並進入其 shell，不是再開一個叫 dev 的工具箱。宿主機的 `ros2_ws` 與容器 `/workspace/ros2_ws` 是同一份資料。可在宿主 IDE 修改自己的五個整合套件。

編譯和重新載入：

```bash
# 在容器 shell 先離開；不會停止後台 sim
exit

# 回到宿主機
make dev-build
make dev-restart
make shell
```

`make dev-build` 也在 Docker 內執行 colcon，只是你從宿主機呼叫它。這時使用 `/workspace/ros2_ws/install` 開發 overlay；標準使用模式使用 image 中的 `/opt/retail_ws/install`。開發完成要讓標準模式也取得修改，執行 `make build` 並停止／重新啟動。

若開發時要接真相機／基本 VQA，改用 `make vqa-dev`。編譯後以 `make vqa-dev` 協調重新啟動，不單獨 restart sim，因為 cameras 共用 sim 的網路 namespace。這個入口是 vqa-only，不是 YOLO UI 專用開發 profile。

## 7. 影片與 MoveIt 怎麼開？

### 影片模式

```bash
# 宿主機：切換模式前取消任務並停止
make stop
make vqa-ui-video VIDEO=/data/video/0610_take3n4.mp4
```

`/data/video` 是容器內路徑，對應 `.env` 裡 `VIDEO_DIR` 掛載的宿主影片資料夾。影片播完為 DATA_LOSS，再執行此命令會建立新的播放工作階段。

### MoveIt 模式

```bash
# 宿主機
make stop
make moveit
make shell
```

以下在**容器裡**，只操作假手臂：

```bash
ros2 run retail_core manual_control move \
  --group right_arm --joints 0 0 0 0.3 0 0 0
ros2 run retail_core manual_control move \
  --group right_arm --joints 0 0 0 0 0 0 0
exit
```

回到宿主機後 `make stop`。RViz 手動執行窗口的詳細操作見 [主 README 的 MoveIt 說明](../README.md#moveit-控制)。此模式不啟動 Nav2／任務管理器，不能同時使用 goto 導航。

## 8. 沒有視窗、看不到影像，怎麼檢查？

### 只印 QStandardPaths / XDG_RUNTIME_DIR

```text
QStandardPaths: XDG_RUNTIME_DIR not set, defaulting to '/tmp/runtime-developer'
```

這行本身不是啟動失敗。它是 Qt 的路徑提示，UI 仍可能已開啟。檢查其他螢幕、工作區、Alt+Tab。VQA 視窗標題包含 **ROS 2 Monitor**；容器使用啟動時的 DISPLAY，而不是在網頁裡顯示。

### VQA 有開，但 RViz 不見

目前查到的情況是 RViz 曾以 exit code -11 結束，而 sim 其餘 ROS 程序還活著。`up -d sim` 沿用同一容器，不會替已退出的子程式重新開視窗。這不表示需要重建全部 image，也不能只由 Qt 路徑提示判斷原因。

在**宿主機桌面終端**輸入：

```bash
cd /home/lester/obstacle_avoid
make rviz
```

它在目前 sim 內讀取運行中的 MoveIt 模型，再重開 RViz；不重啟控制器、不發運動命令、也不新增 container。已運行時不重複啟動。`make nav-vqa` 現在也會檢查這個入口。

若仍沒有開啟，從**宿主機**看獨立 RViz 記錄：

```bash
docker compose exec sim tail -n 40 /tmp/retail-rviz.log
```

### 相機／VQA／整套系統狀態

以下都在**宿主機**執行：

```bash
make status                    # 目前有哪些服務運行
make camera-logs               # 相機 log；Ctrl+C 只離開 log
make logs                      # ROS 系統 log；Ctrl+C 只離開 log
docker compose logs --tail=60 vqa-worker
docker compose exec sim bash    # 與 make shell 相同
```

相機只允許指定設備。探測前先 `make camera-stop`，再 `make camera-list`；探測完成後重新 `make nav-vqa`。不要在另一個容器／宿主程式同時佔用同一相機。

如果出現 `could not connect to display`／xcb，請從已登入 Ubuntu 桌面的終端重新執行啟動命令；不要從沒有桌面授權的 SSH shell 直接開 GUI，也不要用 `xhost +`。`make gui-setup` 只準備授權，環境中的 DISPLAY 改變時還要停止後重新啟動原模式。

## 9. 哪些資料會留下？需要清除容器嗎？

- 宿主機 repo、README、原始碼、權重和影片不會因 `make stop` 刪除。
- 開發的 build／install／log 在掛載的 `ros2_ws`，退出 shell 後仍保留。
- 容器自己的 `/tmp` log 在容器重建後可能消失；要保留驗收結果需另外輸出至宿主資料夾。
- `docker compose down` 比 stop 更進一步，會移除該 Compose 專案的服務容器與網路；日常使用通常不需要。
- 不需要每天刪 image、重建 image 或執行 Docker 全域 prune。正常順序就是：**啟動 → 使用 → 取消任務／確認停止 → exit → make stop**。

## 10. Nav2 與 MoveIt 到底先開哪一個？

你的 `make stop` → `make nav-vqa` 開法正確。三個綠色運行服務與一個停止的 dev 是合理狀態。

這兩種模式**輪流使用**，共用同一個 sim 服務：

| 模式 | 用途 | RViz 畫面 | 接受什麼控制 |
|---|---|---|---|
| `make nav-demo`／`make nav-vqa` | 底盤 Nav2 導航 | 地圖俯視、雷射、路徑＋MoveIt 預覽面板 | 2D Goal Pose／demo_control goto，由任務管理器持有目標 |
| `make moveit` | 手動練習手臂 | 3D 模型＋MoveIt Motion Planning | manual_control 或取得執行窗口後的 Plan & Execute |

`Start make moveit first; mission and manual modes are exclusive` 的意思是：你正處於導航模式，手動手臂控制被拒絕。不是少裝套件，也不需要另外開一個手臂 container。導航模式的直接 MoveIt Execute 同樣不會取得手動執行許可。

### A. 先用 CPU 練習 Nav2，確認地圖與走路都正常

宿主機執行；若有任務先在容器取消並確認停止：

```bash
make stop
make nav-demo
make shell
```

此模式只用合成感知與假硬體，沒有真相機／VQA。容器內：

```bash
ros2 run retail_core demo_control status
ros2 run retail_core demo_control goto --x 0.6 --y 0.2 --yaw 0 --wait
```

`--wait` 會觀看後續階段直到 DONE／HOLD／FAULT，最多監看 120 秒。它是結果監看時間，不是運動 watchdog；退出監看不會取消任務，要停請另用 cancel。正常結果應為 DONE、gate.enabled=false、里程計位置改變。

在 RViz：白／灰底黑邊區域就是地圖，不是另外跳出一張視窗；現在預設改成俯視。上方按 **2D Goal Pose**，在可行走區域按住左鍵拖出朝向再放開；綠線是 Nav2 全域規劃，機器人圖形隨 odom 移動，紅點是模擬雷射。任務未完成不要連續點多個目標，衝突目標會拒絕。需要停止時，在容器執行 `ros2 run retail_core demo_control cancel`。

目前假底盤自行提供 map／odom 對齊，不需要先設 AMCL 的初始姿態。也不要用標準 Nav2 panel 直接繞過任務管理器下 goal；本專案已把 2D Goal Pose 接到 `/mission/goal_pose`。

### B. 再接上真相機 VQA

退出容器，在宿主機：

```bash
make stop
make nav-vqa
# 這個終端機會維持 UI；另開宿主終端執行 make shell
```

新容器 shell 內：

```bash
ros2 run retail_core demo_control status
# 確認有效觀測、停止和收納後，必要時明確 reset
ros2 run retail_core demo_control reset
ros2 run retail_core demo_control status
# 等 reset 完成為 DONE，再給新目標
ros2 run retail_core demo_control goto --x 0.6 --y 0.2 --yaw 0 --wait
```

`Accepted map goal` 是「已接收」，不是「已允許移動」或「已到點」。不動時執行 `demo_control status`，查看：

- `mission.phase/reason`：在哪個階段、是否 HOLD。
- `gate.enabled/hold/reason`：是否真的允許移動、拒絕原因。
- `vqa.action/valid/capture_age_sec`：模型當前建議與擷取年齡。
- `odometry.vx/vy/wz`：是否有實際假底盤速度回饋。

真 VQA 的 OBSERVE、STOP、DATA_LOSS 或過期會阻止行進；CONTINUE 不會自動解除 HOLD。DANGER_WARNING 依既有策略降速。畫面有「最新 CONTINUE」也不能證明前一次 goal 執行時始終健康。先解決原因，再明確 reset、給新 goal；不要關 watchdog 或改模型輸出讓測試通過。

### C. 換成 MoveIt 操作右臂

**先退出舊 shell**，取消任務／確認停止後，在宿主機：

```bash
make stop
make moveit
make shell
```

先用容器 CLI 驗證：

```bash
ros2 run retail_core manual_control move --group right_arm --joints 0 0 0 0.3 0 0 0
ros2 run retail_core manual_control move --group right_arm --joints 0 0 0 0 0 0 0
```

成功後再操作 RViz 面板：

1. Planning Group 選 `right_arm`，Start State 選 `<current>`。
2. 在 **Joints** 分頁修改目標：`openarm_right_joint4` 設 0.3 rad，其他關節保持 0。Goal State `<current>` 本來就等於現在，未修改目標不會出現預期位移。
3. 可先按 Plan 預覽。規劃成功不代表已取得執行許可。
4. 在 Retail Task Controls 按 **MoveIt: Arm execution window (15 s)**，等待出現 GUI execution window。也可改用另一個容器 shell 的 `manual_control prepare`，但兩種 owner 不要同時使用。
5. 在 15 秒窗口內按 **Plan & Execute**，使用當前狀態重新規劃；不要重用先前的 Execute 快取路徑。
6. 結束後撤回許可。若失聯／取消／場景過期進入 HOLD，不反覆 Execute，先查看原因；假硬體可用 `make moveit` 重新開始一個工作階段。

左臂改選 `left_arm`，CLI 改 `--group left_arm`。沒有上述手動模式與執行窗口時直接按 Execute，失敗是預期的控制權拒絕；其他失敗仍需看具體 MoveIt／gate log。

### D. 為什麼之前一次出現兩個 RViz？

之前 ROS launch 與補開視窗的 helper 各自啟動 RViz，存在啟動競爭。現在兩者都走 image 內 `/opt/itri/open_rviz.py`，先取得同一把鎖再等待模型、開視窗。`make rviz` 也用這個入口，已開啟就不再另開。這次修改要先 `make build` 並停止／重新啟動原模式才會生效；執行中的舊 image 不會自動換成新程式。

## 11. ROS 2 原始碼在哪裡？我不用 colcon build 嗎？

宿主機自己的程式在 **`/home/lester/obstacle_avoid/ros2_ws/src/`**，容器透過掛載看見 **`/workspace/ros2_ws/src/`**。

| 套件 | 內容／常用檔案 |
|---|---|
| `retail_core` | `retail_core/mission.py` 任務、`gate.py` 底盤閘門、`arm_gate.py` 手臂閘門、`vqa.py` adapter、`vqa_ui.py` UI、`manual_control.py` 手動控制、`demo_control.py` CLI |
| `retail_bringup` | `launch/sim.launch.py` 整合啟動、`config/nav2.yaml` 導航參數、`maps/shop.yaml` 地圖、`rviz/navigation.rviz` 導航畫面、`rviz/retail.rviz` MoveIt 畫面 |
| `retail_interfaces` | `msg/` 與 `srv/` 介面 |
| `retail_rviz_plugins` | RViz 任務控制面板、Nav2 目標工具入口、手臂執行窗口 |
| `retail_description` | 底盤 URDF／mesh、OpenArm 裝配配置及來源紀錄 |

四個上游鎖定來源在 `external/`。權重不放在 ROS 套件內，依 `.env` 唯讀掛載。GPU HTTP worker 是 `docker/vqa_worker.py`。

**有做 colcon build，只是標準模式由 Dockerfile 幫你做了。** `make build` 會在 image 建置期間，把來源複製至 `/opt/retail_ws/src`，在 Docker 內 colcon build，產物為 `/opt/retail_ws/install`。因此你直接 ros2 run 可以執行，不用每次開機重編譯。

注意：宿主 IDE 修改原始碼，不會自動修改已建好 image 的 `/opt/retail_ws/install`。開發有兩種方式：

| 方式 | 操作 |
|---|---|
| 更新標準使用環境 | 修改來源後 `make build`，停止並重新啟動原模式 |
| 邊改邊編譯 | `make dev` 開可寫 sim，修改後 `make dev-build`、`make dev-restart` |

想親自輸入 colcon 的話：

```bash
# 宿主機
make stop
make dev

# 進入容器後
source /opt/itri/setup.bash
cd /workspace/ros2_ws
python3 /usr/bin/colcon build --symlink-install \
  --packages-select retail_description retail_interfaces retail_core retail_rviz_plugins retail_bringup
source install/setup.bash
exit

# 回到宿主機，重啟讓已運行的 ROS 節點載入新程式
make dev-restart
make shell
```

`make dev-build` 就是在容器裡執行上述編譯工作。新的 msg／srv 也要重新 build；只 source 不會產生介面。不要在唯讀的標準模式嘗試改 `/opt/retail_ws/install`，也不要在宿主 Conda 環境安裝一套 ROS 來混用。
