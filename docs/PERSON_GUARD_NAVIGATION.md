# 大面積人體、STOP 與 Nav2 繞路的接法

本頁是下一個整合階段的設計，**80%／90% 人體比例閘門尚未接入目前 runtime**。目前已接通的是 VQA 建議→base_gate→fake_tidybot，以及任務管理器持有 Nav2 goal；不要把下面的提案當成已完成真機防護。

## 1. 先定義「佔滿畫面」

若用 YOLO，量到的是 **bounding-box area / image area**，不是人體實際像素面積，也不是公尺距離。先選「最大單人的框」，裁到畫面範圍後計算：

```text
ratio = max_person(clipped_bbox_width × clipped_bbox_height) / (image_width × image_height)
```

不要把重疊的多個框直接相加。若要真正的人體像素比例，需要分割模型與獨立測試。框接近邊界、只看見局部身體、遮擋或偵測漏失時，不代表人已遠離。

可在隔離模擬先驗證以下**可配置提案**：

| 最大單人框比例 | 保守行為 |
|---|---|
| ≥0.80、<0.90 | 近身警告／降速，必要時停下觀察；有效 3D 障礙已進 costmap 才談繞路 |
| ≥0.90 | 獨立 guard 撤回底盤運動許可、請求停止、維持 HOLD |
| 觀測過期／相機失效／來源重啟 | 未知，不標記 clear，拒絕許可或停止 |

這些比例不是驗證過的實體安全距離。鏡頭 FOV、安裝角度、速度、遮擋、detector 時效與煞停距離都會影響結果；更小的框也可能有近處手臂或其他危險，因此不能只靠大框才停。

## 2. 獨立 STOP guard，不竄改 VQA 回答

```text
當前 RGB → 有界、較快的人體 detector → ratio / capture stamp / source / valid_for
                                                ↓
                                 本地 person_guard → base_gate 禁動／HOLD
VQA → 原 RiskAdvisory ────────────────────────────↑
```

guard 只增加停止／拒絕條件，不把模型的 CONTINUE 改成別的模型答案，也不能釋放既有 HOLD。不要等較慢的文字生成完成才判斷大框。現有 worker 雖有 YOLO 證據，但結果目前隨 VQA 回傳，還不是獨立高頻人身防護鏈。

觸發後：撤回 enable、唯一出口送停止、任務管理器取消 Nav2 goal、用 odom 確認停妥。框變小、模型又說 CONTINUE，都不自動恢復。解除條件要另設滯回／持續新鮮觀測、停止回饋與明確 reset／新 goal；不重播舊路徑。

「立即」仍需量測端到端最壞延遲與停止距離，不是 callback 被呼叫就算停妥。這份設計不取代獨立實體防護。

## 3. Nav2 目前已會重規劃，但還不知道真實人體位置

已查證的現況：

- `retail_bringup/config/navigate.xml` 的 ComputePathToPose 在 PipelineSequence 中以 **1 Hz** 重算路徑。
- local_costmap 有 obstacle layer，輸入 `/scan`；現在這份 scan 來自 **fake_tidybot**，不是 RealSense 人體深度。
- global_costmap 目前只有 static_layer＋inflation_layer，還沒有即時人體 obstacle layer。
- RealSense launch 目前 `enable_depth=false`，維持純 RGB profile。

因此「VQA 偵測有人」或「框很大」不會自動在 map 裡產生人體障礙；每秒重算相同靜態場景，也不會突然知道要繞哪邊。

## 4. 真正繞路需要的後續接口

```text
當前人體 RGB 偵測
    + 同時間對齊深度 / 已驗證即時 3D 感知
    + CameraInfo / 擷取時 TF / 不確定性
                  ↓
帶 stamp 的 metric PointCloud2 / obstacle observations
                  ↓
local + global costmap 的動態 obstacle layer
                  ↓
Nav2 重規劃 → 新鮮可行路徑 → gate 仍允許時執行
```

可另建 RealSense **RGB-D profile** 做人框內的可靠深度／遮擋處理，與純 RGB 研究路徑分開。當前安裝 TF 仍需實測標定；不能只把 bbox 中心的一個背景深度當成整個人的距離，更不能拿舊的靜態 MASt3R 貨架點雲替當前的人補深度。

障礙資訊要包含範圍／來源／擷取時間，使用有效 TF 投影到 map／odom，並按機器人 footprint、人體運動與量測不確定性建立足夠保守的佔據區。未知與過期資料不當成 clear；Nav2 找不到有觀測支持的可行路徑時停著等待。

80% 警告時若 metric 障礙有效、仍有運動許可，可以讓正常 Nav2 costmap／planner 繞行。90% 已觸發 HOLD 時先確認停止，可以規劃候選路徑，但新路徑本身不會解除 HOLD；需確認新鮮觀測與恢復條件，再明確 reset／新 goal。

## 5. 下一階段如何驗收

先用 fake detector／隔離假硬體測比例邊界、裁框／重疊、偵測失聯、重啟、取消、控制衝突與停止回饋。再量測真相機 detector 延遲；最後才接獨立 RGB-D／metric costmap profile，驗證有路可繞、無路等待、觀測失效、地圖／TF 不一致與停機後不重播。此頁沒有啟動這些尚未完成的功能。
