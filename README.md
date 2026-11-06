# JJ-Butler（从 0 重建版）

红外单向设备管家（控制器版）：FastAPI + SQLite 后端，配一套只作过渡的 Streamlit 前端。

> **这是从 0 重建的版本，与旧 `JJ-Butler` 代码库无关**：不带旧代码、旧数据、旧迁移链；schema 只有 `user_version = 1`。

## 安装与启动

前置：Python ≥ 3.11。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
uvicorn jj_butler.interface.api.app:app --host 0.0.0.0 --port 8000
```

- 默认 `mock` 模式：不连真实板子、不发红外。
- 前端（Vue）构建产物为 `frontend/dist`，不入 git：独立运行前需执行一次 `cd frontend && npm install && npm run build` 生成，或从已构建环境拷贝 dist 目录。构建后与 `/api` **同源**：浏览器直接打开 `http://127.0.0.1:8000/`。生产不使用 Vite dev server。
- 一键启动：`python scripts/start.py`（只拉起 uvicorn，日志写 `logs/backend.log`，Ctrl+C 不留孤儿进程）。
- 离开本仓库运行时，用环境变量 `JJ_BUTLER_CONFIG_DIR`（配置目录）与 `JJ_BUTLER_DB_PATH`（SQLite 文件）指定路径。

## 配置

配置目录默认是仓库根的 `config/`：

- `config/settings.yaml`：网关模式（`mock` / `real`）、网关地址、保留期、`api.token`、静态托管开关（`frontend.enabled` / `frontend.dist_path`）。
- `config/settings.local.yaml`：本地覆盖层（网关 Basic 用户名/密码、token），不入库。
- `config/devices.yaml`：只有房间种子；设备一律运行期通过 API 创建。

必需配置缺失或不可解析时，启动会打印**缺哪个文件、最小可用示例、文档位置**，并以非 0 退出码结束。

## 文档

全部在 [docs/](docs/README.md)：

- [范围](docs/scope.md)｜[架构](docs/architecture.md)｜[领域与数据](docs/domain-and-data.md)
- [API 契约](docs/api-contract.md)｜[红外网关](docs/ir-gateway.md)｜[前端契约](docs/frontend-contract.md)
- [验收清单](docs/acceptance-checklist.md)（含真实板子的验证边界）

**验证边界**：后端仅在 mock 下跑通全量测试；Vue 前端已构建并由后端同源托管（不做 UI 自动化测试）；真实板子只做过只读连通性确认（探测、实体读取），**从未发送真实红外**。
