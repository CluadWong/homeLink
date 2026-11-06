# 验收清单（重建版后端 + 过渡前端）

基准日期：2026-11-06。证据口径：全量测试 **190 passed**（连续两次一致，全程 mock）；真实板子做过**一次只读探测**（192.168.10.3，HTTP 200，详见 §5），**从未发送任何真实红外**。

图例：✅ 已实现且有单测 ｜ 🟡 代码存在但无自动化/运行验证 ｜ 🟠 只有单测替身证据、未真机验证 ｜ ❌ 未实现 ｜ ⚠️ 文档与实现不符（已在本轮修正）

## 1. `scope.md`

| 条目 | 状态 | 证据 / 缺口 |
| --- | --- | --- |
| 单进程 + 单 SQLite，无外部服务 | ✅ | `Database` 每次操作独立连接；无 Redis/消息队列 |
| 房间与设备管理（含删功能级联） | ✅ | `test_rooms.py`、`test_devices.py`、`test_learn.py::test_delete_function_cascades_codes` |
| 设备运行期创建、无内置设备 | ✅ | `POST /api/devices` 只建 `dev_*`；`adapter` 恒 `ir` |
| 学码四步 | ✅ mock / 🟠 real | `test_learn.py` 覆盖 start/capture/save/cancel、401 例外、TTL；真机未跑 |
| 调用回放 | ✅ mock / 🟠 real | `test_control.py`；mock 只记录波形，不证明真板子收到 |
| 状态只追加与用户校正 | ✅ | `test_state.py`、`test_correct.py` |
| 网关 mock / real、地址与探测 | ✅ mock / 🟠 real | mock 全链路单测；real 探测已真机确认可达（HTTP 200），回放与学码未执行 |
| 审计 | ✅ | `test_audit.py`、`test_control.py`、`test_learn.py` |
| Streamlit 六页（过渡前端） | 🟡 | `interface/clients/streamlit/` 六页代码已落盘（最后写入 19:54），从未启动、无自动化测试 |
| Vue 正式前端（`frontend/`） | 🟡 | 已构建 `frontend/dist` 并由后端同源托管；curl 确认页面 200，但无 UI 自动化与浏览器功能验证 |
| 判据① mock 全流程跑通 | ✅ | 190 项单测覆盖「建设备→学码→调用→校正→审计」 |
| 判据① real 切换零代码 | 🟠 | 已确认 real 探测链路可用（只读）；控制与学码仍从未真机执行 |
| 判据② 重启状态不丢 | ✅ | 状态不入内存，`GET /api/state` 直接读 `device_states` 最新行；无跨进程冒烟 |
| 判据③ 审计可追溯 | ✅ | 每行带 `trace_id`，`GET /api/audit` 可按 `result` / `device_id` 过滤 |
| 判据④ 学码半途退出不留空壳 | ✅ | `cancel`、TTL、撤壳双条件均有单测 |
| 范围外（传感器、AI、策略引擎） | ✅ | 未实现即符合；传感器三项恒 `null` |

## 2. `architecture.md`

| 条目 | 状态 | 证据 / 缺口 |
| --- | --- | --- |
| 分层红线（interface/services/gateway/core） | ✅ | grep 核对：`services` 不 import `interface`，`gateway` 不 import `services`/`brain`，`core` 为叶子 |
| 装配顺序（DB→配置→网关→仓储→服务域） | ✅ | `build_context` 单测通过；文档原写的 `state_store.restore` 不存在，已改为「读库即恢复」 ⚠️ |
| 控制链路五步 | ✅ | `ControlService` 单测：取功能→取最新码→回放→写 `unknown`→审计 |
| 运行时可改项与重启边界 | ✅ | `PUT /api/gateway` 写覆盖层 + `pending_restart`；其余重启生效 |
| 21 个 HTTP 端点 | ✅ | `test_api_common.py` 及端点级测试 |
| 目录骨架 | 🟡 | `scripts/start.py`、流式前端已存在；`agent_future/`、`hardware/ir-codes/` 目录不存在（未引用，不影响运行） |
| ADR-01 网关抽象 | ✅ | `IRGateway` + mock/real 两实现 |
| ADR-02 功能即调用单元 | ✅ | 无能力表、无参数，`function_id` 唯一寻址 |
| ADR-03 单服务域 | ✅ | 只有 `services/device_management` |
| ADR-04 状态只追加 | ✅ | `device_states` 事件流，读取最新 |
| ADR-05 配置写唯一入口 | ✅ | `test_config_service.py` 含原子写、回滚、审计 |
| ADR-06 学码复用设备 | ✅ | 草稿由 `POST /api/devices` 建，`start` 不建资源 |
| 失败隔离 | ✅ | 503/401 映射、关接收失败不回滚、配置审计 `rejected` 回滚 |
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
| 学码状态机与 600s 空闲 TTL | ✅ | `test_learn.py`（到期清会话、审计、撤壳、区分 cancel） |
| 保留期清理 | ✅ | `test_retention.py`（90/365 天，失败不阻断启动） |
| 一次性导入脚本 | ❌ | 文档要求「重建时另写」，未交付 |
| 配置权威（`settings.local.yaml` 覆盖） | ✅ | `test_config_service.py` |
| 执行顺序红线（播种早于设备写入） | ✅ | 装配实现与测试均体现 |

