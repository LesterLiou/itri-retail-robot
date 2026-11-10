# Codex 開發提示詞

把 `AGENTS.md` 放在整合 repo 根目錄，把設計書與 VQA 提示詞放到相應子目錄。Docker 定義集中於 `docker/`；根目錄保留 include 入口 `compose.yaml`、`Makefile` 與 `.dockerignore`。overlay 操作以 README／Makefile 的 `--project-directory . -f docker/compose.yaml` 鏈為準。每次給 Codex 一張任務卡即可。先執行 P0，依已驗證的基線調整其餘任務卡，再分開實作。Docker 交付與驗收見 [docs/DOCKER.md](docs/DOCKER.md)。

## 目前整合狀態與下一步

已實作 Docker ROS 整合 graph、真實 VQA／MediaPipe adapters、Nav2／MoveIt fake 任務、本地閘門與 RViz；先讀 README 及 `docs/validation/2026-11-10-integration.md` 再開工。以下 P0／P1a 卡保留為歷史階段範本，不能重新假定目前沒有程式。

下一張任務先選一個可驗收範圍：實體 RealSense 只讀擷取與標定；ArUco 假場景 lease 交接；或取得固定 SHA／模型的 MASt3R runtime 後驗證尺度與配準。保留已通過的 CPU 完整任務和故障測試；不得為讓實體資料通過而改 fixture 標籤、放寬時效、跳過場景有效性或沿用假的 TF／收納姿態。需要模型 runtime 或標定時列明輸入資產，無資產就維持 invalid。

## 歷史 P1a 任務卡

已建立 P0 CPU 開發容器及 `docs/BASELINE.md`。既有 P0 卡保留為重新盤點範本；現在先使用下列 P1a，不要把未連線的 NUC／Thor 資訊當成已確認。

```text
先讀 AGENTS.md、docs/SYSTEM_DESIGN.md、docs/BASELINE.md、docs/DEPENDENCIES.md、docs/DOCKER.md 與最新驗收紀錄。本輪只做 P1a：在既有 dev 映像完成 OpenArm 選定套件的乾淨建置，以及強制 fake hardware 的無視窗 bringup；先不加入 Nav2、VQA、MASt3R 或真機。

用 dependencies.repos 的固定 SHA 取得來源，放 external，不修改上游。只組裝 openarm_can、openarm_description、openarm_hardware、openarm_bringup、openarm_bimanual_moveit_config；先檢查 package.xml 與 CMake 相依。不得執行寫死 /root/openarm 且清除產物的上游 colcon_build.sh。需要額外 apt 套件時加入 Dockerfile 並重建，不安裝到宿主機。

新增零設備映射、無 host network 的 Compose sim 服務，及整合層 fake launch。對 use_fake_hardware 強制 true，控制器設定取自核對過的上游，禁止加入可切換真機的開關。依狀態回饋等待 controller 就緒，驗證左右 FollowJointTrajectory、joint states 與夾爪 action 是否真的可用。若夾爪不在此 bringup 中，明確列出並在整合 launch 正確組裝。

僅在隔離 fake graph 送小型軌跡，驗證正常完成、取消、節點重啟與重複 goal 的結果；cancel 回應與停止回饋分開記錄。這不是實體停止性能驗證。保留 ros-smoke 與 dev-check，最後回報容器 build／測試指令、實際輸出、來源 SHA、映像 ID、失敗項及後續 P1b 任務互鎖的接口。不要宣稱 P1a 已等同完整任務閉環。
```

## 第一次開啟 Codex，貼這段（P0）

```text
你在 ITRI 便利商店機器人的 ROS 2 整合 repo。請先讀根目錄 AGENTS.md、docs/SYSTEM_DESIGN.md 與 docs/DOCKER.md。這一輪只做 P0 基線盤點，唯一 repo 內交付檔是 docs/BASELINE.md。

先檢查 git 狀態、現有目錄與本地 checkout。對照下列上游，記錄實際讀到的 SHA。若需取得上游，放到工作區外的暫存位置，保留既有 checkout 與未提交變更。
1. https://github.com/hrc-pme/openarm_dev
2. https://github.com/Laiting20021202/social-safety-decision
3. https://github.com/LesterLiou/VLMotion_VQA/tree/LesterVQA
4. https://github.com/Li-ChengWang/tidybot_ros2_control

請沿著 README 查進 launch、package.xml、Python/C++ 入口與 controller 設定，輸出兩張表。
- 套件表需包含上游 SHA、套件位置、啟動檔、ROS／OS／Python／CUDA／CPU 架構、依賴、執行主機、模型或感測器需求。
- 介面表需包含 topic/action/service、完整型別、方向、單位、frame、時間戳、QoS、既有擁有者、超時及取消行為。附上相應的檔案與行號。文件與程式相異時列兩者並指出需 runtime 查證的部分。

重點分別查證 OpenArm fake hardware 與雙臂 trajectory action、TidyBot mux 與底盤控制及 ArUco 停靠、整合 VQA 的 headless 入口與 ROS 影像缺口、避障手部觀測與 Gazebo／Isaac 驗收邊界。TidyBot 尤其要核對 enable、control_frame、cmd_vel、odom 與手把。VQA 要核對輸出 enum 與三視角狀態。盤點四台相機與實際 MASt3R-SLAM wrapper、尺度對齊、標定及 rosbag。找不到時標待驗證並列出需補的檔案或資訊。

提出一個可驗證的 ROS 2 發行版建議。比較 Humble 路徑是否能跑 TidyBot 與 Thor adapter，以及 Jazzy 路徑對 OpenArm／避障的移植成本。將可供 P1 使用的假硬體與真機條件分開。P1 僅先做固定目標的流程。禁止啟動真機、送運動命令、變更控制器與安裝主機系統依賴。最後回報寫入的檔案、查證範圍、阻塞項與建議的第一個 P1 改動。

Docker 必須納入盤點。記錄各主機的架構、Docker Engine、Compose V2 及 Thor 的 JetPack、Jetson Linux 與 GPU runtime。若本機有 Docker，依 docs/DOCKER.md 建置並執行隔離的 ros-smoke。記錄實際指令、輸出、狀態碼與缺少的依賴，無法執行時標明原因。
```

