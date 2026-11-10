# HomeLink 下一阶段开发路线（交接给执行 Agent）

> 文档状态：待执行
> 基线提交：`d9adaec`（2026-11-10）
> 适用范围：`src/homelink`、`src/homelink_mcp`、`src/homelink_agent`、`tests/`、`docs/`
> 本文件是执行清单，不是愿景文档。每张任务卡都必须有代码、测试、文档三件套。

---

## 0. 执行 Agent 须知（先读这一节）

### 0.1 硬性规则

1. **不要改后端执行层的业务语义**：`unknown` / `confirmed`、`request_id` 幂等、`function_id` 回放、审计写入规则都属于已验收契约，除非任务卡明确要求。
2. **不要新增通用 HTTP 透传工具**，不要把 `/api/devices`、`/api/learn/*` 等管理端点暴露给模型。
3. **MCP 工具面保持 6 个**，除非任务卡明确要求新增；新增工具必须同步更新 `docs/agent-tools.md` 并过契约同步测试。
4. **任何契约变更（新增字段、改必填、改语义）必须同时改**：
   - `docs/api-contract.md`
   - `docs/agent-tools.md`
   - 对应 Pydantic / MCP schema
   - 测试
5. **每张任务卡独立提交**，提交前四项门禁必须全绿：

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m black --check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m pytest -q
```

6. **不得为了通过测试而放宽测试**。测试断言与文档冲突时，以任务卡为准，并记录冲突。
7. 遇到接口/语义歧义，**停止猜测**，在 `docs/decisions/` 下新增一条决策记录并在 PR/提交说明中标注。

### 0.2 当前基线（执行前自检）

| 项 | 值 |
| --- | --- |
| 测试 | `256 passed` |
| ruff | `All checks passed` |
| black | `106 files would be left unchanged` |
| mypy | `Success: no issues found in 100 source files` |
| 数据库 schema | `SCHEMA_VERSION = 2` |
| 后端端点 | 33 个（21 传统 + 12 Agent） |
| MCP 工具 | 6 个 |
| 工作区 | clean，HEAD `d9adaec` |

若上述任一项不一致，先停止执行，记录差异再继续。

### 0.3 关键文件索引

```text
后端
  src/homelink/interface/api/routers/agent.py      Agent 端点
  src/homelink/services/agent/service.py           AgentService（含 run_scene）
  src/homelink/services/agent/store.py             CommandRequestStore / SceneStore
  src/homelink/services/agent/catalog.py           设备模型与风险推断
  src/homelink/core/errors.py                      错误码
  src/homelink/core/database.py                    schema v2

MCP
  src/homelink_mcp/server.py                       6 个工具注册
  src/homelink_mcp/client.py                       /api/agent HTTP 客户端
  src/homelink_mcp/config.py                       环境变量
  src/homelink_mcp/errors.py                       错误信封 -> 模型可读
  tests/test_mcp_tools.py                          当前仅测配置/错误/工具名

Agent
  src/homelink_agent/agent.py                      主循环与确认闸门
  src/homelink_agent/daemon.py                     常驻服务
  src/homelink_agent/config.py                     环境变量
  src/homelink_agent/prompt.py                     系统提示
  tests/test_agent_tools.py                        纯逻辑测试
  tests/test_daemon.py                             常驻服务测试

评测
  scripts/agent_eval_set.json                      50 条用例
  scripts/eval_agent.py                            工具选择评测

契约文档
  docs/agent-tools.md                              系统提示 + 6 工具 schema
  docs/api-contract.md                             §2.10 Agent 能力
  docs/roadmap.md                                  旧路线图（保留）
  docs/acceptance-checklist.md                     验收清单（数字已过期）
