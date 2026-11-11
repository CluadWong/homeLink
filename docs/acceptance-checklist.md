# 验收清单（重建版后端 + Vue 正式前端）

基准日期：2026-11-06，终态更新 2026-11-08，P0 收口复测 2026-11-10，单 Agent 真机冒烟 2026-11-11。证据口径：全量测试 **472 passed**（2026-11-11；198 passed 为 2026-11-08 里程碑验收的历史值）；真机（192.168.10.3）已完成「学码 → 保存 → 试一次」与「调用」端到端验收，期间真实发送过红外，结果由用户人眼确认；2026-11-11 补做单设备控制、场景执行、定时触发冒烟各 1 次，均 `status=sent`（详见 [backlog.md](./backlog.md) L5）。

图例：✅ 已实现且有单测 ｜ 🟢 已真机验证 ｜ 🟡 代码存在但无自动化/运行验证 ｜ 🟠 只有单测替身证据、未真机验证 ｜ ❌ 未实现 ｜ ⚠️ 文档与实现不符（已在本轮修正）

## 1. `scope.md`

| 条目 | 状态 | 证据 / 缺口 |
| --- | --- | --- |
| 单进程 + 单 SQLite，无外部服务 | ✅ | `Database` 每次操作独立连接；无 Redis/消息队列 |
| 房间与设备管理（含删功能级联） | ✅ | `test_rooms.py`、`test_devices.py`、`test_learn.py::test_delete_function_cascades_codes` |
| 设备运行期创建、无内置设备 | ✅ | `POST /api/devices` 只建 `dev_*`；`adapter` 恒 `ir` |
| 学码四步 | ✅ mock / 🟢 real | `test_learn.py` 覆盖 start/capture/save/cancel、401 例外、TTL；真机「学码 → 保存 → 试一次」全链路可用（风扇），无信号、断电、关接收失败三种失败面均已实测 |
| 调用回放 | ✅ mock / 🟢 real | `test_control.py`；真机两个功能分别回放正确、码未串位，最终动作由人眼确认 |
| 状态只追加与用户校正 | ✅ | `test_state.py`、`test_correct.py`；真机失败请求未新增 `device_states` 行 |
| 网关 mock / real、地址与探测 | ✅ mock / 🟢 real | mock 全链路单测；real 探测、学码、回放均已在真机执行，状态页网关块断电离线、复电在线 |
| 审计 | ✅ / 🟢 real | `test_audit.py`、`test_control.py`、`test_learn.py`；真机失败控制审计为 `result=failed` 且 `reason` 指明连接超时 |
| Streamlit 六页（过渡前端） | 🟡 已冻结，不参与验收 | `interface/clients/streamlit/` 六页代码保留、不启动、不做 UI 测试；正式前端为 Vue，Streamlit 已不是启动入口 |
| Vue 正式前端（`frontend/`） | 🟢 真机手工 / 🟡 无 UI 自动化 | 已构建 `frontend/dist` 并由后端同源托管；真机验收全程通过该前端操作，仍无 UI 自动化测试 |
| 判据① mock 全流程跑通 | ✅ | 280 项单测（2026-11-10）覆盖「建设备→学码→调用→校正→审计」 |
| 判据① real 切换零代码 | 🟢 | 同一套代码切 `gateway.mode=real` 后完成真机学码、保存、回放与调用，未改业务代码 |
| 判据② 重启状态不丢 | ✅ | 状态不入内存，`GET /api/state` 直接读 `device_states` 最新行；多次重启服务后状态与审计均保留 |
| 判据③ 审计可追溯 | ✅ | 每行带 `trace_id`，`GET /api/audit` 可按 `result` / `device_id` 过滤 |
| 判据④ 学码半途退出不留半开会话 | ✅ | `cancel`、TTL 均有单测；两者只停接收态、设备保留；真机 `save` 关接收失败时会话仍结束，`cancel` 可幂等重试 |
| 范围外（传感器、AI、策略引擎） | ✅ | 未实现即符合；传感器三项恒 `null` |

