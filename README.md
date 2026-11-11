# HomeLink（从 0 重建版）

红外单向设备管家（控制器版）：FastAPI + SQLite 后端，配 Vue 3 + Vite 正式前端（`frontend/`，构建产物由后端同源托管）；`interface/clients/streamlit/` 是保留的旧过渡代码，已不是启动入口。

> **这是从 0 重建的版本，与旧 `JJ-Butler` 代码库无关**：不带旧代码、旧数据、旧迁移链；schema 为 v3（`SCHEMA_VERSION = 3`）。

## 安装与启动

前置：Python ≥ 3.11。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
uvicorn homelink.interface.api.app:app --host 0.0.0.0 --port 8000
```

- 默认 `mock` 模式：不连真实板子、不发红外。
- 前端（Vue）构建产物为 `frontend/dist`，不入 git：独立运行前需执行一次 `cd frontend && npm install && npm run build` 生成，或从已构建环境拷贝 dist 目录。构建后与 `/api` **同源**：浏览器直接打开 `http://127.0.0.1:8000/`。生产不使用 Vite dev server。
- 一键启动：`python scripts/start.py`（只拉起 uvicorn，日志写 `logs/backend.log`，Ctrl+C 不留孤儿进程）。
- 离开本仓库运行时，用环境变量 `HOMELINK_CONFIG_DIR`（配置目录）与 `HOMELINK_DB_PATH`（SQLite 文件）指定路径。

### 开机自启与查看地址

开机自启由 Windows 计划任务实现，**安装 / 卸载都需要管理员权限**（各执行一次）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\service\install-service.ps1
powershell -ExecutionPolicy Bypass -File scripts\service\uninstall-service.ps1
```

安装后有四个任务：

| 任务 | 触发 | 行为 |
| --- | --- | --- |
| `HomeLinkService` | 开机（以 SYSTEM 运行，无需登录） | 后台静默执行 `scripts\service\run-service.cmd`，启动输出含访问地址，写 `logs\service.log` |
| `HomeLinkAgent` | 开机（以 SYSTEM 运行） | 常驻 Agent 服务（`homelink-agentd`，127.0.0.1:8100），日志写 `logs\agent.log` |
| `HomeLinkBackup` | 每天 03:00（以 SYSTEM 运行） | 在线快照数据库到 `backups\`（默认保留 7 份），日志写 `logs\backup.log` |
| `HomeLinkAddresses` | 用户登录 | 弹出终端执行 `scripts\service\show-addresses.cmd`，只显示当前访问地址，不重复启动服务 |

- Agent 任务依赖 Ollama 已在运行；Ollama 未就绪时 daemon 会照常启动，首轮提问返回 503。
- 安装脚本会**停用**旧项目的 `JJButlerService`（只停用不删除）；不想动旧任务时加 `-KeepOldService`。
- 卸载后旧任务保持停用；需要恢复时执行 `uninstall-service.ps1 -RestoreOldService`。
- 只想立刻看地址：运行 `scripts\service\show-addresses.cmd`（或 `python scripts\addresses.py`）。

### 备份与日志轮转

```powershell
python scripts\backup.py                  # 在线快照 data\homelink.db 到 backups\，保留最近 7 份
python scripts\restore.py backups\homelink-20261110-142437.db --force   # 先停服务；旧库改名 .pre-restore-* 保留
python scripts\rotate_logs.py             # logs\ 下超 5 MB 的 .log/.jsonl 滚动，保留 3 份
```

- 备份用 SQLite 在线快照，**服务不用停**；备份后自动做 integrity_check，坏文件不留。
- 学码数据全在库里，`install-service.ps1` 已注册 `HomeLinkBackup` 每天 03:00 自动备份（保留 7 份）；手动跑 `backup.py` 也随时可用，`--keep` 控制保留份数。
- 恢复前先停服务；脚本会先校验备份、恢复后复检，坏备份直接拒绝且不动现有库。


## 配置

配置目录默认是仓库根的 `config/`：

- `config/settings.yaml`：网关模式（`mock` / `real`）、网关地址、保留期、`api.token`、静态托管开关（`frontend.enabled` / `frontend.dist_path`）。
- `config/settings.local.yaml`：本地覆盖层（网关 Basic 用户名/密码、token），不入库。
- `config/devices.yaml`：只有房间种子；设备一律运行期通过 API 创建。

必需配置缺失或不可解析时，启动会打印**缺哪个文件、最小可用示例、文档位置**，并以非 0 退出码结束。

## 本地管家 Agent（可选）

`src/homelink_agent` 是一个独立进程：把 6 个能力经 `homelink-mcp` 暴露给本地 Ollama，不 import 服务端代码、不碰数据库。

```powershell
pip install -e ".[agent]"
ollama serve                       # 另开终端，确保模型已 pull
python -m homelink_agent "卧室的风扇现在是什么状态？"
python -m homelink_agent           # 交互模式
```

### 常驻服务模式（推荐）

`homelink-agentd` 把上面的一次性 CLI 变成常驻 HTTP 服务：MCP 子进程只起一次、模型以 `keep_alive` 驻留显存（启动时预热）、每个 `session_id` 独立保留历史与高风险确认状态。

```powershell
homelink-agentd                    # 默认 127.0.0.1:8100，开机自启见下文安装脚本
```

| 接口 | 说明 |
| --- | --- |
| `POST /api/chat` | `{"message": "...", "session_id": "..."}` → `{"reply", "session_id"}`；`session_id` 省略则新建 |
| `GET /api/health` | 连接、模型、会话数 |
| `DELETE /api/sessions/{id}` | 主动丢弃会话（含未完成的高风险确认） |
| `POST /api/schedules` | `{"scene_id", "cron", "enabled", "confirm"}` → 新建定时任务（5 段 cron，本机时区） |
| `GET /api/schedules` | 列出全部定时任务与最近一次执行结果 |
| `DELETE /api/schedules/{id}` | 删除定时任务 |

掉线语义：MCP 掉线时该轮返回 502 并重置连接，**不自动重发**（避免红外重复发送）；Ollama 不可用返回 503。

定时任务存独立 SQLite（`data/agent.db`，不碰 `homelink.db`），到点经 MCP 执行一次场景；执行失败只写审计（`schedule_run` / `schedule_error`），不自动重试。场景在 `homelink.db` 里维护 `enabled` / `action_groups` / `triggers`；停用的场景到点会被服务端拒绝并记审计。含高风险设备的场景在创建时必须 `confirm=true`，若之后风险升级而创建时未授权，到点会跳过并记审计。定时能力不进 MCP 工具面，避免影响工具选择准确率。

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama 地址 |
| `OLLAMA_MODEL` | `qwen3.5:9b-q4_K_M` | 需支持 tool calling |
| `HOMELINK_BASE_URL` | `http://127.0.0.1:8000` | 传给 MCP 子进程 |
| `HOMELINK_API_TOKEN` | 空 | 与服务端 token 一致 |
| `HOMELINK_LOG_LEVEL` | `INFO` | MCP 子进程 stderr JSONL 日志门槛（DEBUG/INFO/WARNING/ERROR） |
| `AGENT_MAX_STEPS` | `6` | 单轮最多几跳工具调用 |
| `AGENT_AUDIT_PATH` | `logs/agent-audit.jsonl` | 审计日志路径；设为 `off` 关闭 |
| `AGENT_DEBUG` | 未设置 | 设为 `1` 打印工具调用明细 |
| `AGENT_KEEP_ALIVE` | `30m` | 模型驻留时长（透传 Ollama `keep_alive`） |
| `AGENTD_HOST` / `AGENTD_PORT` | `127.0.0.1` / `8100` | daemon 监听地址 |
| `AGENTD_TOKEN` | 空 | 非空时除 `/api/health` 外全部要求 `Authorization: Bearer <token>` |
| `AGENTD_QUEUE_TIMEOUT` | `30`（秒） | 模型锁排队上限，超时返回 429 |
| `AGENTD_SESSION_TTL` | `120`（分钟） | 会话闲置多久后淘汰 |
| `AGENTD_MAX_SESSIONS` | `20` | 最多同时保留几个会话（LRU 淘汰） |
| `AGENTD_DB_PATH` | `data/agent.db` | 定时任务库（独立于 `homelink.db`） |

