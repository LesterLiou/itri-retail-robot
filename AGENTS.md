# ITRI 便利商店機器人開發規範

本檔適用於整合 repo。先讀 [系統設計](docs/SYSTEM_DESIGN.md) 和本次任務範圍，再讀現有的 `docs/BASELINE.md` 與目標套件的指引。若既有 repo 已有 `AGENTS.md`，由維護者合併規則並保留更細層級規範。

## 主機分工與相依

| 主機 | 專案與職責 |
|---|---|
| RTX 5070（`ITRI_5070`） | 主要開發機。整合 repo 位於 `/home/lester/obstacle_avoid`，在此開發 ROS 2 adapters、Compose 設定、回放與 fake hardware 驗收。 |
| TidyBot（`ITRI_tidybot`，SSH alias `TidyBot`） | 移動載臺，接一台 RealSense D435。既有底盤控制在 `/home/hrc/itri-retail-robot/tidybot_ros2_control`。由本機擷取 RGB、讀取里程計，並經既有控制鏈仲裁速度與運動許可。 |
| THOR（`ITRI_THOR`，SSH alias `THOR`） | Jetson AGX Thor，遠端專案在 `/home/hrc/itri-retail-robot`。集中執行 VQA 與後續 RGB → MASt3R-SLAM → 點雲的感知運算。ROS adapter 負責收影像與發布結果，底盤速度和 enable 由 TidyBot 本機管理。 |

- 5070 可用既有 SSH alias 直連兩台主機。先以 `ssh -o BatchMode=yes TidyBot hostname` 和 `ssh -o BatchMode=yes THOR hostname` 確認。Codex 遠端專案標籤與 SSH alias 分開辨識。
- 上游 SHA 依 [dependencies.repos](dependencies.repos)，遠端 checkout 與執行環境依 [基線紀錄](docs/BASELINE.md)。5070 既有 Compose 模擬為 Humble，TidyBot 現有底盤容器與 THOR 已安裝的 ROS 為 Jazzy。跨機部署先在 Jazzy 核對共同介面與建置，THOR 的 GPU worker 依 Jetson Linux／CUDA 相依選映像。

## 目前階段｜單相機跨機 VQA 與 2 公尺直行

相依套件、兩台主機的啟動命令與 topic 對照見 [跨機部署](docs/CROSS_HOST_DEPLOYMENT.md)。

1. 在 TidyBot 用既有 RealSense 驅動發布單路 RGB `sensor_msgs/msg/Image` 與 `CameraInfo`。TidyBot 相機與 THOR 的 VQA adapter 使用同一 `ROS_DOMAIN_ID`，先驗證 DDS discovery、跨機網路介面、QoS、時鐘同步、影像擷取時間及斷流。沿用本機模擬 Compose 的 localhost DDS 設定前，須改用能跨機的部署配置。
2. THOR 重用 `retail_core` 的 VQA adapter 和相容 checkpoint，透過 remap 接入相機 topic。沿用 `/perception/vqa/<camera_id>` 的 `retail_interfaces/msg/RiskAdvisory`，兩端建置相同訊息定義。保留原始影像擷取時間、推論時間、有效期及來源。先做純觀測驗收，確認兩端都收得到有效結果並量測延遲。
3. 直行腳本部署在 TidyBot。以啟動時底盤前向為基準，用 `/odom` 沿該方向的位移回饋完成 2 公尺目標及確認停妥。腳本只提出速度請求，經本機仲裁與閘門送入既有控制鏈。先確認任務速度入口，現有 mux 只有手把與停靠輸入。
4. 有效且新鮮的 `DANGER_WARNING` 對應本機降速，`CONTINUE` 在許可有效時維持原直行計畫，`STOP` 觸發停車並保持鎖定。結果未知、失效、逾時、斷流或里程計失效時停止或拒絕啟動。停妥後依本機復歸流程另起新目標。
5. 先在隔離的 Compose fake hardware 測試到達距離、降速、停車回饋、取消、重啟、失聯與控制衝突。實體試車由負責人確認獨立防護及停止回饋後操作。

## 開發範圍與查證

