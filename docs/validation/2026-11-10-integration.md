# Docker ROS 2 整合驗收：2026-11-10

本次在 Ubuntu 22.04 x86_64 宿主機、Docker Engine 29.1.3／Compose 2.39.2 驗收。GPU 為 RTX 5070、驅動 580.178.04。控制命令只送入無設備映射、loopback DDS 的 fake graph；沒有啟動實體底盤或手臂。

## 來源與映像

起始整合 HEAD：`d01fae544ae0961335484d71a8d47d9f3b5a8a54`；本次改動保留在工作樹，未 commit／push。起始已有文件與驗收輸出修改，未 reset。四個上游實際取得並保持乾淨：

| 來源 | SHA |
|---|---|
| OpenArm | `3eaa3361d84866394ff5fa9865514aed80af4833` |
| social-safety-decision | `783b5d986487cd66b3e6f333875c631bbe0f9b64` |
| VLMotion_VQA | `889558ff0ff5f43c818ce82aa39b7b95734e7e6e` |
| TidyBot | `195a46e5770e56ac35fb7fa437bb7fbad7d434b1` |

採 `.repos` 鎖版本，helper shallow／partial fetch，OpenArm sparse checkout；未加 submodule，未修改上游。Docker 內建置 9 個選定套件：五個 OpenArm、TidyBot、`retail_interfaces`、`retail_core`、`retail_bringup`。

| 映像 | 實際 image ID，均 amd64 |
|---|---|
| `itri-retail-robot:humble-dev` | `sha256:6de8f825793b67096af6b6f51548154859931749df1c24a23b9b964dbc3d8e80` |
| `itri-retail-robot:humble-sim` | `sha256:8dbe06dc3242c56f5f6957bde6999f87430631e5f6f7529374ab1ea1cb577abb` |
| `itri-retail-robot:vqa-worker` | `sha256:5dd032c358dd4dc253c98e4d5763c876f25ea17f97ce55b3fd8cd253607b3672` |

ROS 基底仍以 Dockerfile 的官方 manifest digest 鎖定。apt 未使用 snapshot；這些本機 image ID 不宣稱未來 apt 重建位元相同，也不是 registry release digest。

本輪交付原始碼／建置輸入 hash 見 [source.sha256](2026-11-10-integration-source.sha256)，CPU 相依與套件實際版本見 [versions.log](2026-11-10-integration-versions.log)。RealSense wrapper 為 4.58.4、librealsense 為 2.58.4，均為本次 apt 建置取得的版本。

CPU 使用 ROS Humble／Python 3.10、NumPy 1.26.4、MediaPipe 0.10.21、OpenCV 4.11。GPU worker 實測 Python 3.12.3、PyTorch 2.10.0+cu128、torchvision 0.25.0+cu128、Transformers 5.15.0、OpenCV 4.11。worker 與 ROS adapter 以有界 HTTP 傳遞影像／結果，沒有把不同 Python 的 rclpy 混用。

本次 GPU 建置明確指定既有 `sam3-native-foundation-vlm:dev` 作基底（ID `8cf1bf608169a81ddb87f2125c5edfc991f44c81d25362fe0ce3b216550647d8`）：

```bash
VQA_WORKER_IMAGE=itri-retail-robot:vqa-worker \
VQA_BASE_IMAGE=sam3-native-foundation-vlm:dev \
docker compose -f compose.yaml -f compose.vqa-build.yaml build vqa-worker
```

建置成功，執行帳號為 UID/GID 1001/1001。Dockerfile 同時提供以本專案 dev 為預設基底的下載路徑；本輪沒有另外從該基底重下載並驗收第二份 GPU 映像。Thor／ARM64 的 GPU 基底仍待實機版本確認。

## 控制與整合測試

最終重現入口：

```bash
bash scripts/verify_integration.bash /tmp/retail-final-checks
```

此腳本強制 fixture 輸入，分段重建隔離 sim，最後留下 `IDLE`／禁動的新 graph；不接真機設備。原始彙總輸出見 [integration-verify.log](2026-11-10-integration-verify.log)。

| 實際測試 | 結果／證據 |
|---|---|
| 政策與 ROS 回饋邊界、上游 docking core | 45 passed：12 個整合案例、33 個上游案例。包含 STOP 鎖定、過期／缺視角、來源重啟、時間反轉、控制衝突、未知 enum、互鎖、無效／缺／NaN 關節資料，以及取消後延遲抵達的 goal acceptance。 |
| 完整任務 | Nav2 規劃與 controller 驅動假底盤；固定姿態停靠後鎖底盤；掃描、候選視角碰撞檢查／FK、左臂執行與補拍 fixture、右臂 MoveIt 動作、夾爪釋放、雙臂收回；到 `DONE` 且底盤禁動。 |
| MoveIt 場景 | 以 GetPlanningScene 確認貨架背板／三層碰撞物和 `placed_snack` 存在；`known_snack` 已從附著物移除。場景更新使用 ApplyPlanningScene 確認，不只發送 topic 就假定生效。 |
| 未授權手臂 | 公開 FollowJointTrajectory gateway 拒絕 goal；MoveIt／RViz 無法繞過該 gateway 的運動許可。 |
| 導航中 STOP | 假底盤停止，移除 fixture 障礙後仍 HOLD／禁動；需明確 reset 和新目標。 |
| 重複底盤寫入者 | 第二個 `/base/cmd_vel` publisher 觸發鎖定 HOLD。 |
| 手臂中途取消 | 在左臂實際離開收納姿態時取消；獨立比較關節樣本確認保持停止；右臂排隊擺放未執行；非收納狀態 reset 被拒絕。 |
| 相機斷流 | 暫停合成相機 process，失去觀測後停止；恢復 process 不能自動重送導航。 |
| 缺少公尺場景 | external backend 沒有 validated metric reconstruction，擺放在授權手臂前被拒絕。 |
| 重啟 | 新 graph 為 IDLE，實測收納／靜止，disabled，沒有 goal replay。此結果只適用 fake hardware，不把重啟視為真機回到收納。 |

