# 开发路线图

本文件规划「红外设备管家（执行层）」到「上层智能管家 Agent」的演进顺序与验收口径。
定位不变：本仓库负责设备、学码、单次回放、虚拟状态与审计；**Agent 是独立进程**，只通过 HTTP 调用本服务，不进入 `homelink` 包、不直接读写 SQLite。

## 现状基线（2026-11-10）

| 项 | 现状 |
| --- | --- |
| 后端 | FastAPI 单进程 + 单 SQLite（schema v2）；15 条路径 / 33 个端点（21 传统 + 12 Agent），11 个错误码，`X-Trace-Id` 全链路 |
| Agent 接口 | `/api/agent/*` 粗粒度命名空间：设备模型含 `type/room/aliases/risk_level/capabilities/state`，`request_id` 幂等，场景仅顺序执行 |
| 工具面 | `src/homelink_mcp` 把 6 个能力封装成 MCP 工具（stdio）；`src/homelink_agent` 是本地 Ollama 侧车，工具 schema 由 `tools/list` 动态生成 |
| 前端 | Vue 3 正式前端已构建并由后端同源托管；Streamlit 六页保留但非启动入口 |
| 硬件 | 一台 ESPHome 红外网关；`mock` / `real` 只靠配置切换 |
| 验证 | 全量 pytest 233 passed；ruff / black / mypy 全绿；真实板子只做只读探测，从未发送真实红外 |
| 运维 | 开机静默自启（计划任务 `HomeLinkService`）+ 登录后地址展示（`HomeLinkAddresses`） |

## 总体原则

1. 执行层不引入 LLM 依赖；模型调用、对话与语音全部放在 Agent 侧。
2. 模型不得绕过 HTTP API，不得直接写库，不得直接发红外。
3. 红外无回传：Agent 只能承诺「已发送」，设备状态仍以用户校正为 `confirmed`。
4. 每个里程碑结束都必须满足：全量 pytest 0 failed、mock 全流程可跑、真实板子验证保持只读或经确认。
5. 契约变更（新增字段、改语义）单独评审，不在 Agent 里程碑里顺手改。

## 里程碑

```
M0 收口 ──▶ M1 工具层 ──▶ M2 语义与安全 ──▶ M3 Agent MVP ──▶ M4 语音入口
                                                   └──▶ M5 自动化 / 多网关（按需）
```

| 里程碑 | 状态 | 落地情况 |
| --- | --- | --- |
| M0 收口 | 完成 | 空目录已删、历史门禁问题已修、remote 可用 |
| M1 工具层 | 完成（换了形态） | 没有走「OpenAPI 自动生成几十个工具」，改为 `/api/agent` 6 个粗粒度能力 + 统一 envelope |
| M2 语义与安全 | 完成 | 统一设备 ID/房间/别名、能力描述、`request_id` 幂等、高风险确认、11 个错误码、审计 |
| M3 Agent MVP | MVP 完成 | `homelink_mcp`（6 工具）+ `homelink_agent`（Ollama `qwen3.5:9b`），运行时拦截模型自补确认 |
| M4 语音入口 | 暂缓（已决策） | 文本链路已稳定；先用一段时间再定是否启动 |
| M5 自动化 | 部分 | 场景已恢复（仅按顺序执行一组功能）；Agent 审计与重试策略已补；定时/规则仍返回 501 |

### M0 收口现状

**目标**：把仓库恢复到「干净基线」，避免后续里程碑被历史噪声干扰。

- 清理空的残留目录 `src/brain`、`src/core`、`src/gateway`。
- 修复历史 black 待格式化文件与 mypy 报错（不改变运行行为）。
- 配置 git remote；确认提交与推送链路可用。
- 确认开机自启、备份恢复、日志位置在 README 中可查。

**验收**：ruff / black / mypy / pytest 四项全绿；冷启动后 `http://127.0.0.1:8000/api/health` 正常。

**不做**：不新增业务能力，不调整端点契约。

### M1 工具层：让服务可被程序稳定调用

**目标**：Agent 无需读源码，仅凭 OpenAPI 就能生成正确的工具调用。

- 确认 `GET /openapi.json` 可用且与 `api-contract.md` 一致（15 条路径 / 21 个端点、错误码、鉴权）。
- 读取能力已够用：`GET /api/devices` 已返回每个设备的功能列表与房间内部名，房间显示名由 `GET /api/rooms` 提供；按名字定位放在 Agent 侧做本地匹配，M1 不新增搜索端点（确需新增时单独评审契约）。
- 明确「一次调用」的边界：`POST /api/control` 只接受 `function_id`，不做参数校验，不含探测前置。
- 运维：SQLite 备份 / 恢复脚本与 `logs/` 轮转——已落地（`scripts/backup.py` 在线快照 + integrity_check + 保留裁剪，`scripts/restore.py` 先验后恢复、坏备份拒绝、旧库改名保留，`scripts/rotate_logs.py` copy-truncate 滚动），2026-11-10 已对运行中的生产库实测备份成功。

**验收**：从空库开始，用 OpenAPI 生成的工具集完成「建设备 → 学码 → 调用 → 查状态 → 查审计」；恢复脚本可在新目录还原数据。

**不做**：不引入能力表（capability）、不参数化控制、不做策略分级。

### M2 语义与安全边界

**目标**：让 4B 模型「不会猜错、不敢乱发」。

