# 設計圖、LiDAR 掃圖與 Nav2 地圖使用

可以先畫有尺寸的設計圖，再轉成 Nav2 的靜態 occupancy map，不一定要先掃圖。**地圖與定位是兩件事**：設計圖描述障礙在哪裡；真機仍需知道自己在圖上的位置，並感知臨時障礙。[Nav2 定位／建圖說明](https://docs.nav2.org/rolling/configuration_and_development/first_time_robot_setup_guide/sensors/mapping_localization/)

## 1. 這張圖已如何轉換

按你標示的長度，而不是截圖像素比例，採用以下解讀：

| 部位 | 長度 |
|---|---|
| 室內寬度 | 6.60 m |
| 室內高度 | 1.40＋1.60＋1.40＝4.40 m |
| 中央黑色障礙 | 5.00 × 1.60 m，接到右牆 |
| 左側通道 | 1.60 m |
| 上／下通道 | 各 1.40 m |

生成檔案在 `ros2_ws/src/retail_bringup/maps/`：

- [store_corridor.pgm](../ros2_ws/src/retail_bringup/maps/store_corridor.pgm)：乾淨的黑白佔據圖，沒有尺寸文字／虛線。
- [store_corridor.yaml](../ros2_ws/src/retail_bringup/maps/store_corridor.yaml)：Nav2 map_server 讀取的設定。
- [store_corridor.geometry.yaml](../ros2_ws/src/retail_bringup/maps/store_corridor.geometry.yaml)：相同公尺幾何與 fake 初始姿態，供模擬雷射使用。
- [標註預覽](../ros2_ws/src/retail_bringup/maps/store_corridor.preview.png)：方便看尺寸與示範目標；不拿這張有字的圖當 occupancy。
- [SVG 預覽](../ros2_ws/src/retail_bringup/maps/store_corridor.svg)。

這是由設計圖建立的 draft，`calibrated: false`，不是已實測地圖。若圖上 160 cm 指的是其他尺寸，需先修改 geometry 再重新產生。

## 2. Nav2 最少要哪兩個檔案？

一般靜態地圖是 **影像＋YAML**，兩者放一起。PGM 是常見格式；影像與 resolution／origin 要一致。官方範例也是同樣結構：[Nav2 範例](https://github.com/ros-navigation/navigation2/blob/main/nav2_bringup/maps/depot.yaml)。

```yaml
image: store_corridor.pgm
mode: trinary
resolution: 0.05
origin: [-0.05, -0.05, 0.0]
negate: 0
occupied_thresh: 0.65
free_thresh: 0.25
```

- `resolution: 0.05`：一格 5 cm。
- `origin`：整張 raster 左下角的公尺座標與 yaw，不是機器人初始位置。
- 白色為這份設計圖宣告的 free、黑色為 occupied；灰色可表 unknown。
- 本圖額外加一圈 5 cm raster 牆邊，放在室內範圍之外，所以影像是 134×90 格、含邊界總覆蓋 6.70×4.50 m；**室內尺寸仍是 6.60×4.40 m**。這不是量到的實際牆厚。

不能直接把帶文字／尺寸線的截圖交給 map_server，否則字也可能被當成障礙。map_server 以 `yaml_filename` 載入設定並發布 `/map`。[官方參數](https://docs.nav2.org/rolling/configuration_and_development/configuration_guide/core_servers/map_server/configuring_map_server/)

## 3. 本專案直接使用你的地圖

宿主機（先取消當前任務，確認停止，再切換）：

```bash
cd /home/lester/obstacle_avoid
make stop
make floorplan
```

這是 **CPU 合成感知＋假底盤**，不開真相機／模型。RViz 載入新圖，假雷射也使用相同幾何，假初始位置為 **(0.8,0.7,0)**。

如果要搭配真相機 VQA：

```bash
make stop
make floorplan-vqa
```

仍只移動假機器人。相機是即時影像，不表示圖上的座標已和真房間／機器人對齊。

### 座標怎麼選

以室內左下角為 `(0,0)`，x 往右、y 往上，單位公尺：

| 位置 | x | y |
|---|---:|---:|
| 假起點 | 0.8 | 0.7 |
| A，下方通道 | 5.6 | 0.7 |
| B，上方通道 | 5.6 | 3.7 |
| C，左上方 | 0.8 | 3.7 |

在 RViz 用 Retail Task Controls 的 Set goal 或上方 2D Goal Pose 選目標；不動時看 Task／Gate 原因。CPU 模式也可宿主 `make shell`，容器內：

```bash
ros2 run retail_core demo_control goto --x 5.6 --y 3.7 --yaw 0 --wait
ros2 run retail_core demo_control status
```

從下方到上方須經左側通道，不能穿過黑牆。白色格子也不代表每個點都可作為機器人中心；要容納完整 footprint 與避障餘量。目前 demo footprint 是保守的 0.8×0.9 m，實機需量測雙臂收納／載物時的外廓，不能當成已校正尺寸。

本 profile 是 **2D 導航**：不沿用舊的示範貨架 3D 場景，`scene_backend=external`，因此不把 2D 設計圖當成已驗證的 MoveIt 操作場景。不要在此用原本固定貨架 `run/place` 任務；練手臂仍切回 `make moveit`。

根 `docker compose exec sim bash`／`make shell` 都是進當前 profile。不要用不帶 floorplan overlay 的 up 覆蓋設定；可重複 `make floorplan`／`make floorplan-vqa`。GUI 視角中心會配合此地圖，Top 按鈕也讀 `/map` 的中心。

## 4. 真機還需要提供什麼？

| 資料 | 目的 |
|---|---|
| PGM／PNG＋map YAML | 靜態障礙與尺度 |
| 起始位置／方向 | 地圖上從哪裡開始；實際定位的初始猜測 |
| `/odom` 與 `odom→base_link` | 底盤運動回饋，不是只送速度 |
| 定位系統發布 `map→odom` | 將機器人對齊地圖；每條 TF 只有一個來源 |
| 即時 `/scan` 或有效 metric PointCloud2 | 看臨時障礙／人；設計圖本身沒有這些變化 |
| 感測器安裝 TF、時間戳與標定 | 感測點能正確投影到 robot／map |
| 實測 footprint／限速／停止回饋 | 讓機器人實際外廓能安全走過通道 |

目前 fake profile 的 map→odom 是模擬真值對齊，不是已做 AMCL。真機不能保留 fake_base 再同時發布真 odom／scan／TF。

### 有 LiDAR：常見流程

1. 驅動提供 `sensor_msgs/LaserScan`，確認 `/scan`、odom 與 sensor TF 正確。
2. 用 SLAM Toolbox 掃場地，產生 `/map` 和 map→odom。
3. 存 occupancy map，例如 `ros2 run nav2_map_server map_saver_cli -f /your/maps/store`，取得影像＋YAML。
4. 固定地圖使用時，由 map_server 載入，搭配 AMCL 定位；不要讓 SLAM 與 AMCL 同時寫同一條 map→odom。

這是後續真機整合方式，本輪沒有啟動 LiDAR／SLAM／AMCL 或馬達。Nav2 對這兩種方案的定位／建圖接口有 [官方說明](https://docs.nav2.org/rolling/configuration_and_development/first_time_robot_setup_guide/sensors/mapping_localization/)。

若你已用其他電腦掃好圖，先給影像＋YAML 就能看靜態底圖。想繼續 SLAM Toolbox 原有圖優化，另保留它的序列化 pose graph；那不是 AMCL 只讀 occupancy map 的必要檔案。若要我檢查原始掃圖品質，再提供包含 scan／odom／TF 的 rosbag 及相關設定。

### 沒有 LiDAR

手工設計圖仍可作規劃底圖。定位可另用已驗證的視覺／RGB-D SLAM、標記定位或其他來源，但它們必須提供可對齊此圖的公尺座標與 TF；只有單張 RGB／VQA 回答不會自動得到 robot 在圖上的位置。RealSense depth 也需獨立 RGB-D profile、校正與驗證，不能直接等同一份正確的 `/scan`。

## 5. 我建議你的使用順序

先用這份尺寸圖在 Docker 把 RViz 操作／Nav2 路線跑通；實機階段再用 LiDAR 掃出的實際地圖比對、校正尺寸與牆面，接入可靠定位和即時障礙。設計圖上的自由空間不是現場已觀測清空；機器人不能只靠一張圖和 VQA 就自動知道能走到哪裡。

修改尺寸時編輯 geometry，宿主執行：

```bash
make floorplan-generate
make build
# 取消任務、確認停止後
make floorplan
```

Map／scan 不一致會被自訂假世界 launch 拒絕。新真機地圖不能套用這個 fake geometry 限制當成完成部署，仍需獨立硬體 profile。

本輪已實測 CPU 假導航繞過中央障礙、完整外廓檢查與停妥；33 個單元測試及原地圖取消／重啟回歸通過。[驗收紀錄與重現指令](validation/2026-11-11-floorplan.md)。真相機 VQA 的新地圖組合已提供入口，本輪未另外做這個組合的端到端驗收。