审计日志逐行记录 `turn_start` / `tool_call` / `confirm_requested` / `confirm_granted` / `confirm_denied` / `confirm_expired` / `turn_end` / `turn_error` / `schedule_created` / `schedule_deleted` / `schedule_run` / `schedule_error` / `shutdown`，每条带时间戳与参数，用于回溯「谁在什么时候让 Agent 做了什么」。

高风险设备（`risk_level=high`）由运行时兜底：模型不能自己补 `confirm=true`，只有「上一轮被拦下的同一设备 + 本轮用户明确同意」才放行；其他情况一律降级为 `confirm=false`，由服务端返回 409。

## 文档

全部在 [docs/](docs/README.md)：

- [范围](docs/scope.md)｜[架构](docs/architecture.md)｜[领域与数据](docs/domain-and-data.md)
- [API 契约](docs/api-contract.md)｜[红外网关](docs/ir-gateway.md)｜[前端契约](docs/frontend-contract.md)
- [验收清单](docs/acceptance-checklist.md)（含真实板子的验证边界）
- [开发路线图](docs/roadmap.md)（执行层 → Agent 侧车的里程碑与验收口径）
- [当前开发清单](docs/backlog.md)（下一阶段 backlog，唯一前向清单）
- [阶段执行记录](docs/roadmap-next.md)（P0–P3 已完成，已归档）
- [单 Agent MVP 交接说明](docs/handoff-mvp.md)（L3–L6 收口任务）

**验证边界**：后端 mock 全量测试；Vue 前端已构建并由后端同源托管（不做 UI 自动化测试）；真实板子（192.168.10.3）已完成只读探测、真机学码 / 保存 / 回放（2026-11-08，结果由用户人眼确认），并于 2026-11-11 补做单设备控制、场景执行与定时触发冒烟各 1 次（真实红外）。