```

---

## 1. 已知问题与证据

以下问题均在当前代码中确认存在，不是推测。

### P0-1 `request_id` 契约漂移

- `docs/agent-tools.md` 的 function schema：`required = ["device_id", "command", "request_id"]`
- `src/homelink_mcp/server.py`：`request_id: str | None = None`，缺省时 `uuid.uuid4()`
- `tests/test_mcp_tools.py::test_registers_six_tools`：断言 `required == {"device_id", "command"}`

**后果**：模型漏传时 MCP 自动生成新 ID；同一指令被重试会生成新 ID，后端幂等失效，可能重复发送红外。

### P0-2 场景执行绕过高风险确认

- `src/homelink/services/agent/service.py::run_scene` 内部固定 `confirm=True`
- `src/homelink/services/agent/store.py::SceneStore.list_all` 返回的场景不含风险信息

**后果**：包含门锁/燃气/烤箱/取暖器的场景，服务端不会返回 `CONFIRMATION_REQUIRED`，只靠系统提示约束模型。这是提示词安全，不是代码安全。

### P0-3 MCP 未透传身份与链路

- `src/homelink_mcp/client.py` 只发送 `Accept` 与可选 `Authorization`
- 未发送 `X-Actor` / `X-Trace-Id`

**后果**：审计里 Agent 调用一律记为默认 `user`；MCP 日志、后端审计、Agent 审计无法按 `trace_id` 串联。

### P0-4 缺少契约同步测试

- 工具名只在 `tests/test_mcp_tools.py` 硬编码集合，未与 `docs/agent-tools.md` 比对
- P0-1 的漂移正是缺少该测试的直接结果

### P0-5 文档数字漂移

| 位置 | 写的 | 实际 |
| --- | --- | --- |
| `README.md` 开头 | `user_version = 1` | `SCHEMA_VERSION = 2` |
| `README.md` 开头 | 21 个 `/api` 端点 | 33 个 |
| `docs/acceptance-checklist.md` | 198 passed | 256 passed |
| `docs/roadmap.md` | 233 passed | 256 passed |

### P1 级问题

| 编号 | 问题 | 证据 |
| --- | --- | --- |
| P1-1 | 无 CI | 仓库无 `.github/` |
| P1-2 | MCP Client 无 HTTP 层测试 | `tests/test_mcp_tools.py` 只测 config/errors/工具名 |
| P1-3 | 无 MCP→后端端到端测试 | 仅人工冒烟 |
| P1-4 | 每次请求新建 `AsyncClient` | `client.py::_request` 内 `async with httpx.AsyncClient(...)` |
| P1-5 | 评测只看工具选择 | `eval_agent.py::judge` 仅比对工具名 |
| P1-6 | daemon 无鉴权/限流/指标 | `daemon.py` 仅 `/api/chat`、`/api/health`、`/api/sessions/{id}` |

---

## 2. 路线总览

```text
P0 契约与安全收口（3~5 天）
   ↓
P1 质量门禁与可观测性（约 1 周）
   ↓
P2 Agent 服务生产化（约 1 周）
   ↓
P3 定时与场景能力（1~2 周，需先评审）
   ↓