## 2. `architecture.md`

| 条目 | 状态 | 证据 / 缺口 |
| --- | --- | --- |
| 分层红线（interface/services/gateway/core） | ✅ | grep 核对：`services` 不 import `interface`，`gateway` 不 import `services`/`brain`，`core` 为叶子 |
| 装配顺序（DB→配置→网关→仓储→服务域） | ✅ | `build_context` 单测通过；文档原写的 `state_store.restore` 不存在，已改为「读库即恢复」 ⚠️ |
| 控制链路五步 | ✅ / 🟢 real | `ControlService` 单测：取功能→取最新码→回放→写 `unknown`→审计；真机回放成功，断电时在第一步即失败且不写状态 |
| 运行时可改项与重启边界 | ✅ | `PUT /api/gateway` 写覆盖层 + `pending_restart`；其余重启生效 |
| 38 个 HTTP 端点（22 传统 + 16 Agent，29 条路径） | ✅ | `test_api_common.py` 及端点级测试；`/openapi.json` 实测 29 paths / 38 endpoints |
| 目录骨架 | 🟡 | `scripts/start.py`、流式前端已存在；`agent_future/`、`hardware/ir-codes/` 目录不存在（未引用，不影响运行） |
| ADR-01 网关抽象 | ✅ / 🟢 real | `IRGateway` + mock/real 两实现；real 实现在真机完成探测、学码、回放 |
| ADR-02 功能即调用单元 | ✅ | 无能力表、无参数，`function_id` 唯一寻址 |
| ADR-03 单服务域 | ✅ | 只有 `services/device_management` |
| ADR-04 状态只追加 | ✅ | `device_states` 事件流，读取最新；真机失败回放未产生状态行 |
| ADR-05 配置写唯一入口 | ✅ | `test_config_service.py` 含原子写、回滚、审计 |
| ADR-06 学码复用设备 | ✅ | 设备由设备页建，`start` 不建资源；`cancel`/TTL 不动设备 |
| 失败隔离 | ✅ / 🟢 real | 503/401 映射、关接收失败不回滚、配置审计 `rejected` 回滚；真机断电 → `DEVICE_OFFLINE`、错误密码 → `DEVICE_UNAUTHORIZED`、关接收失败仍返回保存成功 + 黄框提示 |
| trace 贯通 | ✅ | 中间件 + 审计 + 错误信封；`state`/`correct` 无 body `trace_id` 是契约例外 |
| 单进程/SQLite 单写者假设 | ✅ | 未做多 worker / 并发写测试 |

## 3. `domain-and-data.md`

| 条目 | 状态 | 证据 / 缺口 |
| --- | --- | --- |
| 7 张表、列、CHECK、外键 | ✅ | `test_schema.py`（含本轮新增 `audit_logs.device_id`） |
| `user_version=1`，无历史迁移链 | ✅ | 老库缺 `device_id` 时在同一 v1 内 `ALTER TABLE` 补列，不改版本号 |
| 不建列红线（`params`、`estimated` 等） | ✅ | `test_schema.py::test_forbidden_columns_absent`、`test_state_source_rejects_estimated` |
| 10 条不变量 | ✅ | 房间引用、adapter 恒 ir、码保留 3 条（`rowid` 倒序）、状态 CHECK 等均有单测 |
| 状态语义 `unknown` / `confirmed` | ✅ | `test_state.py`、`test_correct.py` |
| 学码状态机与 600s 空闲 TTL | ✅ mock / 🟢 real | `test_learn.py`（到期清会话、审计、只停接收、区分 cancel）；真机学码/保存/cancel 已走通，TTL 仍由单测覆盖 |
| 保留期清理 | ✅ | `test_retention.py`（90/365 天，失败不阻断启动） |
| 一次性导入脚本 | 不适用 | 重建版不带旧数据（README 已声明「不带旧代码、旧数据、旧迁移链」）；该脚本仅在未来确需沿用旧库时才写 |
| 配置权威（`settings.local.yaml` 覆盖） | ✅ | `test_config_service.py` |
| 执行顺序红线（播种早于设备写入） | ✅ | 装配实现与测试均体现 |