## P1 假硬體任務閉環

```text
讀 AGENTS.md、docs/SYSTEM_DESIGN.md、docs/BASELINE.md。以 P0 核對過的發行版與接口實作這條最小流程。固定停靠目標 → 假底盤回報停妥與運動抑制 → OpenArm fake hardware 規劃已知貨架格位的擺放 → 退回並解除任務。底盤可以先用可回報 cancel 的 mock action，OpenArm 優先沿用上游 fake hardware 與標準 action。按查證的來源建立 dependencies.repos，限定 build 的套件，避免匯入重複 package。僅在有兩個消費端且標準型別不夠時新增自訂訊息。

交付一個 fake launch、可重跑測試，以及能從乾淨 checkout 建置的 Docker Compose sim 服務。保留獨立 ros-smoke。用 Docker 建置 ROS 套件並在容器內執行完整的假硬體流程與回歸測試。底盤與手臂互斥、預設禁動，收到停止要求後驗證回饋，拒絕重複控制來源。插入 stale odom／arm state、取消、重啟、鎖定消失、重送舊 goal 時維持停止或停在 HOLD／FAULT。模擬 graph 與真機隔離。回報改動、Compose 重現指令、測試結果和映像版本。
```

## P2a TidyBot、Nav2 與停靠

```text
以 P1 假硬體測試為基線。接 TidyBot 既有底層介面與 ArUco docking，為 Nav2、手把和停靠配置獨立的速度輸入與單一最終控制出口，保留全向 vy，核對 Twist/TwistStamped、速度 frame、enable、里程計和 TF 來源。導航抵達後取消導航、確認靜止、再把控制權切給停靠。先在 dry-run／隔離模擬驗證中斷手把、停靠 marker 丟失、導航取消、斷流、重複 publisher、阻路和重新規劃。保持原本的真機啟動入口停用。語言提示點到導航目標是獨立 adapter，先以有標定的地面或已知貨架幾何驗證固定測資，不擴張底盤驅動範圍。
```

## P2b VQA 即時資料接入

```text
在已鎖定的 LesterVQA 版本重用既有模型推論核心、權重、prompt 與 schema 驗證。加入 ROS Image adapter，先讓胸口回放可用，再接三個視角的各自時間序列。暫時只發布 shadow advisory，保存 model_id、prompt_id、各視角資料時間、raw action、normalized action、健康狀態及證據。融合在模型外做。未知 enum、舊資料、錯亂時間和空字串一律不得映成 CONTINUE 或 NO_DETECT。以錄包測胸口單流、肩部丟流、遮擋、移動相機、資料過期和單一視角出現危險。若評估新 prompt，使用 prompts/vqa_nav_advisory_v1.md 做獨立對照，不取代已知相容入口，回報配對測資、結果及延遲。
```

## P3a 胸口三維重建與格位

```text
只接胸口相機、已知貨架標記／模型與實驗室 MASt3R-SLAM wrapper。使用小型 rosbag 重播，保留擷取時間與追蹤品質，檢查尺度、獨立對齊誤差、ROI 覆蓋及 shelf_frame。生成有 revision 的規劃場景，將已知格位轉成 pre-place／place／retract。保持已知貨架模型作對照。故意提供錯誤尺度、失效 TF 或過期場景，規劃須被阻擋。交付重現方式、誤差與規劃結果。這一輪先不加入腕部候選視角或動態障礙。
```

## P3b 左腕補視角

```text
沿用 P3a 的貨架 frame 與 ROI。把遮蔽、缺少觀測、點密度不足和已觀測佔用分開。從 2–3 個已驗證觀測姿勢挑選候選，先篩可達性及碰撞，再以覆蓋收益與移動成本排序。使用擷取時的腕部 TF、手眼標定和可對應的貨架特徵對齊。只在尺度、重疊、誤差檢核通過後更新 scene revision。比較同一 ROI 補拍前後覆蓋、重疊與配準誤差。模擬耗盡候選、配準失敗及左臂未收回，這些情況不得開始擺放。
```

## P4 手部介入與恢復

```text
只在 mock／Gazebo／Isaac 的隔離環境整合手部介入。先重用 social-safety-decision 的觀測或回放，分開管理靜態貨架與有時效的動態障礙。2D 手部訊號或深度未知時使用保守暫停。不得查歷史貨架點雲來替手部補深度。暫停先收回運動許可並請求停止，再以控制器和關節回饋確認停妥。只有新資料支持清空且操作人確認後，才以新的 scene revision 和目前關節狀態重規劃。使用假手場景驗證進入、短暫漏檢、資料過期、TF 遺失、停止未完成、斷線重啟及無路徑。回報端到端停止時間的測量邊界、實際測到的數值與限制。
```