- 一次完成一個可驗收的階段。改動前確認實際目錄、git 狀態、上游 SHA、launch、介面型別及現有測試。把查到的事實、設計提案及待驗證項目分開記錄。
- 優先重用既有驅動、控制器、模型推論核心、schema 驗證器及測試資料。整合層用 ROS 2 adapters、remaps 與 launch 編排。禁止順手改上游 repo、模型權重、下游介面或同事未提交的內容。需要改上游時先列出原因和涉及的套件，將改動限制在明確授權範圍。
- ROS 2 發行版、RMW、架構、容器基底與 Python／CUDA 版本以查證結果為準。鎖定上游 SHA、模型版本及映像。機器 IP、設備序號、TF 外參與場域參數使用配置，不提交權重、大型 rosbag 或機密。
- 先做小型 CPU 測試、回放或假硬體。真機模式須明確選擇和授權。禁止將本地路徑、臨時手把控制或 UI 的測試按鈕當成正式部署依賴。

## 運動控制

- VQA、重建、視角評估與手部感知只提供帶時間戳的資料或建議。任務管理器持有子任務 goal，底盤與手臂各自的本機閘門決定運動許可。每個實體控制端點只有一個最終寫入者，Nav2、ArUco 停靠及手把由同一仲裁入口管理，手臂軌跡由唯一執行鏈送出。
- 底盤移動前確認雙臂收納。左腕補拍及右臂擺放前確認底盤受抑制、已靜止，並持續監看。逾時、資料缺失、取消、重新啟動、狀態衝突或障礙可見性不足，依階段停機、保持或拒絕新命令。已排隊的舊軌跡不得自動復送。
- `NO_DETECT`、`OBSERVE`、`DATA_LOSS` 分別是有效空觀測、未能判斷、輸入或結果失效。舊 enum 經 adapter 明確映射並保留原值。`CONTINUE` 只屬 VQA 建議，無權釋放停機鎖。控制器受控停止與運動許可須有實測回饋，收到 action cancel 不等於停妥。
- 由獨立防護處理實體急停及真人共域需求。開發代理僅能在隔離的模擬／fake hardware 環境測試發命令。禁止為了通過測試縮小防護、加長逾時、停用 watchdog、假造 clear 或允許無回饋執行。真機安全設定須由負責人驗證及授權。

## 資料、感知與規劃

- 優先採標準 ROS 2 訊息與 action。資料必須交代擷取時間、座標、來源、有效期。TF 以觀測時間查詢，維持每條 TF 單一來源。區分 `map`、`odom`、`base_link`、貨架與 SLAM 子地圖，避免未校尺度點雲進入 MoveIt。
- 靜態貨架場景與即時動態障礙分開管理。禁止用靜態背景點雲替當前手部補深度。未知空間與過期障礙資料不能標記為已清空。擺放規劃綁定場景 revision、當前姿態與夾爪狀態。
- 保留純 RGB 研究路徑和資料來源。加入 RGB-D 或其他感測來源時使用獨立 profile 與測試紀錄。加入新 VQA prompt 時先離線比較與驗證，保留現有 checkpoint 的訓練相容 prompt 和受限制解碼路徑。

## 交付與測試

- Docker Compose 為開發、模擬與部署的標準入口。自 P1 起，每個新增的整合功能須在適用容器內建置與驗收，提交所需的 Dockerfile、Compose 或建置設定及重現指令。對照 [Docker 驗收表](docs/DOCKER.md)。
- CPU smoke／模擬與真機部署用不同服務設定。預設 Compose 入口不映射真機設備。依宿主架構與 JetPack 版本驗證映像，模型與硬體配置走明確掛載。
- 功能驗收需有指令、配置、SHA、實際輸出和重現方式。列出實際執行的測試及結果。環境缺少 ROS、模型、容器或真機時，清楚標示未執行的檢查與原因。
- 有狀態機、控制權或介面行為改動時，覆蓋相應的正常、失聯／過期、取消／重啟和控制衝突案例。區分靜態檢查、單元測試、假硬體、模擬閉環及隔離真機量測。