## 4. `api-contract.md`

| 条目 | 状态 | 证据 / 缺口 |
| --- | --- | --- |
| 21 端点、统一错误信封、严格请求体 | ✅ | 端点级单测 + `test_api_common.py` |
| 7 个错误码与状态映射 | ✅ / 🟢 real | 旧码已清；401/404/409/503 均有断言；真机实测 `DEVICE_OFFLINE`（断电）与 `DEVICE_UNAUTHORIZED`（错误密码） |
| Bearer 鉴权（空 token 跳过） | ✅ | `test_api_common.py` 四条 |
| `X-Trace-Id` 生成与回带 | ✅ | 生成/复用/超长重新生成 |
| health、rooms、devices、learn | ✅ / 🟢 real | 对应测试文件；`test_learn.py` 覆盖 `save` 空名/超长名 400、保存后回放成功/失败；真机学码四步全链路通过 |
| control（含 `result` 键冻结） | ✅ / 🟢 real | `test_control.py`、`test_control_state_audit_gateway.py`；真机回放与断电失败均符合契约 |
| state（无 `success`、传感器 null） | ✅ | `test_state.py` |
| correct（`state≤64`、`reason≤128`） | ✅ | `test_correct.py`；长度用例在 `test_control_state_audit_gateway.py`（state 64、reason 128 边界） |
| gateway（15s 缓存、`refresh`、`pending_restart`、URL 校验） | ✅ | `test_gateway.py`、`test_gateway_api.py` |
| audit（`limit` 50/200、`result` 三值、`device_id` 精确） | ✅ / 🟢 real | `test_audit.py`、新增 `device_id` 列筛选单测；真机失败控制记录 `failed` + 超时原因 |
| 真实网络路径（跨进程 HTTP、多 worker） | 🟢 单 worker | 服务以 `0.0.0.0:8000` 跨进程运行并被真机前端调用；多 worker 未纳入本轮验收 |

## 5. `ir-gateway.md`

| 条目 | 状态 | 证据 / 缺口 |
| --- | --- | --- |
| mock 固定行为（默认 `no_signal`、显式注入） | ✅ | `test_gateway.py` |
| Basic 鉴权（双非空才发头） | ✅ mock / 🟢 real | 真机带 Basic 全部 200；错误密码下 `learn/start` 返回 401 `DEVICE_UNAUTHORIZED`，message 指明 `settings.local.yaml` 的 `gateway.ir.username` / `gateway.ir.password` |
| 真实协议端点与波形 | 🟢 | 学码（`turn_on` + 读 `ir_last_code`）、回放（`transmit`，38000 Hz）均已在真机执行；波形为逗号分隔微秒序列（100 段，末尾 `-10000`），已按真实格式修复并有真实样本回归 |
| 逐条失败映射（拒连/超时/401/5xx/畸形） | 🟢 部分 | 拒连/超时 → `DEVICE_OFFLINE`（断电实测）；401 → `DEVICE_UNAUTHORIZED`（错误密码实测）；畸形波形三类错误由单测覆盖；真实 5xx 未构造 |
| 学码时序与「开学习清上次波形」 | 🟢 | 真机学码成功；10 秒无信号 → 明确失败可重试；开始学码后断电 → 超时失败；捕获后断电再保存 → 保存成功 + 接收态未关黄框；断电重插后 `ir_learn_mode=OFF`，人为置 ON 不挡新会话（见下方补充验证） |
| 服务端 600s 空闲 TTL | ✅ | `test_learn.py` TTL 用例（只停接收、设备保留） |
| 客户端 10s 判失败 | 🟢 | 前端轮询与超时提示已在真机断电路径验证 |
| 单向语义（回放后一律 `unknown`） | ✅ 后端 / 🟢 前端 | 后端单测；前端「试一次 / 调用」在真机执行并显示「已发送」，状态仍 `unknown` |
| 探测 15s 缓存与 `refresh` | ✅ / 🟢 real | `test_gateway.py`、`test_gateway_api.py`；状态页网关块断电离线、复电自动恢复在线 |
| 独立脚本 `scripts/ir_learn.py`（0.25s 轮询） | 不交付 | 调试辅助、非产品路径；产品链路是客户端 1s 轮询 `capture`，此脚本明确不交付（不是待补，而是决策不做） |

