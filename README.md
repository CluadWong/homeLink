# HomeLink（从 0 重建版）

红外单向设备管家（控制器版）：FastAPI + SQLite 后端，配 Vue 3 + Vite 正式前端（`frontend/`，构建产物由后端同源托管）；`interface/clients/streamlit/` 是保留的旧过渡代码，已不是启动入口。

> **这是从 0 重建的版本，与旧 `JJ-Butler` 代码库无关**：不带旧代码、旧数据、旧迁移链；schema 只有 `user_version = 1`。

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

安装后有两个任务：

| 任务 | 触发 | 行为 |
| --- | --- | --- |
| `HomeLinkService` | 开机（以 SYSTEM 运行，无需登录） | 后台静默执行 `scripts\service\run-service.cmd`，启动输出含访问地址，写 `logs\service.log` |
| `HomeLinkAddresses` | 用户登录 | 弹出终端执行 `scripts\service\show-addresses.cmd`，只显示当前访问地址，不重复启动服务 |

- 安装脚本会**停用**旧项目的 `JJButlerService`（只停用不删除）；不想动旧任务时加 `-KeepOldService`。
- 卸载后旧任务保持停用；需要恢复时执行 `uninstall-service.ps1 -RestoreOldService`。
- 只想立刻看地址：运行 `scripts\service\show-addresses.cmd`（或 `python scripts\addresses.py`）。


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

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama 地址 |
| `OLLAMA_MODEL` | `qwen3.5:9b-q4_K_M` | 需支持 tool calling |
| `HOMELINK_BASE_URL` | `http://127.0.0.1:8000` | 传给 MCP 子进程 |
| `HOMELINK_API_TOKEN` | 空 | 与服务端 token 一致 |
| `AGENT_MAX_STEPS` | `6` | 单轮最多几跳工具调用 |
| `AGENT_AUDIT_PATH` | `logs/agent-audit.jsonl` | 审计日志路径；设为 `off` 关闭 |
| `AGENT_DEBUG` | 未设置 | 设为 `1` 打印工具调用明细 |

审计日志逐行记录 `turn_start` / `tool_call` / `confirm_downgraded` / `turn_end` / `turn_error`，每条带时间戳与参数，用于回溯「谁在什么时候让 Agent 做了什么」。

高风险设备（`risk_level=high`）由运行时兜底：模型不能自己补 `confirm=true`，只有「上一轮被拦下的同一设备 + 本轮用户明确同意」才放行；其他情况一律降级为 `confirm=false`，由服务端返回 409。

## 文档

全部在 [docs/](docs/README.md)：

- [范围](docs/scope.md)｜[架构](docs/architecture.md)｜[领域与数据](docs/domain-and-data.md)
- [API 契约](docs/api-contract.md)｜[红外网关](docs/ir-gateway.md)｜[前端契约](docs/frontend-contract.md)
- [验收清单](docs/acceptance-checklist.md)（含真实板子的验证边界）
- [开发路线图](docs/roadmap.md)（执行层 → Agent 侧车的里程碑与验收口径）

**验证边界**：后端仅在 mock 下跑通全量测试；Vue 前端已构建并由后端同源托管（不做 UI 自动化测试）；真实板子只做过只读连通性确认（探测、实体读取），**从未发送真实红外**。
