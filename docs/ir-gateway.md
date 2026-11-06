# 红外网关

## 1. 两种模式

- `gateway.mode: mock` —— 不发任何网络请求，用于开发、单测、演示；`base_url` 为空串，探测 `reachable=true`、`simulated=true`。
- `gateway.mode: real` —— 直连 ESPHome 板子的 `web_server` REST API。
- 切换点只有配置一处；模式在装配期读取，改后必须重启。优先级 `settings.local.yaml` > `settings.yaml`，密钥只进本地覆盖层。
- 相关键：`gateway.ir.base_url`、`gateway.ir.username`、`gateway.ir.password`、`gateway.probe_timeout_seconds`（默认 3）。

### mock 的固定行为（「无需硬件跑通全流程」的依据）

| 调用 | mock 返回 |
| --- | --- |
| `start_learn` | 成功（记录会话，无硬件操作） |
| `stop_learn` | 成功（幂等） |
| `read_last_code` | 默认 `no_signal`（`captured=false`） |
| `read_last_code`（显式开启 mock 学码注入） | 返回**固定测试波形**，供学码全流程联调 |
| `replay` / `probe` | 回放成功（只记录波形）；探测 `reachable=true`、`simulated=true` |

- 注入通过**进程内函数**完成：`inject_test_waveform()` 写入固定波形、`inject_last_code(raw)` 写任意原文（`None` 恢复无信号）。只有显式调用才生效，**没有配置键、没有文件开关、也没有 HTTP 端点**——生产装配默认永远 `no_signal`。
- 固定波形只在显式开启注入时出现；默认 `no_signal`，避免把假数据写进真实设备。
- mock 不模拟鉴权失败与超时；这两类要靠单测注入替身。

## 2. Basic 鉴权

- `username.strip()` 与 `password` **两者都非空**才发 `Authorization`；缺任一项即不发头；密码不 `strip`（首尾空格可能是真实字符）。
- 401/403 → `DEVICE_UNAUTHORIZED`（**不是** `DEVICE_OFFLINE`），文案必须说清「连上了但被拒」。
- 鉴权值没有 API 写入路径：只能改 `settings.local.yaml` 并重启；探测与发送共用同一套凭证判定。

## 3. 真实协议端点

网关只承担三件事：开关学习模式、读最近波形、回放波形；没有任何按设备类型控制的内置端点。

| 用途 | 请求 |
| --- | --- |
| 学码开/关 | `POST /switch/ir_learn_mode/turn_on` / `turn_off` |
| 读最近波形 | `GET /text_sensor/ir_last_code` |
| 回放学码波形 | `POST /infrared/ir_proxy_tx/transmit`（**form body**） |

规则：

- 回放**固定走 `ir_proxy_tx`**，与设备 id、功能名都无关；载波恒 **38000 Hz**，本期不做配置项。
- 波形编码为 base64url 无 padding 的小端 int32 数组（正数 mark、负数 space，微秒），必须带 `carrier_frequency`；载荷走 form body，走 query 会因长度 414。
- 不存在 `switch/{entity}/turn_on` 或 `climate/{entity}/set` 这类按设备类型的控制路径；所有控制都是学码波形回放。

## 4. 学码时序

```
POST /api/devices {learn_draft:true} ──▶ 建空壳（失败不留行）
start ──▶ switch/ir_learn_mode/turn_on   （设备侧同时清空 ir_last_code）
         │
         ├─ 客户端每 1s 调 GET /api/learn/capture
         │    └─ 服务端读 GET /text_sensor/ir_last_code
         │        ├─ 空串       → captured=false, reason=no_signal
         │        ├─ 解析失败    → captured=false, reason=malformed
         │        └─ 解析成功    → captured=true, raw_code=[...]
         │
save ──▶ 落库 ir_codes（DB 为权威，每功能保留最近 3 条，按 `rowid` 倒序保留）→ 再 turn_off
cancel / 600s 超时 ──▶ 撤空壳 → turn_off
```

- 「保留最近 3 条」按 `ir_codes.rowid` 倒序（不按 `id` 字符串或 `learned_at`），删除更早的行；每次插入后在同一事务内裁剪。
- 撤壳双条件：仅当草稿标记仍在**且** `device_functions` / `ir_codes` / `device_states` 对该设备全为空时才删设备；`save` 成功后标记被清掉，不会再撤壳。
- 空闲 TTL 到期由服务端自生成 `trace_id`（原请求已断），审计 `who=system`、`action=learn.idle_timeout`；用户主动取消是 `learn.cancel`，两者不得混淆。

