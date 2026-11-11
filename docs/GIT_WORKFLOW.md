# Git 提交與環境還原

Git 保存可重建系統的程式、設定範本與測試證據。Docker 容器、模型與現場設定不會隨 git push 上傳。以下命令都在宿主機專案根目錄執行。

## 本次哪些要提交

| 內容 | 處理方式 |
|---|---|
| `ros2_ws/src/retail_*` | 提交 ROS 套件、介面、測試、URDF／STL、launch、RViz 和地圖 |
| `docker/`、根 Compose、`Makefile`、`.dockerignore` | 提交容器建置與啟動設定 |
| `scripts/` | 提交建置、操作、資產複製／核對及驗收工具 |
| `config/vqa/`、`config/interfaces/`、`.env.example` | 提交 prompt、契約、資產 hash 和設定範本；不含本機序號或授權檔 |
| `vendor/lightweight_risk_vqa/` | 提交已複製的固定版本推論原始碼與隨附 THIRD_PARTY 文件；不是模型權重 |
| `dependencies.repos` | 提交上游 repo URL 與固定 SHA |
| `README.md`、`AGENTS.md`、`docs/` | 提交操作／設計文件及整理過的驗收 log、JSON、截圖；分享前確認影像適合公開 |
| `exports/store_corridor_nav2_map.zip` | 提交這份小型地圖交付包，README 有下載連結 |

本輪盤點可提交內容約 6.8 MiB，最大檔為約 3 MiB 的底盤 STL。這是工作樹檔案大小，不是 Git 傳輸壓縮後大小；後續修改可能改變數字。

## 哪些留在本機

- `.env`、`config/local/`：設備序號、DISPLAY 授權與機器差異；只提交範本。
- `models/`、`checkpoints/`、權重檔：用另外的資產儲存／複製流程傳遞，依 `config/vqa/assets.manifest.json` 核對。
- `data/`、影片與 rosbag：另存測資儲存空間。
- `external/`：依固定 SHA 重新取得；不複製整份上游 Git 歷史進整合 repo。
- colcon `build/ install/ log/`、Python cache、core dump、一般 runtime log。
- `.vscode/`：目前只有本機絕對路徑設定，不是共用開發依賴。
- 其他 `exports/` 輸出、SSH／私鑰檔。

`.gitignore` 不會自動移除已被 Git 追蹤的檔案。本輪確認沒有「已追蹤但符合忽略規則」的檔案，因此沒有執行 `git rm --cached`，也沒有刪除本機資產。

## 本次 commit／push 操作

這次整理沒有代為 stage、commit 或 push。先檢查；確認要把目前整合改動一起提交後再執行：

```bash
git status --short
git diff --stat
git diff --check

# 將目前所有未忽略的新增／修改／刪除加入暫存區。
# 若只想提交一部分，改用 git add <檔案或目錄>。
git add -A

# 檢查即將提交的檔案，包含新增檔。
git diff --cached --stat
git diff --cached --name-status
git diff --cached --check
# 詳讀內容：git diff --cached

git commit -m "Integrate Docker ROS2 navigation, VQA and RViz workflows"

# 先確認目前分支、remote 與遠端差異。
git branch --show-current
git remote -v
git fetch origin
git status -sb

# 推送目前分支，並建立 upstream 關係。
git push -u origin HEAD
```

若遠端已有新提交或 push 被拒絕，先檢查分歧並整合變更，不使用 force push 覆寫別人的提交。移出暫存區可用 `git restore --staged <path>`，不會刪除工作檔案。

## 換一台電腦後要恢復什麼

目前**沒有 submodule／`.gitmodules`**；上游採 `dependencies.repos` ＋取得腳本，`vendor/` 則是直接追蹤的固定程式副本。不要對 `external/` 使用 `git add -f`。

1. clone 此整合 repo，從 `.env.example` 複製 `.env`，依新主機設定 UID／GID。
2. 按 [Docker 教學](DOCKER_USAGE.md) 的來源取得與建置流程，恢復固定版本 `external/`。上游 OpenArm 來源是 sim 建置的必要輸入。
3. 額外傳遞完整 VQA bundle 到 `models/vqa/best/`、YOLO 到 `models/yolo/yolov8n.pt`；需要影片模式時還原 `data/video/`。只 clone 不會取得模型。
4. 執行 `python3 scripts/verify_vqa_assets.py`，依輸出補齊所需資產並確認 hash；模型／影片未還原時會缺檔，不能當成已可推論。
5. 用範本建立 `config/local/cameras.yaml`，填入新主機的相機設定；GUI 授權由 `make gui-setup` 在當前桌面重新產生，不搬舊 cookie。
6. `make build` 建置 CPU ROS 映像；需要 GPU VQA 時按 Docker 教學建置相應 worker。依需求 `make floorplan`／`make nav-vqa`。

宿主架構與 GPU runtime 不同時仍需重新驗證；Git 同步程式不代表 x86 映像可以直接用在 Thor。
