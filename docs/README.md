# HomeLink

红外单向设备管家（控制器版）的后端服务与过渡前端：所有设备在运行期创建，学码后按功能回放，并维护一份带可信度等级的虚拟状态。

## 是什么

- 后端 FastAPI + SQLite，33 个 `/api` 端点（21 传统 + 12 Agent）；schema `SCHEMA_VERSION = 2`。正式前端是 `frontend/`（Vue，构建产物 `frontend/dist`），生产由后端**同源托管**；`interface/clients/streamlit/` 是旧的过渡六页，已不参与启动流程。
- 硬件是一台 ESPHome 红外网关；`mock` 模式无需任何硬件即可跑通「建设备 → 学码 → 调用」（学码默认 `no_signal`，可显式注入固定波形）。没有内置功能、能力或策略；传感器本期不接入。

## 启动（最小可复现）

前置：Python ≥ 3.11；只需要一个 SQLite 文件，无数据库/中间件依赖。

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1        # Windows PowerShell；macOS/Linux: source .venv/bin/activate
pip install -e .                  # 后端运行时依赖（开发/测试再装 requirements.txt）
uvicorn homelink.interface.api.app:app --host 0.0.0.0 --port 8000
```

- 工作目录为仓库根即可；包是标准 src-layout（`src/homelink/...`），`src/` 本身不是包。
- 默认配置目录 `./config`、默认库 `./data/homelink.db`；离开仓库时用环境变量指定：
  `HOMELINK_CONFIG_DIR`（配置目录）、`HOMELINK_DB_PATH`（SQLite 文件路径）。
- 一键启动：`python scripts/start.py`（只拉起 uvicorn，默认 8000）。日志写 `logs/backend.log`；Ctrl+C 会终止后端，不留孤儿进程。前端构建：`cd frontend && npm install && npm run build`。
- 前端页面与 `/api` **同源**：dist 存在时 `GET /` 直接打开页面（history 路由回退 `index.html`），`/api/*` 的 404 仍是 JSON 错误信封。**生产不使用 Vite dev server**。
- `frontend-contract.md` 的 Vue 前端已交付并在 `frontend/dist` 构建通过（本仓库不做 UI 自动化测试）；Streamlit 六页代码保留，但已不是启动入口。
- mock 模式下无需硬件即可跑通「建设备 → 学码 → 调用」：默认学码恒 `no_signal`，只有显式调用进程内 `inject_test_waveform()` 才返回固定波形。

## 配置在哪

| 文件 / 键 | 管什么 |
| --- | --- |
| `config/settings.yaml` | `gateway.mode`（`mock`/`real`）、`gateway.ir.base_url`、`gateway.probe_timeout_seconds`、`state.retention_days`、`audit.retention_days`、`api.token`、`frontend.enabled`、`frontend.dist_path` |
| `config/devices.yaml` | **只有房间种子**（名称 / 显示名 / 排序） |
| `config/settings.local.yaml` | 本地覆盖层（`gateway.ir.username` / `password`、`api.token`、`gateway.ir.base_url`），**不入库** |

- **mock/real 切换**：改 `gateway.mode` 后**重启**；mock 不发任何网络请求，real 直连 ESPHome `web_server`。
- **token**：`api.token` 为空 = 完全不校验；非空则所有 `/api/*` 端点要求 `Authorization: Bearer <token>`（`/docs` 等文档页与同源静态页面不受限）。只改 `settings.local.yaml` 并重启，没有写 token 的 API。
- **网关地址**：只能用 `PUT /api/gateway` 写本地覆盖层；返回 `restart_required`，`GET /api/gateway` 会回显新地址并标 `pending_restart=true` 直到重启。
- **静态托管开关**：`frontend.enabled`（默认 `true`）与 `frontend.dist_path`（默认 `frontend/dist`，相对配置目录的上一级解析）。关闭 = 纯 API；开启但缺 `dist/index.html` 时，非 API 路径返回 503 并提示先构建，`/api` 不受影响。
- 写入必须经 `ConfigService`；鉴权值没有 API，只能改本地覆盖层并重启；房间种子同样需重启。
- **当前验证边界**：后端只在 mock 下跑通全量测试；真实板子只做过**只读连通性确认**（探测、`ir_last_code` / `ir_learn_mode` 读取），**从未发送真实红外**，波形载荷与学码联动仍未知，见 `acceptance-checklist.md`。
- **配置缺失/损坏**：必需文件不存在或 YAML 解析失败时，启动会打印文件全路径、最小可用示例与文档位置，并以非 0 退出码结束（命令行与 `create_app_from_env` 同一段提示）。

## 文档索引

[scope.md](scope.md)（范围）· [architecture.md](architecture.md)（架构）· [domain-and-data.md](domain-and-data.md)（领域与数据）· [api-contract.md](api-contract.md)（API 契约）· [agent-tools.md](agent-tools.md)（Agent 工具描述与系统提示）· [ir-gateway.md](ir-gateway.md)（红外网关）· [frontend-contract.md](frontend-contract.md)（前端契约）· [acceptance-checklist.md](acceptance-checklist.md)（验收清单）· [roadmap.md](roadmap.md)（开发路线图）。
