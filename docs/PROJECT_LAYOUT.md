# 實際目錄與 Docker 開發流程

使用方式與容器架構以 [README](../README.md) 為準。Docker 的正式定義集中在 `docker/`；根目錄只保留 Compose discovery 入口與 Makefile。歷史驗收紀錄仍使用當時的檔名，沒有改寫歷史 log／hash。

```text
obstacle_avoid/
  compose.yaml                 # include docker/compose.yaml，保留直接 docker compose 操作
  Makefile                     # make help；啟動、開發、相機、VQA
  .dockerignore                # 根 build context 的忽略規則
  .env.example                 # 本機配置範例；.env 不進 Git
  docker/
    Dockerfile                 # common / smoke / dev / sim
    Dockerfile.vqa             # GPU worker Python/Torch 環境
    compose.yaml               # 正式共用服務
    compose.dev.yaml           # 可寫 sim workspace 與 colcon overlay
    compose.vqa.yaml           # 真模型＋外部相機來源
    compose.vqa-build.yaml     # GPU 映像建置
    compose.realsense.yaml     # 相機 driver 與一次性 camera-probe
    compose.mast3r.yaml        # 選用 MASt3R，需實機驗證環境
    run_sim.bash                # ROS launch 與容器內桌面
    setup.bash / entrypoint.bash
    vqa_worker.py / mast3r_worker.py
    requirements-perception.txt / cyclonedds-local.xml
    dev_check.bash / ros_smoke.py
  dependencies.repos           # 四個上游的完整 SHA
  external/                    # 鎖定 checkout；忽略，不直接修改
  ros2_ws/src/
    retail_interfaces/         # 觀測／場景／互鎖訊息
    retail_core/               # adapters、任務、本地閘門與假硬體
    retail_bringup/            # launch、範本配置、地圖與 RViz
  ros2_ws/{build,install,log}/  # Docker 開發產物；忽略
  config/local/                # 相機序號等本機配置；忽略
  scripts/                     # 來源取得、CPU 驗收與唯讀 RealSense/VQA 檢查
  prompts/                     # 研究 prompt，與 checkpoint 相容 prompt 分開
  docs/validation/             # 日期化的實測紀錄與證據
  AGENTS.md / CODEX_PROMPTS.md  # 開發規範與任務卡
```

`sim` 與 `cameras` 共用 humble-sim image，但運行在不同容器。只有 cameras 取得指定相機設備；它共用 sim 的 network namespace，以 localhost DDS 交付影像。`vqa-worker` 使用自己的 GPU image，透過 Compose DNS／HTTP 供 ROS adapter 呼叫。桌面在 sim，瀏覽器連 localhost:6080。

開發 `make dev`／`make vqa-dev` 將 `/workspace` 改為可寫，使用映像的 `/opt/retail_ws/install` 為 underlay，並載入 `/workspace/ros2_ws/install` overlay。編譯仍在容器中執行，無須宿主 ROS。標準使用 `make up`／`make vqa` 不載入開發 overlay，工作區唯讀。

`make shell` 只進入當前模式。純模擬編譯後用 `make dev-restart`；真相機開發編譯後用 `make vqa-dev` 重新啟動相機與 sim，避免相機停留在已替換的網路 namespace。改依賴或上游後重建映像。

套用 overlay 時以 Makefile 或 `docker compose --project-directory . -f docker/compose.yaml -f docker/compose.<mode>.yaml ...` 為準；不要將根目錄的 include 入口當作 override 鏈的第一份檔案。`docker compose exec sim bash`／`docker compose logs sim` 則繼續可用。

目前三個整合套件保留在同一 repo；系統設計中的六功能套件與三機部署拆分仍是後續提案，不放佔位空殼。模型、影片、編譯產物、快取與 core dump 不進 Git。既有修改、權重、上游和驗收紀錄均保留，未執行 Docker 全域 prune。
