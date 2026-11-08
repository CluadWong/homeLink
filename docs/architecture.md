# 架构

## 1. 分层与依赖红线

自外向内：`interface`（HTTP / Streamlit）→ `services`（服务域）→ `gateway`（硬件抽象）→ `core`（库、配置、时间）；`brain` 只剩审计落点（策略引擎已删除）。

1. `interface` 不 new 服务、不直接碰库；只从 `bootstrap` 取装配好的对象图。
2. `services` 不得 import `interface`；`gateway` 不得 import `services` / `brain`。
3. `core` 是叶子，不 import 任何上层；配置读写只有 `ConfigService` 一个入口。
4. `agent_future` 为已停用包，生产代码不得引用。反向引用即架构破坏。

## 2. 运行时装配

`build_context(config_dir, db_path, gateway_mode)` 是唯一装配入口：

1. `Database.initialize()` —— 重建版直接建最终 DDL，`SCHEMA_VERSION = 1`；无历史迁移链。
2. `ConfigService`（挂 `SqliteAuditStore`）→ `build_gateways` / `build_gateway_probe`（按 `mode` 选 mock/real）。
3. `DeviceStore`（运行期设备）→ `DeviceStateStore` → `AuditSink`。
4. `build_service_domains` 装配「设备管理」服务域；没有能力注册表，也没有策略引擎。
5. `sync_config_to_database` 只做房间播种 —— **必须早于任何设备写入**：`devices.room` 引用 `rooms.name`，空表时建设备会失败。
6. 启动清理 `purge_older_than(90)` / `purge_audit_logs(365)`，失败只告警。状态**不载入内存**，`GET /api/state` 直接读 `device_states` 最新记录，因此重启天然不丢。

`AppContext` 是装配结果的数据类，API 与集成测试共用；`pending_base_url` 是唯一的运行期可变字段（`PUT /api/gateway` 写入，重启后自然清空）。

## 2.5 控制链路

`POST /api/control` 的固定顺序：

1. router 取 `AppContext`，生成 `trace_id`。
2. 按 `function_id` 取功能与所属设备；功能不存在，或该功能还没有可用码 → 404。
3. 取该功能最新一条码，经 `IRGateway` 回放（固定 `ir_proxy_tx`）。
4. 状态落地：追加一条 `device_states`，`state=unknown`、`state_source=unknown`、`reason=control:<function_id>`。
5. 审计：成功与失败都写 `audit_logs`（`who=user`），`trace_id` 与响应同值；启动播种、清理、学码空闲超时这类系统收尾写 `system`，重建版**不会出现** `agent`。

没有策略裁决、没有参数校验、没有状态映射表。审计失败**不得**回滚已发出的红外——信号已出门，回滚只会造成「现实已开、库里没记」。发送/学码连接失败 → 503；探测结果**不是**控制前置条件。

## 2.6 运行时可改与重启才生效

| 变更对象 | 生效方式 |
| --- | --- |
| 设备、房间、功能、码 | 立即（写库） |
| 网关地址 | `PUT /api/gateway` 写本地覆盖层并回显新地址（`pending_restart=true`）；探测与发送链路都重启后切换，带 `restart_required` |
| 网关模式、Basic 鉴权、房间种子 | 装配期读取，**改后必须重启**；鉴权无 API，只能改 `settings.local.yaml` |
| 保留期 | 启动清理时读取，下次启动生效 |

运行期改文件后假设已生效是错的：没有文件监听，写盘不等于装配。

## 3. 模块边界与对外集成面

| 面 | 形态 | 用途 | 是否契约 |
| --- | --- | --- | --- |
| HTTP API | 21 个端点 | 唯一程序化入口 | 是，见 `api-contract.md` |
| 配置目录 | `settings*.yaml`、`devices.yaml`（仅房间种子） | 部署期输入 | 是 |
| 数据目录 | 一个 SQLite 文件 | 持久化 | 否，外部不得直连 |

**不外暴露**：内部类、仓储、表结构、Streamlit 页面、`agent_future`。**嵌入式集成**只允许 `build_context(...)` 取 `AppContext`，进程外只允许 HTTP。**单实例独占 SQLite**，不支持多进程写同一库；身份只体现在 `audit_logs.who`（`user` / `system`）。

## 4. 目录骨架

```
src/jj_butler/
  core/         配置服务、SQLite、时间工具 —— 无业务
  gateway/      IRGateway 抽象、mock/real、探测缓存
  brain/        审计落点（AuditSink）；策略引擎已删除
  services/device_management/  唯一服务域：设备 / 房间 / 功能 / 码 / 状态 / 学码
  bootstrap/    对象图装配、房间播种、保留期清理
  interface/api/  router，见 `api-contract.md`
  interface/clients/streamlit/  过渡客户端
  agent_future/ 已停用，禁止引用
config/          settings / devices（房间种子）+ *.local.yaml
hardware/ir-codes/  设备侧历史归档，不作为数据源
```

启动式固定为 `uvicorn jj_butler.interface.api.app:app`（工作目录为仓库根；包是标准 src-layout，`src/` 本身不是包）。

## 5. 压缩 ADR

- **ADR-01 网关抽象**：硬件差异收敛在 `IRGateway`，mock/real 可互换；新增硬件只加实现。
- **ADR-02 功能即调用单元**：没有内置功能、能力与参数；`function_id` 是唯一调用入口，一次调用回放一条学码。
- **ADR-03 单服务域**：不为多域提前抽象；内核不持有任何设备名。
- **ADR-04 状态只追加**：`device_states` 是事件流，读取取最新；来源只有 `unknown` / `confirmed`。
- **ADR-05 配置写入唯一入口**：必须经 `ConfigService`，否则并发写坏文件且 `config_audit` 断链。
- **ADR-06 学码复用设备**：设备由设备页创建，`start` 只开会话；`cancel` 与 TTL 只停接收态，不增删设备。

## 6. 失败隔离

- 发送/学码连接失败 → 503；探测失败只影响展示；发送 10s、探测 3s，二者独立，探测缓存 15s。
- 学码异常 → 停接收态、返回结构化错误，设备原样保留；关接收失败 → 已存码不回滚，返回 `receiver_stopped` / `receiver_error`。
- 配置审计写入失败 → 变更整体 `rejected`，不允许「改了但没记录」。
- 启动清理失败只记日志；库损坏照常抛错、启动失败。

## 7. 观测与部署

- 每个请求带 `trace_id`，贯穿 `audit_logs`、`config_audit` 与错误响应；审计是唯一操作历史来源。
- 单进程 uvicorn，无 worker 外扩：SQLite 单写者假设；配置进版本库，库文件与备份不进；前端独立进程，只走 HTTP。