P4 语音 / 多网关（可选，按使用反馈决定）
```

**优先级原则**：P0 未完成前，不启动 P2/P3/P4。P1 可以与 P0 部分并行。

---

## 3. P0：契约与安全收口

### 任务卡 P0-1：统一 `request_id` 语义

**目标**：同一轮对话内，同一设备 + 同一命令的重复调用必须复用同一 `request_id`；不同轮、不同设备、不同命令必须不同。

**推荐方案**（执行 Agent 按此实施，不要自行换方案）：

1. MCP 工具 `home_control_device` 的 `request_id` 改为**必填**，与 `docs/agent-tools.md` 对齐。
2. Agent 运行时负责生成：
   - `respond()` 开始时生成 `turn_id = uuid.uuid4().hex[:8]`
   - `_call_tool()` 中对 `home_control_device` 注入：
     ```python
     request_id = f"ag-{turn_id}-{sha256(device_id + '\x00' + command)[:12]}"
     ```
   - 同一 turn 内同设备同命令 → 同 ID；重试天然复用。
   - 长度 ≤ 64，满足后端约束。
3. 模型若自己传了 `request_id`，以运行时注入为准（覆盖），避免模型编造。
4. `home_run_scene` 由后端为每个场景项生成 `scene-<scene_id>-<index>-<random>`，维持现状，不在本卡范围。

**改动文件**：

```text
src/homelink_mcp/server.py
src/homelink_agent/agent.py
tests/test_mcp_tools.py
tests/test_agent_tools.py
docs/agent-tools.md（若 schema 有变）
```

**测试要求**：

- MCP `tools/list` 中 `home_control_device.required` 含 `request_id`
- 同 turn 同设备同命令连续调用两次 → 注入的 `request_id` 相同
- 不同 turn / 不同设备 / 不同命令 → `request_id` 不同
- 模型传入伪造 `request_id` → 被运行时覆盖
- 长度 ≤ 64、非空

**验收**：

- 后端 `command_requests` 中同一次重试只有一行记录
- `docs/agent-tools.md` 与实际 schema 一致
- P0-4 契约同步测试通过

---
### 任务卡 P0-2：场景高风险强制确认

**目标**：包含 `risk_level=high` 设备的场景，未确认时服务端返回 409 `CONFIRMATION_REQUIRED`。

**实施要点**：

1. `SceneStore.list_all()` / `get()` 增加风险聚合：
   - 通过 `DeviceStore` 取场景内每个设备，调用 `build_device_model` 或读取 `extra.risk_level`
   - 场景 payload 增加：
     ```json
     {"risk_level": "low" | "high", "contains_high_risk": true}
     ```
2. `AgentService.run_scene(scene_id, *, confirm: bool, ...)`：
   - 先算风险
   - 高风险且 `confirm=False` -> `AppError(CONFIRMATION_REQUIRED, ...)`
   - 通过后再逐项调用 `control(..., confirm=True)`
3. 路由 `POST /api/agent/scenes/{scene_id}/run` 增加可选 body：
   ```json
   {"confirm": false}
   ```
   - 兼容无 body：默认 `false`
   - 严格请求体：未声明字段 400
4. MCP `home_run_scene(scene_id, confirm=False)` 同步增加参数。
5. Agent `_guard_confirm` 扩展到 `home_run_scene`：
   - 待确认键用 `scene:<scene_id>`
   - 与设备确认状态互不串用
6. 更新契约文档：`docs/api-contract.md` §2.10、`docs/agent-tools.md` function schema。

**改动文件**：

```text
src/homelink/services/agent/store.py
src/homelink/services/agent/service.py
src/homelink/interface/api/routers/agent.py
src/homelink/interface/api/schemas.py
src/homelink_mcp/server.py
src/homelink_agent/agent.py
tests/test_agent_api.py
tests/test_mcp_tools.py
tests/test_agent_tools.py
docs/api-contract.md
docs/agent-tools.md
```

**测试要求**：

- 低风险场景：无 `confirm` 可执行
- 高风险场景：无 `confirm` -> 409，且 `command_requests` 无新增
- 高风险场景：`confirm=true` -> 执行，且只执行一次
- 无 body 调用 -> 按 `confirm=false` 处理
- 非法字段 -> 400
- Agent 侧：模型不能自己补 `confirm=true`；上一轮被拦 + 本轮明确同意才放行

**验收**：

- 高风险场景未经确认发送次数 = 0
- 确认后每条场景项只产生一条 `command_requests` 记录

---
### 任务卡 P0-3：透传 `X-Actor` 与 `X-Trace-Id`

**目标**：MCP 调用可被审计识别为 Agent 来源，并能与后端日志串链。

**实施要点**：

1. `src/homelink_mcp/config.py` 增加 `HOMELINK_ACTOR`，默认 `butler-agent`，最长 64 字符。
2. `HomeLinkClient._request`：
   - 每次请求生成 `trace_id = secrets.token_hex(4)`
   - 请求头加 `X-Actor`、`X-Trace-Id`
   - `HomeLinkError` 携带 `trace_id`
3. `homelink_agent` 启动 MCP 子进程时透传 `HOMELINK_ACTOR`（默认 `butler-agent`）。
4. MCP 日志（与 P1-4 合并实现）：stderr 输出 JSONL，字段含 `ts / tool / trace_id / duration_ms / ok / error_code`。
5. 确认 stdout 仍只有 MCP JSON-RPC。

**改动文件**：

```text
src/homelink_mcp/config.py
src/homelink_mcp/client.py
src/homelink_mcp/errors.py
src/homelink_agent/agent.py
src/homelink_agent/daemon.py
tests/test_mcp_tools.py
```

**测试要求**：

- MockTransport 断言请求头包含 `X-Actor`、`X-Trace-Id`
- `HOMELINK_ACTOR` 超长 -> 截断或启动失败（二选一，写进文档）
- 后端审计能看到 `actor=butler-agent`
- 错误信息包含 `trace_id`

**验收**：

- 一条 Agent 指令可从 `logs/agent-audit.jsonl` 追到 MCP 日志，再到后端 `audit_logs.trace_id`

---

### 任务卡 P0-4：契约同步测试

**目标**：文档与 MCP 实际 schema 不再漂移。

**实施要点**：

1. 新增 `tests/mcp/test_contract_sync.py`：
   - 读取 `docs/agent-tools.md` 第 3 节的 JSON 代码块
   - 解析工具名集合
   - 与 `await server.mcp.list_tools()` 的名称集合比对
   - 对每个工具比对 `required` 字段集合
   - 比对枚举字段（如 `device_type` 的 enum）
2. 解析失败要给出明确错误，不要静默跳过。
3. 同时校验 `home_run_scene` 的 `confirm` 参数存在（P0-2 之后）。

**改动文件**：

```text
tests/mcp/test_contract_sync.py（新增）
tests/mcp/__init__.py（如需要）
```

**验收**：

- 故意改错文档或 schema 任一侧，测试必须失败
- 正常状态下测试通过

---
### 任务卡 P0-5：文档数字校准

**目标**：消除验收文档与实际不一致。

**实施要点**：

| 文件 | 修改 |
| --- | --- |
| `README.md` | `user_version = 1` 改为 `schema v2`；21 端点改为 33 端点 |
| `docs/README.md` | 同步 schema / 端点表述 |
| `docs/acceptance-checklist.md` | 测试基线改为当前实际值（执行时以命令输出为准），并注明日期 |
| `docs/roadmap.md` | 同上；M3/M5 状态按实际更新 |
| `docs/agent-tools.md` | 与 P0-1/P0-2 同步 |

**验收**：

- 全文搜索 `198 passed`、`233 passed`、`user_version = 1`、`21 个` 不再出现（历史章节除外，需标注「历史」）
- 文档中的命令可复制执行

---
## 4. P1：质量门禁与可观测性

### 任务卡 P1-1：CI 门禁

新增 `.github/workflows/ci.yml`：

```yaml
name: ci
on: [push, pull_request]
jobs:
  quality:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e ".[mcp,agent,dev]"
      - run: python -m ruff check .
      - run: python -m black --check .
      - run: python -m mypy
      - run: python -m pytest -q