## 4. `api-contract.md`

| 条目 | 状态 | 证据 / 缺口 |
| --- | --- | --- |
| 21 端点、统一错误信封、严格请求体 | ✅ | 端点级单测 + `test_api_common.py` |
| 7 个错误码与状态映射 | ✅ | 旧码已清；401/404/409/503 均有断言 |
| Bearer 鉴权（空 token 跳过） | ✅ | `test_api_common.py` 四条 |
| `X-Trace-Id` 生成与回带 | ✅ | 生成/复用/超长重新生成 |
| health、rooms、devices、learn | ✅ | 对应测试文件 |
| control（含 `result` 键冻结） | ✅ | `test_control.py`、`test_control_state_audit_gateway.py` |
| state（无 `success`、传感器 null） | ✅ | `test_state.py` |
| correct（`state≤64`、`reason≤128`） | ✅ | `test_correct.py`；长度用例在 `test_control_state_audit_gateway.py`（state 64、reason 128 边界） |
| gateway（15s 缓存、`refresh`、`pending_restart`、URL 校验） | ✅ | `test_gateway.py`、`test_gateway_api.py` |
| audit（`limit` 50/200、`result` 三值、`device_id` 精确） | ✅ | `test_audit.py`、新增 `device_id` 列筛选单测 |
| 真实网络路径（跨进程 HTTP、多 worker） | 🟠 | 全部用 `TestClient` 进程内调用 |

## 5. `ir-gateway.md`

| 条目 | 状态 | 证据 / 缺口 |
| --- | --- | --- |
| mock 固定行为（默认 `no_signal`、显式注入） | ✅ | `test_gateway.py` |
| Basic 鉴权（双非空才发头） | ✅ 已确认 / 🟠 拒绝对路径 | 真机：带 Basic 的三个 GET 全部 200（2026-11-06）；错误密码下的 401 行为未试 |
| 真实协议端点与 base64url 波形 | 🟠 部分已确认 | `GET /`、`GET /text_sensor/ir_last_code`、`GET /switch/ir_learn_mode` 均 200；`POST /infrared/ir_proxy_tx/transmit` 的 form body 与波形载荷**仍未发送过**，未知 |
| 逐条失败映射（拒连/超时/401/5xx/畸形） | 🟠 仍未知 | 只观察到 200 路径；拒连/超时/5xx 只有替身证据 |
| 学码时序与「开学习清上次波形」 | 🟠 仍未知 | 未开学习模式、未让任何人按键；仅确认 `ir_learn_mode` 实体可读（`state=OFF`） |
| 服务端 600s 空闲 TTL | ✅ | `test_learn.py` 四条 TTL 用例 |
| 客户端 10s 判失败 | 🟡 | Streamlit `learn.py` 有逻辑，从未运行验证 |
| 单向语义（回放后一律 `unknown`） | ✅ 后端 / 🟡 前端 | 后端单测；前端硬约束未运行验证 |
| 探测 15s 缓存与 `refresh` | ✅ | `test_gateway.py`、`test_gateway_api.py` |
| 独立脚本 `scripts/ir_learn.py`（0.25s 轮询） | ❌ | 不存在 |

**真机只读确认结果（2026-11-06）**：目标 `http://192.168.10.3`，Basic 凭据为用户名/密码双非空。

| 请求（只读） | 结果 | 耗时 |
| --- | --- | --- |
| `GET /` | HTTP 200（鉴权被接受） | 195 ms |
| `GET /text_sensor/ir_last_code` | HTTP 200，`{"value":"","state":""}`（实体存在、当前为空） | 55 ms |
| `GET /switch/ir_learn_mode` | HTTP 200，`{"state":"OFF","value":false}`（实体存在、未在学码） | 152 ms |