- 功能命名规范：设备名 + 房间 + 功能名可读且能唯一定位；重名时返回明确提示。
- 交互策略：控制前必须复述目标并等待确认；未匹配到功能时反问，不猜测。
- 状态语义：控制成功只回「已发送」，界面与 Agent 文案不得暗示「已开启」。
- 审计可追溯：一次对话的每次调用都能按 `trace_id` 串起来；如需区分来源，再单独评审 `actor` 字段。

**验收**：未确认指令 0 次发送；未知设备 / 未知功能 0 次误调用；审计可按时间与设备回放整段操作。

**不做**：不改变 `unknown` / `confirmed` 语义，不引入「设备在线」判定。

### M3 Agent 侧车 MVP（已落地，Ollama `qwen3.5:9b`）

**目标**：本地对话完成「找到设备 → 找到功能 → 确认 → 调用」的最小闭环。

实际形态（与初稿的差异）：

- 没有新建 `agent/`，而是拆成两层：`src/homelink_mcp` 把 6 个粗粒度能力封装成 MCP 工具（stdio），`src/homelink_agent` 消费 MCP 并驱动 Ollama。
- 工具面固定为 6 个：`home_find_devices`、`home_get_device_state`、`home_get_home_snapshot`、`home_control_device`、`home_run_scene`、`home_create_automation`（固定 501）。工具 schema 不写死，由 MCP `tools/list` 动态转成 Ollama 格式。
- Agent 不 import `homelink`、不访问数据库，只经 `homelink-mcp` 子进程发 HTTP；本机模型名可用 `OLLAMA_MODEL` 覆盖。
- 兜底策略：高风险设备由运行时拦截——模型不能自己补 `confirm=true`，只有「上一轮被拦下的同一设备 + 本轮用户明确同意」才放行；其余一律降级为 `confirm=false`，由服务端返回 409。

**已验收**：mock 网关下三条链路实测通过——查状态（`find_devices` → `get_device_state`，答「unknown」）、控制（`control` → 「信号已发出」）、不支持的功能（无灯具 / 无法调亮度，未误调）。高风险场景：无同意时 `command_requests` 计数不变；「打开 → 同意」两轮里只在第二轮产生一条 `sent`。

**评测（2026-11-10，mock，`qwen3.5:9b`）**：50 条全部执行，**50 通过（100%）**，「不应发送却发送」**0 次**。「风扇定时关掉」最初按控制类标注判为未命中，复核定为准正确：设备只有一个「定时」红外码（无独立「定时关」），语义有歧义时先澄清、不盲目发送才是期望行为，已重归类为澄清类（禁止误发）并复测通过。首轮第 24 条曾出现一次 Ollama 500（模型生成畸形 tool_call XML），已修复异常组处理，单条模型故障计错但不中断整批；重跑该条通过。第 9 条约束生效——「关掉卧室那个风扇」实际调用了控制工具。

**常驻服务化（2026-11-10 已落地）**：`homelink-agentd`（FastAPI，默认 127.0.0.1:8100）把一次性 CLI 变成常驻服务——MCP 子进程长连（掉线只重连、不重发失败轮，返回 502，避免红外重复发送）；模型带 `keep_alive`（默认 30m）启动预热；每个 `session_id` 独立保留历史（最近 10 轮）与高风险确认状态，TTL 120 分钟、上限 20 会话（LRU）。模型调用全局串行（9B 单卡）。实测：首轮 9.2s（含网关快照），次轮 2.5s。开机自启随 `install-service.ps1` 注册 `HomeLinkAgent` 计划任务。

**不做**：长期记忆、多轮复杂规划、自动重试。

### M4 语音入口（可选）

**目标**：语音 → Agent → 现有 API，链路不绕过 M3。

- 本地 STT / TTS（如 whisper.cpp、sherpa-onnx），不接云服务。
- 语音识别失败、置信度低时只反问，不产生控制请求。

**验收**：安静环境 20 句常用指令可完成闭环；识别失败 0 次发送。

### M5 自动化与多网关（按需，最后做）

- 场景已恢复，但只支持「按顺序执行一组已有功能」（`POST /api/agent/scenes/{id}/run`），不引入调度器；定时 / 规则仍返回 501。若要做定时，应由 Agent 侧调度，通过 HTTP 触发场景，不回到本服务。
- Agent 侧审计已落地：`logs/agent-audit.jsonl` 逐行记录 `turn_start / tool_call / confirm_downgraded / turn_end / turn_error`，可用 `AGENT_AUDIT_PATH` 改路径或 `off` 关闭。
- 服务端重试策略已明确写入契约：调用方不得自动重发；`GATEWAY_TIMEOUT` 视为「可能已发出」，`EXECUTION_FAILED / DEVICE_OFFLINE` 可在用户确认后用新 `request_id` 重发。
- 多网关需要把 `gateway` 从单实例改为多实例，会触及配置、探测与调用路由，属于 v2 契约变更，需单独设计评审。

**验收**：按最终选定的范围另行补充；未选定前不排期。

## 待决策

| 问题 | 影响 | 建议 |
| --- | --- | --- |
| 是否恢复场景 / 定时 | 决定 M5 是否启动 | 已决策：场景恢复为「顺序执行一组功能」，定时不做 |
| 是否支持多网关 | 决定是否进入 v2 契约 | 单网关够用时不做 |
| 是否引入语音 | 决定 M4 优先级 | 已决策：暂缓，实际使用文本链路一段时间后再评估 |
| Agent 是否需要来源标识 | 影响审计字段 | 默认不加，审计已可按 `trace_id` 串联 |