放置流程目前用固定關節目標驗證執行鏈，已知商品／貨架與補拍資料是 fixture。表面覆蓋以已知貨架格網統計，未知區不標自由空間。這不是商品辨識、任意格位 Cartesian 擺放、完整 NBV 或實體放置成功量測。

## 真實 VQA／手部 ROS 回放

```bash
docker compose up -d vqa-worker
docker compose run --rm --no-deps sim bash /workspace/scripts/accept_vqa.bash
```

使用 `/home/lester/VLMotion` 原有 `c_policy/best` 與影片，只讀掛載；未更換權重、prompt 或上游 constrained decoder。ROS Image 經新 adapter 到 GPU，回覆再經原 schema 驗證，保留擷取時間、raw action／raw response、模型／prompt／worker 身分；空字串、schema 錯誤、逾時與來源失效不補成 CONTINUE／NO_DETECT。

- `model.pt` SHA-256：`30a2397b628c81721afde15c690122887b5a9709a9fa1bac0f7bb73f7403e619`。
- ROBOT_QUERY SHA-256：`d02131eab27f7639a650c3bcca26baae02ad80fd3d0f3558f8ed14e2ce070f21`。
- 已收到非 synthetic 的有效 `STOP`／`CONTINUE`、原始 JSON，並收到非 synthetic 的有效 MediaPipe 空手觀測。原始輸出見 [live-vqa.log](2026-11-10-integration-live-vqa.log)。
- GPU 紀錄中可見首個採樣約 1.36–1.48 s、後續採樣約 0.73–0.78 s；這只是少量樣本，不是 P95／停止性能驗收。也有原 schema 拒絕的 DATA_LOSS，未將其修補成有效回答。
- 有一次 STOP 回覆的說明文字與所選風險語意不一致，即使通過既有結構檢查；模型準確率與證據一致性仍須離線評估，不能把本次接口測試當安全辨識驗收。
- MediaPipe 沿用上游 RGB hand detector（full packaged model）；沒有估人手 3D，也沒有從舊貨架點雲補深度。空手影片只驗證 detector 可執行，不代表召回率已測量。

影像來源 `synthetic`／`video`／`external` 寫入 source_id；fixture 或合成影像即使經模型推論仍標為 synthetic。三視角有獨立狀態，但本次實際模型只驗證胸口；三路真相機、吞吐與側視角語意尚未量測。

## RViz2 與相機入口

RViz2 實際在 Xvfb／Mesa 軟體渲染下啟動，noVNC 的 `/vnc.html` 回應 HTTP 200。已顯示 robot、地圖、路徑、貨架點雲、RGB、決策與任務 marker。[RViz 截圖](2026-11-10-rviz-vqa.png) 保留當次畫面，圖中失效標籤也如實反映資料健康。

RealSense ROS driver／SDK 已安裝，`rs_launch.py --show-args` 通過，額外硬體 Compose 可解析；相機型號／序號／UVC 節點與多相機頻寬尚未驗收。本主機未列出 `/dev/video*`，本輪沒有啟動實體相機。相機 profile 沒有 CAN 或馬達程序。

## 未執行與剩餘限制

1. MASt3R 安裝環境／checkpoint／獨立尺度與配準測資尚未提供；本輪只建 ROS／worker transport，不執行上游自動下載或相容 patch。raw cloud 維持 `metric_valid=false`，沒有可直接放行的學習重建場景。
2. 左腕真實手眼標定、主／腕影像對齊與融合、未知場景遮擋／自由空間評估尚未完成。候選姿勢庫與 fixture 覆蓋不冒充上述研究結果。
3. 上游 ArUco／docking 套件已編譯、docking core 測試通過，整合 lease 接點已提供；真 marker／教導目標／相機外參與視覺停靠閉環未驗收。校正預設 false。
4. 真底盤／雙臂、Thor、跨機 DDS／時鐘、實體 stop distance／latency、真人共域與故障復歸未驗收。預設 Compose 不提供真機運動開關。
5. 上游 URDF 的手指慣量會引發 RViz 顯示警告；未修改上游慣量。CPU software renderer 曾輸出 map shader 警告，但地圖／影像實際可見；這不等於動力學模擬驗收。

未為測試延長 watchdog／有效期、縮小保護、停用互鎖或假造 clear。剩餘工作應按 [CODEX_PROMPTS](../../CODEX_PROMPTS.md) 選一個資產齊備的階段繼續。
