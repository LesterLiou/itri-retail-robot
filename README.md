# ITRI 便利商店機器人整合開發

這裡是整合層：TidyBot 移動載台、OpenArm 雙臂、三視角 VQA、胸口重建及左腕補拍。先建立可重現的 CPU 開發環境，再分階段加入功能。現在交付的是 **P0 開發容器**，尚未實作 P1 任務／假硬體閉環。

## 立即進入開發

在此資料夾的宿主機終端執行：

```bash
# 首次設定；已有 .env 時保留並檢查內容。
test -f .env || printf 'LOCAL_UID=%s\nLOCAL_GID=%s\n' "$(id -u)" "$(id -g)" > .env
docker compose build dev ros-smoke
docker compose run --rm dev
```

容器內工作目錄為 `/workspace/ros2_ws`；`/workspace` 對應目前資料夾，原始碼與編譯結果會保留。每次進入自動載入 Humble 與已存在的工作區 overlay。已安裝 MoveIt 2、Nav2、ros2_control、Cyclone DDS、colcon、vcstool、rosdep、pytest、OpenCV 及 C++ 編譯工具。模型、CUDA、相機 SDK 與 TidyBot 的 Phoenix runtime 需在相應部署映像另行處理。

```bash
# 宿主機：驗收環境
docker compose run --rm ros-smoke
docker compose run --rm dev-check

# 選用：保持開發容器，從其他終端進入同一 ROS graph
docker compose up -d dev
docker compose exec dev bash
docker compose stop dev
```

`dev` 有一般容器網路以取得依賴，ROS 限本機；`ros-smoke`／`dev-check` 禁用外部網路。預設不啟動真機、不映射設備或 Docker socket，也不使用 host network。RViz 套件已安裝，顯示轉送尚未配置；此入口先做無視窗開發與驗收。

## 接下來讀什麼

- [系統設計](docs/SYSTEM_DESIGN.md)：三機分工、流程、資料與控制權。
- [實際查證基線](docs/BASELINE.md)：本機環境、上游 SHA、程式接點、未驗證事項。
- [Docker 操作](docs/DOCKER.md)：建置、測試、權限及三機映像邊界。
- [來源與套件取得](docs/DEPENDENCIES.md)：哪些 repo 要抓、哪些套件要用。
- [Codex 任務卡](CODEX_PROMPTS.md) 與 [開發規範](AGENTS.md)。

建議採 **一個整合 repo + SHA 鎖定的外部 repo**。整合套件放 `ros2_ws/src`，上游放 `external`。本資料夾目前沒有可用的 Git repository；需在一般宿主機終端建立／連結整合 repo，保留現在的檔案。本次未執行 `git init`、commit 或推送。