```

**验收**：

- push 到任意分支自动触发
- 四项任一失败则 job 失败
- 不依赖本机 Ollama / 真实板子

**注意**：若 Windows runner 上部分服务脚本测试不稳定，用 `pytest -m "not hardware"`，但不得跳过核心业务测试；需在文档中说明。

---

### 任务卡 P1-2：MCP Client 测试 + 连接复用

**目标**：HTTP 层可测，且不再每次新建客户端。

**实施要点**：

1. `HomeLinkClient.__init__` 支持注入 `transport: httpx.AsyncBaseTransport | None = None`。
2. 客户端改为长生命周期：
   - `HomeLinkClient` 持有一个 `httpx.AsyncClient`
   - 提供 `aclose()`
   - MCP Server 在 lifespan 中创建/关闭（FastMCP 支持 lifespan，按当前 SDK 版本实现）
   - 若 SDK 生命周期改造成本高，退而求其次：模块级惰性单例 + 显式关闭
3. 新增 `tests/mcp/test_client.py`：
   - 路径拼接正确（`quote` 处理 device_id / scene_id）
   - `Authorization` 仅在 token 非空时发送
   - 超时 -> `GATEWAY_TIMEOUT`
   - 连接失败 -> `UNREACHABLE`
   - 非 JSON -> `BAD_RESPONSE`
   - `success=false` -> `HomeLinkError.from_envelope`
   - HTTP 500 且 body 合法 -> 保留错误码

**验收**：

- 不启动真实后端也能覆盖全部错误分支
- MCP 连续调用 10 次不产生 10 个 TCP 连接（可用日志或 mock 计数验证）

---
### 任务卡 P1-3：MCP 到后端端到端测试

**目标**：stdio 子进程 + 真实 FastAPI 的链路进入自动化。

**实施要点**：

1. 新增 `tests/mcp/test_e2e.py`，标记 `@pytest.mark.integration`。
2. fixture：
   - 临时目录 + 最小 `settings.yaml` / `devices.yaml` / 空 DB
   - 用 `uvicorn.Server` 在随机空闲端口启动 `homelink.interface.api.app`（mock 网关）
   - 用 `mcp.client.stdio.stdio_client` 启动 `python -m homelink_mcp`，env 指向该端口
3. 用例：
   - `tools/list` 返回 6 个工具
   - 建设备 + 学码（直接调后端 API）-> `home_find_devices` 能看到
   - `home_control_device` 成功 -> `status=sent`
   - 重复同 `request_id` -> `replayed=true`
   - 错误设备 -> `isError=true` 且含 `NOT_FOUND`
4. 测试结束确保子进程与端口释放。

**验收**：

- 本地一条命令可跑：`pytest tests/mcp/test_e2e.py -q`
- CI 中可运行（不依赖 Ollama；仅 mock 网关）

---

### 任务卡 P1-4：MCP 结构化日志

**实施要点**：

- 新增 `src/homelink_mcp/logging.py`
- 日志只写 stderr，JSONL
- 字段：`ts / level / event / tool / trace_id / duration_ms / ok / error_code`
- 不记录 token、完整设备列表、用户原话
- `HOMELINK_LOG_LEVEL` 环境变量（默认 INFO）

**验收**：

- `python -m homelink_mcp` 的 stdout 无任何非 JSON-RPC 内容
- MCP Inspector 仍可正常连接

---

### 任务卡 P1-5：评测扩展

**目标**：从工具选择升级为安全 + 话术 + 延迟评测。

**实施要点**：

1. 用例扩到 120 至 150 条，分类：
   - 查询（20）
   - 控制（30）
   - 歧义/澄清（20）
   - 高风险确认（15）
   - 不支持能力（20）
   - 失败与重试（15）
   - 多轮（10）
2. 用例字段扩展：

```json
{
  "text": "...",
  "expect": ["home_control_device"],
  "forbid": [],
  "category": "control",
  "must_not_claim": ["已打开", "已关闭"],
  "max_steps": 4
}
```

3. `eval_agent.py` 指标：
   - 工具准确率
   - 误发控制次数（必须为 0）
   - 澄清率（歧义类必须追问）
   - 话术违规次数（`must_not_claim`）
   - P50 / P95 延迟
   - 模型异常数
4. 输出 `docs/eval-report.md`（执行时生成，含日期、模型名、通过率、失败明细）。

**验收**：

- 工具准确率 >= 95%
- 误发控制 = 0
- 话术违规 = 0
- 报告可复现（记录命令与环境）

---

### 任务卡 P1-6：daemon 指标

**实施要点**：

- `GET /api/metrics` 返回 JSON，包含 `requests_total / errors_total / tool_calls_total / latency_ms(p50,p95) / sessions / mcp_connected`
- 进程内计数即可，不引入 Prometheus 依赖
- 指标只读，不影响 `/api/chat`

**验收**：

- 调用 3 次 `/api/chat` 后计数递增
- 不泄露用户输入

---
## 5. P2：Agent 服务生产化

### 任务卡 P2-1：daemon 鉴权

- 新增 `AGENTD_TOKEN`（默认空 = 不校验，保持本机开发体验）
- 非空时：除 `/api/health` 外所有 `/api/*` 要求 `Authorization: Bearer <token>`
- 失败返回 401，统一错误信封
- 更新 README 环境变量表

### 任务卡 P2-2：队列与背压

- 保留全局模型锁（9B 单卡）
- 增加 `AGENTD_QUEUE_TIMEOUT`（默认 30 秒）
- 等待超时返回 429，message 说明模型忙
- 不丢请求、不重复执行

### 任务卡 P2-3：确认状态机加固

- 明确同意词集合与拒绝词集合
- 待确认项带过期时间（默认 120 秒）
- 一次性使用，执行后清除
- 审计记录 `confirm_requested / confirm_granted / confirm_denied / confirm_expired`
- 补测试：长句夹带确认词不放行；过期后不放行；跨设备不串用

### 任务卡 P2-4：优雅退出

- 收到 SIGTERM / Ctrl+C 时关闭 MCP 子进程、HTTP 连接池、会话存储
- 记录 `shutdown` 审计
- 验证无孤儿进程（Windows 用 `tasklist`，Linux 用 `pgrep`）

---

## 6. P3：定时与场景能力（需先评审）

**前置决策**：执行层不做调度，调度放 Agent 侧。

建议范围：

1. Agent 侧 `schedules` 表（独立 SQLite，如 `data/agent.db`，不碰 `homelink.db`）
2. daemon 增加：
   - `POST /api/schedules`（scene_id、cron、enabled、confirm）
   - `GET /api/schedules`
   - `DELETE /api/schedules/{id}`
3. 到点调用 MCP `home_run_scene`
4. 高风险场景首次创建时必须确认；执行失败记录审计，不自动重复发送

**暂不加入 MCP 工具面**，避免工具选择准确率下降。

**验收**：

- 定时触发一次场景，只产生一组 `command_requests`
- 服务重启后计划仍在
- 高风险场景未确认不可创建

---

## 7. P4：可选能力

| 能力 | 启动条件 | 备注 |
| --- | --- | --- |
| 语音入口 | 文本链路连续稳定使用 2 至 4 周 | 本地 STT/TTS，识别失败只反问 |
| 多网关 | 单网关确认不够用 | v2 契约，需单独评审 |
| 多用户/权限 | 出现第二个使用者 | 需要身份体系 |
| 长期记忆 | 出现明确个性化需求 | 先做偏好表，不急着上向量库 |

---

## 8. 建议排期（两周）

| 天 | 任务 |
| --- | --- |
| D1 | P0-1 request_id 统一 |
| D2 | P0-2 场景高风险确认 |
| D3 | P0-3 trace/actor 透传 |
| D4 | P0-4 契约同步测试 + P0-5 文档校准 |
| D5 | P1-1 CI |
| D6-D7 | P1-2 Client 测试 + 连接复用 |
| D8-D9 | P1-3 端到端测试 |
| D10 | P1-4 结构化日志 + P1-6 指标 |
| D11-D12 | P1-5 评测扩到 120 条 |
| D13 | P2-1 / P2-2 |
| D14 | P2-3 / P2-4 + 阶段验收 |

---
## 9. 阶段验收清单

### P0 完成定义

- [ ] `home_control_device` 的 `request_id` 为必填，MCP 与文档一致
- [ ] 同轮同设备同命令重试复用同一 `request_id`
- [ ] 高风险场景未确认返回 409，且无 `command_requests` 新增
- [ ] 高风险场景确认后只执行一次
- [ ] MCP 请求带 `X-Actor` 与 `X-Trace-Id`
- [ ] 审计可按 `trace_id` 串起 Agent 与后端
- [ ] 契约同步测试存在且能捕获漂移
- [ ] 文档数字与实际一致
- [ ] 四项门禁全绿

### P1 完成定义

- [ ] CI 自动跑四项门禁
- [ ] MCP Client 错误分支全覆盖
- [ ] MCP 到后端端到端测试可一条命令运行
- [ ] MCP 日志为 stderr JSONL，stdout 干净
- [ ] 120+ 评测用例，误发 0，话术违规 0
- [ ] `/api/metrics` 可用且不泄露用户输入

### P2 完成定义

- [ ] daemon 支持 Bearer token
- [ ] 队列超时返回 429
- [ ] 确认状态机有明确同意/拒绝/过期/一次性测试
- [ ] 退出无孤儿进程

---

## 10. 完成任务卡后的交付格式

每个任务卡提交时，提交说明必须包含：

```text
任务卡：P0-1
改动：...
测试：pytest 结果、ruff/black/mypy 结果
契约影响：有/无
文档：更新的文档路径
风险：...
```

若任务卡无法完成，必须写明：

```text
阻塞原因：
已验证的假设：
需要谁决策：
建议的替代方案：
```

---

## 11. 待决策项（执行前需确认）

| 问题 | 影响 | 建议 |
| --- | --- | --- |
| request_id 由 Agent 注入还是模型生成 | P0-1 方案 | 推荐 Agent 注入，模型不可见 |
| 场景风险等级是否需要逐项返回 | P0-2 返回结构 | 推荐返回 contains_high_risk + risk_level |
| MCP 是否升级到 Streamable HTTP | 远程部署 | 本阶段不做，保持 stdio |
| 定时放 daemon 还是独立服务 | P3 | 先放 daemon，规模变大再拆 |
| 是否引入 Prometheus | P1-6 | 本阶段不引入，JSON 指标够用 |

---

## 12. 参考

- 现有路线图：[roadmap.md](./roadmap.md)
- API 契约：[api-contract.md](./api-contract.md)
- Agent 工具契约：[agent-tools.md](./agent-tools.md)
- 验收清单：[acceptance-checklist.md](./acceptance-checklist.md)
- 架构说明：[architecture.md](./architecture.md)

---

## 13. 执行顺序速查

```text
P0-1 -> P0-4 -> P0-5
P0-2 -> P0-4
P0-3 -> P1-4
P1-1 -> P1-2 -> P1-3
P1-5 -> P1-6
P2-1 -> P2-2 -> P2-3 -> P2-4
P3（评审后）
P4（按需）
```

依赖说明：

- P0-4 依赖 P0-1 和 P0-2 的最终 schema。
- P1-3 依赖 P1-2 的客户端可注入 transport。
- P1-5 依赖 P0-1、P0-2 的行为稳定。
- P2 依赖 P0-3 的 trace 与 P1-6 的指标基础。