# 领域与数据

## 1. 目标 schema（重建版）

重建版共 12 张表，`SCHEMA_VERSION = 3`：v1 七张业务表保持不变，v2 新增 Agent 场景与命令记录，v3 把场景升级为动作组 + 目标设备 + 触发器。没有内置功能，也没有能力、参数或策略表。

| 表 | 列 |
| --- | --- |
| `rooms` | `name`(PK)、`display_name`、`sort_order`、`created_at` |
| `devices` | `id`(PK)、`name`、`adapter` CHECK(`ir`)、`room`(FK→rooms.name)、`notes`、`extra` JSON 默认 `'{}'`、`created_at`、`updated_at` |
| `device_functions` | `id`(PK)、`device_id`(FK→devices，级联删)、`name`、`created_at`、`updated_at` |
| `ir_codes` | `id`、`device_id`(FK)、`function_id`(FK)、`raw_code` JSON 非空、`learned_at`、`created_at` |
| `device_states` | `id`、`device_id`(FK)、`state`、`state_source` CHECK(`unknown`/`confirmed`)、`reason`、`trace_id`、`created_at` |
| `audit_logs` | `id`、`who` CHECK(`user`/`system`)、`action`、`device_id`(可空)、`context`、`reason`、`level` 默认 `'low'`、`result` CHECK(`success`/`failed`)、`trace_id`、`created_at` |
| `config_audit` | `id`、`file`、`key_path`、`old_value`、`new_value`、`who` CHECK(`user`/`system`)、`source` CHECK(`startup`/`api`)、`result` CHECK(`applied`/`rejected`/`rolled_back`)、`error`、`trace_id`、`created_at` |
| `scenes` | `id`(PK)、`name` UNIQUE（1–40 字符）、`enabled` 默认 1、`created_at`、`updated_at` |
| `scene_action_groups` | `id`(PK)、`scene_id`(FK→scenes，级联删)、`name`（1–40）、`capability`（能力名，1–40）、`position`、`created_at` |
| `scene_action_targets` | `id`(PK)、`group_id`(FK→scene_action_groups，级联删)、`device_id`(FK→devices，级联删)、`position`、`created_at` |
| `scene_triggers` | `id`(PK)、`scene_id`(FK→scenes，级联删)、`type` CHECK(`manual`/`schedule`/`voice`/`device_state`)、`config` JSON、`enabled` 默认 1、`position`、`created_at`、`updated_at` |
| `command_requests` | `request_id`(PK)、`device_id`、`function_id`、`command`、`parameters` JSON、`actor`(1–64)、`status` CHECK(`running`/`sent`/`failed`)、`error_code`、`error_message`、`trace_id`、`created_at`、`updated_at` |

关系：

```
rooms 1──n devices 1──n device_functions 1──n ir_codes
                  ├──n device_states
                  └──n scene_action_targets n──1 scene_action_groups n──1 scenes
                                                          scenes 1──n scene_triggers
command_requests 独立留存，不随设备删除
```

- 场景只存「能力名 + 目标设备」，**不存 function_id**：执行时按 `capability` 现查设备功能。设备删功能后目标仍在，运行时报该设备缺少能力；设备被删则目标级联清理。
- `device_functions` **只存学码功能**，字段只有 id / device_id / name / 时间戳；没有 `action`、`params_schema`、`risk_level`、`kind`。
- `devices.extra` 可存 Agent 元数据 `type` / `aliases` / `risk_level`，只影响描述与确认策略，不改变控制语义。
- `devices` 没有 `capability`、`source`、`enabled`、`state/state_source/state_updated_at`：设备一律运行期创建，状态只来自 `device_states`。
- `ir_codes` 冗余 `device_id`：删设备时一次级联清干净，回放不必 join 功能表。

## 2. 不变式

1. **设备 id 唯一**：一律 `dev_*`，由服务端在 `POST /api/devices` 时生成。
2. **设备全部运行期创建**：没有配置种子、没有内置设备，也没有「重启复活」。
3. **功能从属设备**：`device_functions.device_id`、`ir_codes.device_id`、`device_states.device_id` 均指向 `devices`，删设备级联删除。
4. **功能表只存学码功能**：每行只有 id / 名字 / 时间戳；没有内置功能这个概念。
5. **重名允许、寻址用 id**：同设备功能可重名；调用与删除都按 `function_id`。
6. **码必须非空**：`raw_code` 为 JSON 数组，拒绝 `''` 与 `'[]'`；空波形在 API 层即 400。
7. **码以最新为有效**：读取永远取 `ir_codes.rowid` 最大（最后写入）的一条；每功能**只保留最近 3 条**，按 `rowid` 倒序保留、其余删除。
8. **状态唯一权威**：只认 `device_states` 最新记录；`devices` 不存状态。
9. **状态来源只有两种**：`unknown`（回放后无法确认）与 `confirmed`（用户校正）；不存在 `estimated` 与 `sensed`。
10. **学码不改设备**：学码只在已有设备上开会话；`cancel` 与空闲 TTL 只停接收态，不创建、不删除、不修改设备（`devices.extra` 恒为空对象）。

补充：房间被设备引用时不可删（409）；重命名房间会同步其下设备的 `room`。`audit_logs.level` 恒 `low`（列保留、已无分级）；`audit_logs.device_id` 可空，学码四类动作（`learn.start/save/cancel/idle_timeout`）与控制动作（`{device_id}.control`）写入对应设备 id，便于按设备精确筛选；`config_audit.source` 只取 `startup` / `api`。

## 3. 状态语义

| 取值 | 含义 | 何时写入 |
| --- | --- | --- |
| `unknown` | 不可判定 | 初始、每次学码回放后 |
| `confirmed` | 用户校正过 | `POST /api/correct` |