**仍未知**：红外发送（`transmit` 载荷与固件兼容性、38000Hz 载波）、学码采集链路（需真实按键）、401/超时/5xx 的真实表现、重启后的实际行为。**没有执行任何 POST、控制或红外发送。**

## 6. `frontend-contract.md`

| 条目 | 状态 | 证据 / 缺口 |
| --- | --- | --- |
| Vue 3 + Vite 技术栈与六页结构 | 🟡 | `frontend/` 六页已实现并构建 `dist`，由后端同源托管（curl 页面 200）；无 UI 自动化与浏览器功能验证 |
| 过渡 Streamlit 六页（设备/学码/调用/状态/校正/审计） | 🟡 | 代码齐全，从未启动、无 UI 测试 |
| 非标准 `state` 原样显示、超 12 字符截断 | ✅ 代码 / 🟡 运行 | 本轮修改 `widgets.state_text` |
| 成功只说「已发送」 | 🟡 | `pages/call.py` 文案已按契约，未运行验证 |
| 鉴权头、错误人话、trace 展开 | 🟡 | `http_client.py` / `widgets.py` 有单测级逻辑，无 UI 验证 |
| 页面级交互（离页取消、去重 cancel、离线不整页拦截） | 🟡 | 代码可见对应处理，未运行验证 |

## 7. `README.md`

| 条目 | 状态 | 证据 / 缺口 |
| --- | --- | --- |
| 安装与启动命令 | ✅ | `docs/README.md` 与本轮新增的根 `README.md` |
| 配置键、mock/real 切换、token | ✅ | `docs/README.md` 最小说明表 |
| 环境变量与默认路径 | ✅ | `JJ_BUTLER_CONFIG_DIR`、`JJ_BUTLER_DB_PATH` |
| 根 `README.md` 最短入口（安装/启动/配置/文档索引/重建声明） | ✅ | 本轮新增；不复制正文，只指向 `docs/` |
| 配置缺失/不可解析的友好报错（文件路径 + 最小示例 + 非 0 退出） | ✅ | `test_config_startup.py` 六条（含 CLI 子进程退出码 1、无 Traceback） |
| 静态托管开关 / history 回退 / `/api/*` 不吞 404 / 缺 dist 降级 | ✅ | `test_static_frontend.py` 五条；curl 实测同源页面 200、`/api/unknown` 404 JSON |
| 一键启动（日志落文件、Ctrl+C 终止子进程） | ✅ 代码 / 🟡 真机 | `test_start_script.py`（含 `_stop` 终止用例）；未做完整交互式 Ctrl+C 手测 |
| 诚实声明 mock-only / 真实红外未发送 | ✅ | 根 `README.md` 与 `docs/README.md` 同步；Vue 前端已交付但未做 UI 验证也写明 |

## 8. 能否离开 `d:\Project\JJ-Butler2` 独立运行

**后端可以，但有前提；前端（Vue dist）由同一进程同源托管，不再需要第二个前端进程。**

- 依赖：Python ≥ 3.11；`pip install -e .` 拉齐 fastapi / uvicorn / pydantic / pyyaml / httpx；SQLite 是标准库，无系统服务。
- 配置：`config/settings.yaml` 必须存在（缺失时打印文件路径与最小示例，非 0 退出）；`devices.yaml` 提供房间种子；`settings.local.yaml` 放密钥。
- 数据：默认写仓库根 `data/jj_butler.db`；`Database.initialize()` 会递归创建父目录，目标目录不必预先存在。
- 路径风险：默认路径由 `Path(__file__).parents[4]` 推出，只在仓库内 / editable 安装成立；普通 wheel 安装会指向错误目录，**必须**设置 `JJ_BUTLER_CONFIG_DIR` 与 `JJ_BUTLER_DB_PATH`。
- 前端：`frontend/dist` 随仓库或单独拷贝即可，`frontend.enabled=true` 时由后端直接提供；**重新构建**才需要 Node（`cd frontend && npm install && npm run build`），生产不使用 Vite dev server。
- 结论：拷出 `config/` + `frontend/dist` + 装包 + 设两个环境变量即可独立运行；不依赖仓库内其它脚本或文件。必需配置缺失时现在会给「文件路径 + 最小示例 + 文档指针」并以非 0 退出，不必再读堆栈猜。