**真机验收证据（2026-11-08，经前端手工操作）**：目标 `http://192.168.10.3`。

| 场景 | 结果 |
| --- | --- |
| 学码 → 保存 → 试一次（风扇） | 全链路通过；两个功能分别回放正确、码未串位 |
| 学码 10 秒无信号 | 明确失败 + 可重试 |
| 捕获后断电再保存 | 「功能已保存」+ 接收态未关黄色提示（设计意图） |
| 开始学码后断电 | 超时失败 |
| 断电状态下调用 / 试一次 | `DEVICE_OFFLINE`「设备没有响应…」+ 指向状态页 |
| 失败路径残留核对 | `/api/state` 未变、审计 `result=failed`、`device_states` 未新增 |
| 错误 Basic 密码 → `learn/start` | 401 `DEVICE_UNAUTHORIZED`，message 指明配置文件与键名 |
| 状态页网关块 | 板子断电离线、复电自动在线 |

**断电恢复与残留接收态补充验证（2026-11-08，只操作 `switch/ir_learn_mode`，未发红外）**：

| 步骤 | 结果 |
| --- | --- |
| 板子断电重插、服务在线后 `GET /switch/ir_learn_mode` | `state=OFF`（恢复后复位） |
| 无活动会话时 `GET /api/learn/capture` | 404 `NOT_FOUND`「学码会话不存在或已结束」 |
| 人为 `turn_on` 置板子为 ON（`state=ON`）后调 `POST /api/learn/start` | 200「已开始接收红外信号」，残留接收态未挡住新会话 |
| 该会话 `GET /api/learn/capture`（不按键） | 200，`captured=false`、`reason=no_signal` |
| `POST /api/learn/cancel` | 200；随后 `GET /switch/ir_learn_mode` 回到 `OFF` |

**仍保留的开放项**：真实电器的最终动作只能人眼确认（红外单向，无回读）。「再学一个」按钮走同一 `start` 路径，本次验证的是该路径本身；未单独在 UI 上点击该按钮复测。

## 6. `frontend-contract.md`

| 条目 | 状态 | 证据 / 缺口 |
| --- | --- | --- |
| Vue 3 + Vite 技术栈与六页结构 | 🟢 真机手工 / 🟡 无 UI 自动化 | `frontend/` 六页已实现并构建 `dist`，由后端同源托管；真机验收全程使用该前端，仍无 UI 自动化测试 |
| 学码三步式 + 保存后「试一次」 | 🟢 | `LearnView` 第一步选已有设备 + 预置功能名，捕获后沿用该名字保存，保存成功后可「试一次」；真机全链路通过，关接收失败时黄框指向「再学一个」 |
| 过渡 Streamlit 六页（设备/学码/调用/状态/校正/审计） | 🟡 已冻结，不参与验收 | 代码保留、不启动、不做 UI 测试；正式前端为 Vue，Streamlit 已不是启动入口（与 docs/README.md 一致） |
| 非标准 `state` 原样显示、超 12 字符截断 | ✅ 代码 / 🟡 运行 | 本轮修改 `widgets.state_text` |
| 成功只说「已发送」 | 🟢 | 前端调用与试一次成功均只显示「已发送」；真机确认 |
| 鉴权头、错误人话、trace 展开 | 🟢 部分 | 断电、错误密码、学码超时均展示人话提示与状态页指引；trace 展开仍无 UI 自动化 |
| 页面级交互（离页取消、去重 cancel、离线不整页拦截） | 🟡 | 代码可见对应处理，未单独做交互测试 |