- 回放只代表「信号已发出」，没有任何反馈，所以回放后一律写 `unknown`；不存在 `estimated`。
- 状态只追加不覆盖：`device_states` 是事件流，读取取最新一条。
- `device_states.reason` 写入约定：控制与回放统一 `control:<function_id>`；用户校正写用户给的 `reason`，缺省 `用户校正`。

## 4. 调用模型

- **功能就是调用单元**：一次调用等于回放某个学码功能最新的一条码，没有内置功能、能力或参数层。
- `POST /api/control` 只接受 `function_id`；设备由功能归属推出，不需要也不接受 `device_id`。
- 同一设备可以有多个学码功能（开、关、调温……），区别只在用户起的名字与各自的码。
- `capabilities.yaml` 已删除；温度边界、枚举、扫风/风速/预设等参数化概念全部不存在，也不再出现在契约里。

## 5. 学码状态机

```
设备页 POST /api/devices ──▶ 建普通设备（extra={}）
        │
        ▼
POST /api/learn/start {device_id} ──▶ 只开会话，不创建、不修改设备
        │
        ├─ GET /api/learn/capture 每 1s（未捕获 captured=false，200）
        ├─ POST /api/learn/cancel（幂等，只停接收态）
        ├─ 服务端 600s 空闲 TTL → 关接收 + 审计 learn.idle_timeout
        └─ POST /api/learn/save → 先落库、再关接收；关失败不回滚码
```

- 失败/取消/超时/会话异常只结束会话与接收态，**设备原样保留**。
- 客户端 10 秒无信号判本次失败只是提示层；服务端 600s TTL 才是资源回收层。
- 保存时用户命名，得到 `device_functions` 一行；码落在 `ir_codes`，功能名不参与定位。

## 6. Schema、迁移与保留

- `SCHEMA_VERSION = 3`：v1 直接建最终 DDL；老库启动时原地升级——v1→v2 补 `scenes` / `scene_items` / `command_requests`，v2→v3 给 `scenes` 补 `enabled` / `updated_at`、新建 `scene_action_groups` / `scene_action_targets` / `scene_triggers`，并把旧 `scene_items` 一行转成一个动作组（能力 = 功能名，目标 = 该设备）后删除旧表。设备、功能、码、状态、审计数据原样保留。
- 如需从更老的库迁移：另写**一次性导入脚本**，按新 schema 映射（旧 `devices` 的 `capability`/`source`/`enabled`/三列状态丢弃；旧 `device_functions` 只留 learned 行并去掉动作/参数/风险列）。导入不在启动流程里。
- 保留期启动时清理：`device_states` 90 天（`state.retention_days`），`audit_logs` 365 天（`audit.retention_days`）。
- `ir_codes` 不按时间清理，按功能保留最近 3 条（按 `rowid` 倒序）；`config_audit` 不清理。
- 清理只按 `created_at < cutoff`；清理失败只告警，不阻断启动。

## 7. 配置权威

| 事实 | 权威文件 | 读取方 | 写入口 |
| --- | --- | --- | --- |
| 房间种子（名称/显示名/排序） | `devices.yaml` | 启动播种 | `ConfigService` |
| 网关模式、地址、Basic 鉴权、探测超时 | `settings.yaml` + `settings.local.yaml` | `build_gateways` | 仅改本地文件后重启 |
| 状态 / 审计保留期 | `settings.yaml` | `build_context` | `ConfigService` |

`devices.yaml` **只保留房间种子**，设备一律由 API 创建：

```yaml
rooms:
  - name: bedroom
    display_name: "卧室"
    sort_order: 1
  - name: living_room
    display_name: "客厅"       # 可选，缺省用 name
```

- `rooms` 每项必填 `name`；`display_name`、`sort_order` 可选（缺省分别为 `name` 与 0）。
- 覆盖层优先级：`*.local.yaml` > `*.yaml`；密钥只进本地覆盖层；配置写入必须经 `ConfigService`。
- 鉴权值与网关模式没有 API，只能改本地文件并重启。

## 8. 执行顺序与红线

- 先建最终 schema，再写 API；顺序反过来会让旧库在新代码下启动失败。
- 删列/删表一律「备份 → 重建表 → 迁移」，不在原地 `ALTER`；旧库只走一次性导入脚本，历史迁移不进启动流程。
- 不建内置功能相关结构：没有能力表、没有参数列、没有策略引擎。
- 清理不得改变语义：`trace_id`、审计、状态事件流三样必须保留完整。

## 附录：当前实现遗留（重建版不要建）

- `config/capabilities.yaml`、`devices.yaml` 的 `devices` 段、`PolicyEngine` 与策略拒绝审计：随内置功能体系一起删除。
- `devices`：`capability`、`source`（`builtin`）、`enabled`、`state`、`state_source`、`state_updated_at`、`protocol`、`ir_codes_path`。
- `device_functions`：`kind`、`action`、`params_schema`、`risk_level`、`enabled`。
- `ir_codes`：`protocol`、`source`（`learned`/`factory`）。
- `audit_logs`：`who='agent'`、`model_used`、`result='denied'`。
- 11 张停用表：`capabilities`、`scenes`、`scene_steps`、`schedules`、`policies`、`events`、`memories`、`profiles`、`route_logs`、`model_calls`、`redact_logs`。
- `settings.yaml`：`router.confidence_threshold`、`gateway.sensor.base_url`。
- 错误码：`POLICY_DENIED`、`FORBIDDEN`、`TIMEOUT`、`MODEL_UNAVAILABLE`、`FALLBACK_EXHAUSTED`、`REDACT_FAILED`、`BUDGET_EXCEEDED`、`P3_NOT_ALLOWED`。
