# Isaac 接口契約：環境與 runtime 暫緩

本階段不安裝／啟動 Isaac，也沒有宣稱 Isaac bridge 已驗收。可讀配置在 [isaac.example.yaml](../config/interfaces/isaac.example.yaml)，`runtime_enabled: false`。目前使用真 RealSense／VQA 和 ROS 2 Nav2，底盤位移由隔離的 CPU kinematic simulator 提供，RViz 使用既有 6×4 m 地圖。

## 目前已接通

```text
RealSense RGB → VQA adapter ↔ GPU YOLO/Qwen worker
                     ↓ RiskAdvisory
RViz goal → mission_manager → Nav2 → /navigation/cmd_vel
                                      ↓
                              本機 base_gate
                                      ↓
                              /base/cmd_vel
                                      ↓
                                fake_tidybot
                                      ↓
                              /odom、/scan、TF
```

RViz 目標走 `/mission/goal_pose`，由任務管理器持有 NavigateToPose goal。VQA 是建議，不能自行建立目標或解除 HOLD。CONTINUE 在健康且有明確新 goal 時允許移動，DANGER_WARNING 依既有閘門降為半速；STOP／OBSERVE／DATA_LOSS／過期會拒絕許可，移動中則停機並取消舊導航。解除 HOLD 必須實測停妥、收納與新鮮有效觀測，再由操作人 reset 並發新 goal。

## 未來替換邊界

| 方向 | Topic／型別 | 契約 |
|---|---|---|
| 閘門 → Isaac adapter | `/base/cmd_vel` Twist、`/base/enable` Bool、`/base/control_frame` String | 唯一最終寫入者為本地 gate；保留全向 vx／vy／wz，不改成差速車 |
| adapter → Isaac | `/isaac/tidybot/cmd_vel`、enable、control_frame | 新鮮度／控制權不符即零速度；殘留 latched enable=true 不代表許可 |
| Isaac → adapter | `/isaac/tidybot/odom` Odometry | 公尺／秒、parent=odom、child=base_link，保留擷取時間與實測速度 |
| Isaac → Nav2 | `/isaac/tidybot/scan` LaserScan → `/scan` | 實際模擬 sensor，明示來源與可視範圍，不把感知失效當成清空 |
| Isaac → 手臂回饋（後續） | `/isaac/robot/joint_states` JointState | 名稱對應 OpenArm URDF；不能直接沿用原 USD 的 Kinova joints |

接 Isaac 時停用 fake_tidybot 的 odom／scan／TF，不能兩個來源同時發布。`odom→base_link` 只由選定 bridge 提供；手臂／相機裝配 TF 由 robot_state_publisher 提供；`map→odom` 只由選定定位或已對齊的 ground-truth 來源提供。當前簡單地圖與 Isaac 世界必須先確認尺度、原點、方向及障礙一致。

OpenArm 與底盤已組成同一份 URDF。提供的 USD 含 Kinova，所以未來應匯入合併後的 robot_description，或替換其手臂 articulation；不能將兩支不同手臂當成同一組關節控制。手臂仍經 MoveIt → arm gate → ros2_control；接 Isaac 的硬體／articulation adapter 必須支援取消與關節停止回饋，不能新增 UI／joint command 的第二個最終寫入者。

## 時間、重置與失聯

目前所有節點與 RealSense 使用 system time。若 Isaac 使用模擬 `/clock`，不能直接混用帶 wall-clock capture stamp 的真相機影像。未來 bridge 要選擇一致時間基準，或驗證保留原 capture stamp、對映 stamp、來源 epoch、誤差界限的時間轉換；不能簡單改成收到影像的 now。未完成時間對齊前維持 DATA_LOSS／禁動。

暫停 Isaac、重置 articulation、跳時、bridge 重啟或回饋逾時時，先撤回許可、零速度、取消目前 Nav2／手臂 goals。恢復後提供新的來源 epoch 與實際靜止／收納回饋；操作人 reset 後必須重新給目標，不重送之前排隊的路徑。

這份檔案是後續 bridge 的接口與驗收要求，現在沒有對外連接 Isaac 或實體機器人。