## 7. `README.md`

| 条目 | 状态 | 证据 / 缺口 |
| --- | --- | --- |
| 安装与启动命令 | ✅ | `docs/README.md` 与根 `README.md` |
| 配置键、mock/real 切换、token | ✅ | `docs/README.md` 最小说明表 |
| 环境变量与默认路径 | ✅ | `HOMELINK_CONFIG_DIR`、`HOMELINK_DB_PATH` |
| 根 `README.md` 最短入口（安装/启动/配置/文档索引/重建声明） | ✅ | 不复制正文，只指向 `docs/` |
| 配置缺失/不可解析的友好报错（文件路径 + 最小示例 + 非 0 退出） | ✅ | `test_config_startup.py` 六条（含 CLI 子进程退出码 1、无 Traceback） |
| 静态托管开关 / history 回退 / `/api/*` 不吞 404 / 缺 dist 降级 | ✅ | `test_static_frontend.py` 五条；curl 实测同源页面 200、`/api/unknown` 404 JSON |
| 一键启动（日志落文件、Ctrl+C 终止子进程） | ✅ 代码 / 🟢 真机 | `test_start_script.py`（含 `_stop` 终止用例）；真机验收期间多次用该脚本启动与重启，端口释放正常 |
| 诚实声明 real 模式与单向语义 | ✅ | 根 `README.md` 与 `docs/README.md` 同步；真机验收结论与开放项已写入本清单 §5 |

## 8. 能否离开 `d:\Project\HomeLink` 独立运行

**后端可以，但有前提；前端（Vue dist）由同一进程同源托管，不再需要第二个前端进程。**

- 依赖：Python ≥ 3.11；`pip install -e .` 拉齐 fastapi / uvicorn / pydantic / pyyaml / httpx；SQLite 是标准库，无系统服务。
- 配置：`config/settings.yaml` 必须存在（缺失时打印文件路径与最小示例，非 0 退出）；`devices.yaml` 提供房间种子；`settings.local.yaml` 放密钥。
- 数据：默认写仓库根 `data/homelink.db`；`Database.initialize()` 会递归创建父目录，目标目录不必预先存在。
- 路径风险：默认路径由 `Path(__file__).parents[4]` 推出，只在仓库内 / editable 安装成立；普通 wheel 安装会指向错误目录，**必须**设置 `HOMELINK_CONFIG_DIR` 与 `HOMELINK_DB_PATH`。
- 前端：`frontend/dist` 为构建产物、不入 git；独立运行前需执行一次 `cd frontend && npm install && npm run build` 生成，或从已构建环境拷贝 dist 目录。`frontend.enabled=true` 时由后端同源提供，生产不使用 Vite dev server。
- 结论：拷出 `config/` + 构建产物 `frontend/dist`（dist 不入 git，需先构建或从已构建环境拷贝）+ 装包 + 设两个环境变量即可独立运行；不依赖仓库内其它脚本或文件。必需配置缺失时现在会给「文件路径 + 最小示例 + 文档指针」并以非 0 退出，不必再读堆栈猜。

## 9. P3 新增面（场景 v3 / 调度器 / Agent 运维，2026-11-11 补记）

> 背景：场景动作组、触发器、执行记录、调度器、daemon 指标与鉴权都是近期新增面，旧清单未覆盖。