- 产品路径由客户端 1s 调 `capture`，服务端即时读传感器。
- 开学习模式会清空上次波形，`start` 后读到的必然是本次按键。
- `hardware/ir-codes/<device_id>.json` 只是历史归档；与库冲突时**以数据库为准**，文件不得反向覆盖库。
- `start` 不创建设备：草稿由 `POST /api/devices` 创建，`start` 只对已有设备开会话；捕获的 `raw_code` 原样存储，保存前只校验非空、可解析。

## 5. 双层超时

| 层 | 值 | 职责 | 缺了会怎样 |
| --- | --- | --- | --- |
| 客户端 | 10 秒无信号判失败 | 体验：用户立刻知道「没收到」 | 用户在设备前干等 |
| 服务端 | 600 秒空闲 TTL | 资源：关接收、撤草稿、写 `learn.idle_timeout` | 关标签页/断网后接收态与空壳永久残留 |

- 每次 `capture` 重置服务端 TTL；`save` / `cancel` 立即结束会话。两层都不能省：客户端管单次成败，服务端管设备侧不长期占用。
- 另有发送 10s、探测 3s，二者独立。
- 学码单次轮询与发送的 **10 秒超时是模块常量**（`gateway` 层写死），**不是配置键**，不随 `settings.yaml` 变化；只有服务端 600 秒空闲 TTL 可通过测试装配参数压缩（生产恒 600 秒）。

## 6. 单向语义的后果

- 红外只有发射、没有回执：HTTP 200 只代表「已发出」；回放后状态一律 `unknown`，不存在 `estimated`。
- 唯一能变成 `confirmed` 的路径是用户主动校正，网关与后端都不猜。
- 界面不得根据「刚执行过」把状态改成开/关；这是前端硬约束。

## 7. 探测与失败映射

- `GET /api/gateway` 探测 `base_url/`，超时 3s，只读；结果缓存 **15 秒**，`refresh=true` 绕过缓存。
- 探测缓存**只存在进程内存**（`GatewayProbe` 的锁 + 字典）：重启即清零，没有持久化、没有跨进程共享；`last_success_at` 同样只在进程内保留。
- `last_success_at` 只保留最近一次成功探测时间，失败不清空，用来区分「刚断」与「早就断」。
- 地址写入只发生 `PUT /api/gateway`：写本地覆盖层 + `config_audit`，返回 `restart_required`。
- `PUT` 之后 `GET /api/gateway` 立即回显**已保存的新地址**并给出 `pending_restart=true`（已保存、尚未生效）；重启后 `pending_restart=false`。不要让用户看到旧值而误判保存失败。
- **探测不是控制前置条件**：探测失败只影响状态展示；回放/学码时真正连接失败才返回 503。

| 现象 | 返回 | 下一步 |
| --- | --- | --- |
| 连接被拒 / 网络不通 | 503 `DEVICE_OFFLINE` | 查板子电源、是否同 Wi-Fi、地址 |
| 发送或读取超时 | 503 `DEVICE_OFFLINE`，文案带秒数 | 查网络与板子负载 |
| 板子在线但 401/403 | 401 `DEVICE_UNAUTHORIZED` | 核对 `gateway.ir.username / password` |
| 板子在线但 5xx | 503 `DEVICE_OFFLINE`，文案带 HTTP 状态 | 重启 ESPHome / 查固件 |
| 波形不规整 | 200 `captured=false, reason=malformed` | 让用户重按，不当作网关故障 |
| 学码轮询时网关连不上 | **200** `captured=false, reason=gateway_unreachable` | 会话保留，提示检查网关后可继续轮询；不升级为 503 |
| 没有活动学码会话时轮询 | 404 `NOT_FOUND` | 重新 `start`（会话已被 save/cancel/TTL 回收） |
| 空波形保存 | 400 `INVALID_REQUEST` | 重新捕获 |
| 关闭接收态失败 | 200 + `receiver_stopped=false` | 如实提示；已存码不回滚 |

只有鉴权失败有专属码，其余网关异常兜底 `DEVICE_OFFLINE`；文案必须带主机与原因。