| 条目 | 状态 | 证据 / 缺口 |
| --- | --- | --- |
| 场景动作组（`action_groups`）与顺序执行 | ✅ / 🟢 real | `test_agent_api.py::test_scene_action_group_crud_and_triggers`、`test_scene_payload_aggregates_high_risk`、`test_scene_requires_exactly_one_action_source`；真机 2026-11-11 执行「离家」成功（见 backlog L5） |
| 场景逐设备失败不整体回滚 | ✅ | `test_scene_run_is_best_effort_when_one_device_missing_capability`、`test_scene_run_reports_offline_per_device_without_rollback` |
| 场景触发器（manual / voice / schedule） | ✅ 模型 / 🟡 voice 冻结 | `test_scene_trigger_config_is_validated`；`schedule` 触发器由 daemon 启动时同步；`voice` 只保留数据模型，触发入口未实现（已冻结） |
| 场景执行记录 `GET /api/agent/scenes/{id}/runs` | ✅ / 🟢 real | `test_scene_runs_group_by_trace_and_are_scoped_to_scene`、`test_scene_runs_validates_limit_and_scene`；真机读取到 2026-11-11 冒烟记录 |
| 高风险场景确认闸门 | ✅ / 🟢 real | `test_high_risk_scene_requires_confirmation`；未确认返回 409 `CONFIRMATION_REQUIRED` 且不发送（backlog L4 隔离环境实测） |
| 调度器 `ScheduleStore` 与 cron | ✅ / 🟢 real | `test_scheduler.py` 14 条（持久化、同分钟不重复、禁用跳过、失败不重试、旧表迁移、触发器同步）；真机 2026-11-11 18:25 自动执行 1 次，仅 1 条 `command_requests` |
| 调度计划 API（`/api/schedules` CRUD） | ✅ | `test_daemon.py::test_schedule_create_list_delete_and_persist`、`test_schedule_persists_after_restart`、`test_high_risk_scene_requires_confirm_at_creation` |
| daemon `/api/metrics` | ✅ | `test_metrics_counters_after_three_chats`、`test_metrics_counts_errors_on_503`、`test_metrics_counts_tool_calls`、`test_metrics_percentile_and_sample_cap` |
| `AGENTD_TOKEN` 鉴权 | ✅ | `test_bearer_auth_enforced_when_token_set`、`test_bearer_auth_off_when_token_empty`（`/api/health` 豁免） |
| 场景前端页 | ✅ 代码 / ✅ UI 自动化 | `frontend/src/views/ScenesView.vue`（列表、编辑、执行、最近执行）；`tests/e2e/test_scenes_page.py` 覆盖列表→新建→编辑→执行→结果弹层→执行记录，`pytest -m e2e` 一键运行，CI `e2e` job 执行 |
| `data/agent.db` 备份与恢复 | ✅ | `scripts/backup.py` 默认同时快照 `agent-*.db`，`restore.py` 按前缀自动恢复；`test_backup_restore.py` 4 条（见 backlog C1） |
| `agent.db` schema 版本化 | ✅ | `PRAGMA user_version`（`AGENT_SCHEMA_VERSION = 1`）；v0 自动升级，版本过高明确报错；见 backlog C2 |
| 调度失败可见性 | 🟡 | `last_status` / `last_error` 已入库且 `GET /api/schedules` 返回，前端未展示（见 backlog C5） |
| daemon 单实例防重 | ✅ | `InstanceLock`（`msvcrt` / `fcntl` 文件锁）在 `main()` 启动时独占 `<agent.db>.lock`；`test_instance_lock_blocks_second_instance` |
| 数据保留期 | ✅ | 状态 90 / 审计 365 / 指令 180 天，启动时清理；场景「最近执行」由指令记录派生（见 backlog C6） |
| 审计隐私 | ✅ | 根 `README.md` 明确审计含用户原话与工具参数属敏感数据；`rotate_logs.py` 覆盖 `.jsonl`（见 backlog C7） |
