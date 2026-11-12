# HomeLink 开发清单（下一阶段 Backlog）

> 文档状态：活跃
> 生成日期：2026-11-11
> 当前基线：`3a5ab84`，493 passed，schema v3
> 前置文档：[roadmap-next.md](./roadmap-next.md)（已归档，仅作执行记录）
> 本文件是唯一的前向开发清单：只列「还没做完的事」，完成一项勾一项。
> **2026-11-12 范围收缩**：产品目标收敛为「浏览器文字指令 → 管家 Agent → 家电基础开关，本地部署」。已完成但不在当前口径内的能力一律**归档 / 冷却**——代码、测试、文档保留，不删除、不再扩展、不占验收；恢复条件写在对应条目里。

---

## 0. 使用说明

### 0.1 这份清单解决什么问题

`roadmap-next.md` 已经完成 P0–P3，正文里仍保留着当时的问题描述和基线数字。继续把它当前向计划使用会误导执行 Agent。

从本文件开始：

- **已完成的工作**：留在 `roadmap-next.md` 作为历史记录，不再修改
- **未完成的工作**：只在本文件维护
- **新增事项**：先加到本文件，再决定是否进入开发

### 0.2 优先级定义

| 级别 | 含义 | 处理方式 |
| --- | --- | --- |
| P0 | 文档误导、数据可能丢失、真实环境未验证 | 先做，未完成不进入下一阶段 |
| P1 | 新组件运维缺口、质量门禁缺口 | 紧随 P0 |
| P2 | 产品化与交付形态 | P0/P1 稳定后 |
| P3 | 可选能力，需先决策 | 只做 ADR，不默认开发 |

### 0.3 当前基线（执行前自检）

| 项 | 值 |
| --- | --- |
| pytest | `493 passed` |
| ruff | `All checks passed` |
| black | `128 files unchanged` |
| mypy | `Success: no issues found in 128 source files` |
| CI | `.github/workflows/ci.yml` |
| schema | `SCHEMA_VERSION = 3` |
| MCP 工具 | 6 个；`request_id` 必填；`X-Actor` / `X-Trace-Id` 已透传 |
| 场景 | 动作组 + 触发器 + 执行记录；高风险强制确认 |
| 调度器 | `src/homelink_agent/scheduler.py`，独立 `data/agent.db` |
| 评测 | 130 条，7 分类 |

若自检结果与上表不一致，先记录差异，不要直接开始任务。

---

## A. 文档与基线收口（P0，必须先做）

- [x] **A1 归档 `roadmap-next.md`**
  - 完成：2026-11-11
  - 验收：文件标题已改为「阶段路线执行记录（已归档）」，顶部指向本文件，并声明基线数字为历史快照

- [x] **A2 更新基线数字**
  - 完成：2026-11-11
  - 验收：实际自检 434 passed / ruff passed / black 116 unchanged / mypy 116 files；`docs/roadmap.md` 的 384、`docs/acceptance-checklist.md` 的 280 已更新；`README.md` 与 `docs/README.md` 无测试数字；`256/233/198 passed`、`d9adaec` 仅存在于已归档的 `roadmap-next.md`

- [x] **A3 统一真机验证口径**
  - 完成：2026-11-11（L6 统一根 `README.md`、`docs/roadmap.md`、`docs/acceptance-checklist.md`；本轮补修 `docs/README.md` 仍写「从未发送真实红外」的残留）
  - 验收：四份文档一致承认真实红外已发送，保留 2026-11-08 与 2026-11-11 的日期与证据

- [x] **A4 更新 `acceptance-checklist.md` 覆盖 P3 新面**
  - 完成：2026-11-11
  - 验收：新增 §9 共 13 条（场景动作组、逐设备失败、触发器、执行记录、高风险确认、调度器、计划 API、`/api/metrics`、`AGENTD_TOKEN`、场景前端页、agent.db 备份、失败可见性、单实例），每条带状态标记与证据；同时修正端点数为 37（21 + 16）

- [x] **A5 文档索引与链接检查**
  - 完成：2026-11-11
  - 验收：`docs/README.md` 已含本文件入口；脚本扫描根 `README.md` 与 `docs/*.md` 的全部相对链接，0 处失效

> A 组提交：`a24b825`（任务卡：A 组文档收口）

---

## B. 真实环境验证（P0，最高业务价值）

> 目标：把「mock 下通过」升级为「真机上可信」。每条都要记录日期、设备、结果、是否人眼确认。

- [x] **B1 真机只读复核**
  - 完成：2026-11-11 19:10（未发送任何红外）
  - 验收：`GET /api/gateway` → `mode=real`、`base_url=http://192.168.10.3`、`reachable=true`、`latency_ms=327`；`/api/health` → `schema_version=3`；`/api/agent/capabilities` → 6 个能力（风扇 `dev_56b875c8`）；设备 2 台（风扇、空调，均 `risk_level=low`）；`/api/state` 两台均 `unknown`
  - 备注：`command_requests` 未新增
  - 提交：`af42eef`

- [x] **B2 真机单设备控制（需用户明确同意）**
  - 完成：2026-11-12 11:27（用户明确授权发送真实红外）
  - 内容：选一个低风险设备，执行一次已学码功能
  - 验收：`status=sent`；审计有记录；用户人眼确认设备动作
  - 证据：卧室风扇 `dev_56b875c8` / 「摇头」 `fn_682f0e66`；`POST /api/agent/control`（`request_id=b2-fan-osc-20261112-01`）→ `status=sent`、`replayed=false`、`trace_id=65cdf7ea`；`command_requests` 93→94（仅 1 条）；审计 id 144 `dev_56b875c8.control` `result=success`；用户人眼确认风扇摇头

- [ ] **B3 真机场景执行（冷却）**
  - 冷却：2026-11-12。当前口径只要单设备开关；场景能力已交付并有 mock/E2E 覆盖
  - 恢复条件：需要「一键多设备」时恢复
  - 内容：2 至 3 台设备的场景，执行后查看「最近执行」
  - 验收：逐项结果正确；有失败时 `status=partial` 且明细完整

- [ ] **B4 真机定时触发（冷却）**
  - 冷却：2026-11-12。定时依赖场景触发器，与当前「文字指令 → 开关」主线无关
  - 恢复条件：需要定时开关时恢复
  - 内容：创建 1 分钟后触发的计划，等待执行
  - 验收：`last_status=ok`；只产生一组 `command_requests`；无重复发送

- [x] **B5 真机失败面（裁剪后保留）**
  - 完成：2026-11-12（由 M3 执行，证据见 M 组）
  - 内容：只做「网关不可达」与「错误网关凭据」两项；「场景含离线设备」随 B3 冷却
  - 验收：分别得到 `DEVICE_OFFLINE`、`DEVICE_UNAUTHORIZED`；`command_requests` 不新增或记 `failed`；审计一致；不自动重发

- [ ] **B6 高风险场景确认实测（冷却）**
  - 冷却：2026-11-12。当前只有 2 台低风险设备，且场景不在本期口径内
  - 恢复条件：接入取暖器 / 门锁 / 燃气等 `risk_level=high` 设备时恢复
  - 内容：含 `risk_level=high` 设备的场景，未确认时不发送
  - 验收：返回 `CONFIRMATION_REQUIRED`；前后 `command_requests` 计数不变

---
## C. P3 新组件加固（P1）

> 背景：场景 v3、调度器、执行记录都是近期新增，功能可用但运维面尚未补齐。

- [x] **C1 `data/agent.db` 纳入备份与恢复**
  - 完成：2026-11-11
  - 实现：`scripts/backup.py` 默认同时备份 `homelink.db` 与 `agent.db`（`agent-<时间戳>.db`，两前缀各自裁剪），新增 `--agent-db` / `--skip-agent`；`scripts/restore.py` 按文件名 `agent-` 自动恢复为 `AGENTD_DB_PATH` 或 `data/agent.db`
  - 测试：`tests/test_backup_restore.py` 新增 4 条（agent 备份→删库→恢复数据仍在、库不存在返回 None、两前缀裁剪互不影响、CLI 自动目标）
  - 验收：对真实运行中的库执行 `backup.py --output-dir <临时目录>`，`agent-*.db` 完整性 `ok` 且 `schedules` 表可读；单测覆盖「备份后删除 agent.db，恢复后数据仍在」；全量 438 passed
  - 提交：`af42eef`

- [x] **C2 `agent.db` schema 版本化**
  - 完成：2026-11-11
  - 实现：`scheduler.py` 引入 `AGENT_SCHEMA_VERSION = 1` 与 `PRAGMA user_version`；`v0` 旧库自动补 `source` / `trigger_id` 并升到 `v1`；版本高于当前支持时抛 `RuntimeError`（提示升级或恢复旧备份），并关闭连接
  - 测试：新增 `test_store_sets_user_version_on_create`、`test_store_rejects_newer_schema_version`；原迁移用例补充版本断言
  - 提交：`b9c7190`

- [x] **C3 调度时区显式化**
  - 完成：2026-11-11
  - 决策：不新增 `AGENTD_TZ`（Windows 下需额外 `tzdata` 包，收益低于成本）；在根 `README.md` 明确「按宿主机本地墙钟时间解释、分钟粒度、不做夏令时换算，改系统时区后需重启 daemon」
  - 测试：新增跨天/月末/闰日边界用例（`test_cron_crosses_midnight_and_month_boundaries`、`test_cron_month_end_and_leap_day`）
  - 验收：时区行为可预测，且与本机墙钟一致
  - 提交：`f0511b5`

- [x] **C4 单实例防重**
  - 完成：2026-11-11
  - 实现：新增 `src/homelink_agent/instance_lock.py`（Windows `msvcrt` / POSIX `fcntl` 文件锁）；`daemon.main()` 启动独占 `data/agent.db.lock`，第二个实例打印「已有 agentd 实例在运行」并以非 0 退出
  - 测试：`test_instance_lock_blocks_second_instance`、`test_instance_lock_release_is_idempotent`
  - 验收：进程退出自动释放锁，无陈旧锁文件；重复启动被拒绝
  - 提交：`f0511b5`

- [x] **C5 调度失败可见性**
  - 完成：2026-11-11
  - 现状：失败只写审计
  - 动作：前端明确展示 `last_status` 与失败原因；必要时本地告警
  - 实现：后端新增 `GET /api/agent/schedules`，代理 agentd 的 `GET /api/schedules`（`HOMELINK_AGENTD_URL` / `HOMELINK_AGENTD_TOKEN`，3 s 超时；不可达 → 503 `DEVICE_OFFLINE`）；前端场景页加载计划状态，卡片上显示「上次执行成功 / 失败 · 原因」，失败计划在页首汇总列出且注明不会自动重试；agentd 未启动时显示「定时状态暂不可用」，场景列表照常
  - 测试：`tests/test_agent_schedules_api.py` 覆盖代理成功与 503；`vue-tsc` 通过；全量 474 passed、0 skipped（123 files）
  - 验收：失败计划在界面上可见，不静默；端点口径 39（22 传统 + 17 Agent）/ 30 条路径
  - 提交：`56d3ee0`

- [x] **C6 数据保留策略**
  - 完成：2026-11-11
  - 实现：`config/settings.yaml` 新增 `commands.retention_days: 180`；`purge_retention` 启动时同时清理 `command_requests`；场景「最近执行」由 `command_requests` 派生，随同一保留期，不另建归档表
  - 测试：`test_retention_purges_only_rows_older_than_cutoff` 增加指令记录断言；新增 `test_retention_defaults_command_days_to_180`
  - 验收：三张表都有明确保留期（状态 90 / 审计 365 / 指令 180 天），长期运行数据库不再无限增长；口径写入 `settings.yaml` 注释
  - 提交：`65458c5`

- [x] **C7 审计隐私**
  - 完成：2026-11-11
  - 决策：不做自动脱敏（会破坏回溯价值），改为「访问与保留说明」：根 `README.md` 明确审计含用户原话、工具名与参数、确认结果，属敏感数据，只写本机、不落库、不外发
  - 保留：`rotate_logs.py` 已覆盖 `.jsonl`（默认保留 3 份）；不需要留痕时可直接删除 `logs/agent-audit.jsonl`，不影响运行
  - 提交：`65458c5`

---

## D. 质量工程（P1）

- [x] **D1 前端场景页 E2E**
  - 完成：2026-11-11
  - 实现：`tests/e2e/test_scenes_page.py` 用 Python Playwright 驱动 Chromium，真实后端（mock 网关、临时配置与临时库）+ 同源托管 `frontend/dist`；覆盖空态、新建（能力选择 + 勾设备）、编辑改名、执行结果弹层、执行记录
  - 运行方式：`pip install -e ".[dev,e2e]"` → `python -m playwright install chromium` → `cd frontend && npm run build` → `python -m pytest -m e2e`；默认 `pytest` 由 `-m "not e2e"` 排除
  - CI：`.github/workflows/ci.yml` 新增 `e2e` job（setup-node 22 + `npm ci` + `npm run build` + Playwright Chromium）
  - 验收：本地连跑 2 次通过；默认全量仍 472 passed、0 skipped；ruff/black/mypy 全绿（120 files）
  - 提交：`931cc9b`

- [x] **D2 cron 属性测试**
  - 完成：2026-11-11
  - 实现：`tests/test_scheduler.py` 新增独立参考实现（`_reference_expand` / `_reference_matches`，不复用被测 `_expand`），随机生成 `*`、单元、`a-b`、`*/n`、`a-b/n`、`a/n`、逗号列表共 7 类字段
  - 测试：新增 23 条——随机表达式 × 随机时刻 12 组各 200 点比对、3 条随机表达式扫满一周（跨月，2026-12-28 起）逐分钟比对、周日 0/7 等价、`*/n` 与 `a/n` 步长语义、9 条越界/畸形输入拒绝
  - 验收：属性测试与参考实现 0 分歧；全量 468 passed
  - 提交：`8f5a73e`

- [x] **D3 评测常态化**
  - 完成：2026-11-11
  - 内容：130 条评测定期跑（发版前或每周），默认用本机 Ollama
  - 实现：新增 `scripts/eval_sandbox.py`——自建临时 mock 后端（临时配置 + 临时库，预置「卧室风扇」6 键、「卧室空调」2 键）跑全量，随机端口、`finally` 停服，全程不发真实红外；新增 `scripts/service/run-eval.cmd`，`install-service.ps1` 注册 `HomeLinkEval`（每周日 04:00，SYSTEM，2 小时上限，`IgnoreNew`），`uninstall-service.ps1` 与 README 同步；`render_report()` 增加「## 结论」段（达标 / 未达标 + 口径）
  - 结果（Ollama `qwen3.5:9b-q4_K_M`，mock 后端，2 设备）：130 条 109 通过，准确率 83.8%，误发 1，话术违规 0，澄清率 90.3%，P50/P95 9710 / 38789 ms，模型异常 0；分类 control 17/30、honesty 11/15、risky 13/15、clarify 18/20，query / unsupported / multi_turn 全过
  - 结论：按口径判「未达标」——准确率 83.8% 达标，但误发 1。误发为 `085 我要睡了你看着办`，属 L4 已决策「放权 / 模糊指令不纳入验收、不新增护栏」的已知缺口；另有 4 条「模型没有返回内容」（026/044/055/061）。control 类偏弱与 P95 38.8 s 已如实记入报告，未调低口径或删用例
  - 验收：`docs/eval-report.md` 含模型、日期、后端地址、设备数、指标、结论、分类小计与失败明细；全量 472 passed、0 skipped；ruff/black/mypy 全绿（121 files）
  - 提交：`fcded0f`

- [x] **D4 后端可观测性补齐**
  - 完成：2026-11-11
  - 实现：新增 `src/homelink/interface/api/metrics.py`（线程安全进程内计数，错误含 4xx+5xx，耗时保留最近 1000 样本）；`app.py` 的 trace 中间件顺带记录 `/api/*`（不记 `/api/metrics` 自身），未处理异常按 500 计数
  - 接口：`GET /api/metrics`（受 Bearer 鉴权）返回 `status`、`schema_version`、`gateway.mode/base_url`、`uptime_seconds`、`requests{total,errors,error_rate,by_status}`、`latency_ms{p50,p95,samples}`；不探测网关，无网络副作用
  - 测试：`tests/test_api_common.py` 新增 4 条（健康+调用量+错误率、自身不计数、token 鉴权、5xx 与分位）；全量 472 passed
  - 文档：`api-contract.md` §2.1、`architecture.md`、`docs/README.md`、`roadmap.md`、`acceptance-checklist.md` 端点口径统一为 38（22 传统 + 16 Agent）/ 29 条路径
  - 提交：`7a937da`

- [x] **D5 性能与容量基线**
  - 完成：2026-11-11
  - 实现：新增 `scripts/benchmark.py`（mock + 进程内 TestClient + 临时库，60 轮 × 20 设备/场景），打印 JSON 与 Markdown 表
  - 结果（Windows 10 / Python 3.12.10，本机 SSD）：读接口 P95 2.9–16.3 ms；`runs?limit=50` P95 23.8 ms；场景 20 动作 P50 616.7 ms / P95 1366.0 ms，60 次全 `sent`；DB 增长约 10.6 KB/场景、543 B/动作
  - 容量上限：场景 items 1–20、动作组目标 1–20、`runs` limit 1–50、审计 limit 1–200；daemon 对话并发 1（热态两次并发墙钟 9.37 s，分别完成于 3.97 s / 9.37 s）
  - 文档：[performance.md](./performance.md)（口径、阈值建议、复现命令）；`docs/README.md` 与根 `README.md` 索引已加
  - 提交：`a49f5ca`

---
## E. 交付形态（P2）

- [x] **E1 Linux / Docker 部署**
  - 完成：2026-11-11
  - 状态：已交付，冷却（本地部署已由 Windows 计划任务满足；Docker 作为备选保留，不占验收）
  - 现状：仅 Windows 计划任务
  - 动作：Dockerfile + compose，或 systemd unit
  - 实现：`Dockerfile` 两段构建（Node 22 产出 `frontend/dist` → Python 3.12 运行镜像，`pip install ".[agent]"` 同时带 MCP / daemon）；`docker-compose.yml` 起 `backend`（8000，同源托管前端，代理调度状态到 `http://agent:8100`）与 `agent`（8100，`agentd`，Ollama 经 `host.docker.internal` / `host-gateway`），共享宿主 `config/`、`data/`、`logs/`；`.dockerignore`；README 新增「Linux / Docker 部署」；CI 新增 `docker` job（ubuntu 上构建、起双容器、健康检查 + `/api/agent/schedules` 冒烟）
  - 验收：非 Windows 环境可启动后端与 daemon —— 本机无 Docker，**未在 Windows 上实机验证**镜像运行；Dockerfile / compose 已做 YAML 解析与路径核对（`config_dir.parent` → `/app/frontend/dist` 与镜像布局一致），实际构建与启动由 CI `docker` job 在 ubuntu-latest 验证；本机全量 474 passed / 123 files
  - 提交：`6165faf`

- [x] **E2 一键部署 / 升级 / 回滚**
  - 完成：2026-11-12
  - 内容：配置迁移、数据库迁移、版本检查
  - 实现：新增 `scripts/upgrade.py`——升级前校验配置可解析（`ConfigService`）、拒绝库 schema 高于程序版本；在线快照 `homelink.db` / `agent.db` 到 `backups/`，并把 `settings*.yaml` / `devices*.yaml` 复制成 `backups/config-<时间戳>/`；复用 `Database.initialize()` 与 `ScheduleStore` 原地补表迁移，校验后写 `data/upgrade.json`（前后版本 + 快照路径）；迁移/校验失败自动还原库与配置并退出 1。`--dry-run` 只检查，`--rollback` 按版本记录还原。配置以「读时默认值」演进，升级不改写用户配置，故回滚无需配置迁移脚本
  - 验收：升级失败可回滚，数据不丢——`tests/test_upgrade.py` 覆盖成功备份+版本记录、迁移失败自动回滚（库内数据仍在、留 `.pre-restore-*`）、拒绝降级、配置非法时不动库、dry-run 不写盘、`--rollback` 还原库与配置；README 增「升级与回滚」
  - 提交：`f432608`

- [x] **E3 告警**
  - 完成：2026-11-12
  - 内容：备份失败、调度连续失败、MCP 掉线、Ollama 不可用
  - 实现：新增 `src/homelink_agent/alerts.py`——统一 `[ALERT]` stderr 行 + `logs/alerts.jsonl`，同 `kind+key` 10 分钟冷却；`Scheduler` 对同一计划连续失败 3 次告警（成功即清零，第 4 次起不重复）；daemon 在 Ollama 预热/对话失败记 `ollama_unavailable`，MCP 拉起/会话异常记 `mcp_offline`（对话分别 503/502）；`backup.py` 失败输出改为 `[ALERT] 备份失败`
  - 验收：至少有日志告警——`tests/test_alerts.py` 覆盖落盘、冷却去重、过期再记、写文件失败降级；`tests/test_scheduler.py` 覆盖连续 3 次告警与成功清零；日志落 `logs\agent.log` / `logs\alerts.jsonl` / `logs\backup.log`，README 增「告警」；本地弹窗通知未做（记为后续可选）
  - 提交：`569d2d5`

---

## M. 当前主线（2026-11-12 收缩后，P0）

> 产品口径：浏览器输入文字 → 管家 Agent → 家电服务（只做基础开关）→ 本机运行。语音、多网关、多用户、云端部署均不在本期。

- [x] **M1 界面对话入口（P0）**
  - 完成：2026-11-12
  - 现状：前端 7 个页面没有对话页；Agent 只能走 CLI 或直连 `127.0.0.1:8100/api/chat`
  - 实现：后端新增 `POST /api/agent/chat`（`AgentChatRequest`：`message` 1–2000、`session_id` ≤64），`AgentdClient.chat()` 代理 agentd `/api/chat`，代理超时 120 s；agentd 不可达 → 503 `DEVICE_OFFLINE`，429（模型排队）→ 503 透传人话，502（MCP 掉线）→ 502 `EXECUTION_FAILED`。前端新增 `ChatView.vue`（消息列表、示例指令、发送中态、错误卡片、`session_id` 存 `sessionStorage`、新对话重置），路由 `/chat` 设为默认首页，导航新增「00 管家」
  - 测试：`tests/test_agent_chat_api.py` 6 条（成功透传、无会话新建、不可达 503、429 映射、502 映射、空消息 400）；`vue-tsc` + `vite build` 通过
  - 验收：全量 **493 passed**、0 skipped；ruff / black / mypy 全绿（128 files）；端点口径 40（22 + 18）/ 31 条路径
  - 提交：`3a5ab84`

- [x] **M2 真机「开 / 关」闭环（P0）**
  - 完成：2026-11-12 17:28（用户明确授权真实红外）
  - 内容：卧室风扇「开」「关」各 1 次（B2 只验证了「摇头」）
  - 实现说明：该风扇只有一个翻转码「开关」`fn_c4a71e4a`，无独立开/关；执行方式为连按两次，最终恢复原状
  - 证据：`m2-fan-toggle-20261112-01` → `status=sent`、`trace_id=2ffcda84`，`command_requests` 94→95，审计 id 145 `success`，用户确认风扇由关变开；`m2-fan-toggle-20261112-02` → `status=sent`、`trace_id=95e8388e`，95→96，审计 id 146 `success`，用户确认风扇已停
  - 验收：各 `status=sent`、每次只新增 1 条 `command_requests`、审计 `success`、用户人眼确认、无 `replayed` 与自动重发。空调无功能码，未纳入
  - 提交：`48bbade`

- [x] **M3 失败面精简（P1）**
  - 完成：2026-11-12
  - 内容：B5 裁剪后的两项（网关不可达 / 错误网关凭据），均在真机执行
  - 证据（错误凭据）：临时把 `config/settings.local.yaml` 的 `gateway.ir.password` 改为错误值并重启 → `POST /api/learn/start` 401 `DEVICE_UNAUTHORIZED`（trace `a656beb3`，message 指向 `gateway.ir.username / gateway.ir.password`）；随后 `GET /api/learn/capture` 404，证实鉴权失败不建半开会话。恢复原配置重启后：网关 `reachable=true`（281 ms）、`learn/start` 200（trace `887c1da4`）、`learn/cancel` 200（trace `2830ed6d`）；审计 147/148 `learn.start` failed、149 success、150 cancel
  - 证据（网关不可达）：用户拔掉板子电源 → `GET /api/gateway?refresh=true` 得 `reachable=false`「探测超时（3s）」；`POST /api/agent/control`（`dev_56b875c8` / `fn_c4a71e4a`，`request_id=m3-offline-20261112-01`）→ 503 `DEVICE_OFFLINE`（trace `53ebf0fa`），命令记录 `status=failed`、`error_code=DEVICE_OFFLINE`、`replayed=false`；`command_requests` 96→97 且该 `request_id` 仅 1 行；`device_states` 未新增；传统 `POST /api/control` → 504 `GATEWAY_TIMEOUT`（trace `m3off01`），审计 id 151 `dev_56b875c8.control` failed，reason 含完整原因
  - 恢复：插回电源后探测 `reachable=true`（328 ms）；`command_requests` 仍 97、失败命令仍 `failed`（无自动重发）、审计最大 id 仍 151
  - 验收：分别得到 503 `DEVICE_OFFLINE` 与 401 `DEVICE_UNAUTHORIZED`；失败命令记 `failed` 不重发；审计一致；`device_states` 无残留
  - 提交：`64031f9`

---

## F. P4 决策（已归档 / 冷却）

> 2026-11-12：当前产品只要「本地文字指令 + 基础开关」，F 组全部冷却。以下仅保留决策入口，代码与文档不删除；恢复时按原验收口径执行。

- [ ] **F1 语音入口 spike（冷却）**
  - 冷却原因：明确本期不需要语音
  - 恢复条件：文本链路稳定使用一段时间且用户提出语音需求
  - 范围：只做「语音 → 文本 → 现有 chat」，不做 TTS
  - 技术：本地 whisper.cpp 或 sherpa-onnx
  - 验收：20 句验收 + 一份 ADR，明确是否产品化

- [ ] **F2 多网关 ADR（冷却）**
  - 冷却原因：单网关 + 本地部署已满足当前需求
  - 恢复条件：出现第二块网关或跨房间独立硬件
  - 内容：评估 v2 契约成本（配置、探测、路由、审计）
  - 验收：明确 go / no-go 与触发条件

- [ ] **F3 多用户 / 权限 ADR（冷却）**
  - 冷却原因：单用户本地部署
  - 恢复条件：需要区分家庭成员或远程访问
  - 现状：单静态 token，无用户体系
  - 验收：明确何时需要、需要哪些字段与迁移路径

- [ ] **F4 长期记忆（冷却）**
  - 冷却原因：当前无个性化需求
  - 恢复条件：用户提出偏好记忆诉求
  - 范围：先做偏好表（SQLite），不做向量库
  - 约束：写入需用户确认
  - 验收：有 schema 与隐私规则

---

## G. 依赖关系

```text
M 当前主线（P0）
  M1 界面对话入口 ──▶ M2 真机开关闭环 ──▶ M3 失败面精简
                      （M2 需用户授权真实红外）

已交付 / 冷却（不占排期）：
  A / C / D / E1–E3 已完成；B3 / B4 / B6 需场景·定时·高风险设备，随需求恢复
  F 组（语音 / 多网关 / 多用户 / 长期记忆）全部冷却
```

- **M1 先做**：主链路「用户界面发文字」目前不通，其余都是它的下游或已交付项
- **M2 需真人确认**：真实红外会改变设备物理状态，必须逐次授权并人眼确认
- **M3 与 M2 共用同一天窗口**：失败面要在真机上才有意义
- **冷却项不删除**：代码、测试、文档全部保留，恢复时按原验收口径继续

---

## H. 当前主线收尾状态

按顺序执行，三条已全部完成：

1. ~~界面对话入口：前端新增对话页 + 后端 `POST /api/agent/chat` 代理~~（M1，已完成）
2. ~~真机「开 / 关」各一次闭环验证~~（M2，已完成）
3. ~~失败面精简版：网关不可达 / 错误网关凭据~~（M3，已完成）

> 其余条目状态：A / C / D / E 已完成；B1 / B2 / B5 已完成；B3 / B4 / B6 冷却；F 组全部冷却。冷却项只有在恢复条件成立时才重新排期。
> 下一步无排期任务；若继续推进，从「界面/真机重启可用性」或用户新需求展开。

---

## I. 暂不启动

| 事项 | 原因 |
| --- | --- |
| 语音直接产品化 | 文本链路与真机场景尚未完全验证 |
| 多网关 | 单网关够用，v2 契约成本高 |
| 多 Agent | 6 个工具的单 Agent 仍最稳 |
| 向量数据库 / 长期记忆产品化 | 没有明确个性化需求 |
| Streamable HTTP | 本机 stdio 已够用 |
| Prometheus | 现有 JSON 指标够用 |

---

## J. 完成任务后的记录格式

每完成一项，在本文件勾选并补一行记录：

```text
- [x] A1 归档 roadmap-next.md
  完成：2026-11-11
  提交：<commit>
  验收：<测试或人工证据>
```

若无法完成，记录：

```text
阻塞原因：
已验证的假设：
需要谁决策：
建议的替代方案：
```

---

## K. 变更记录

| 日期 | 变更 |
| --- | --- |
| 2026-11-11 | 建立本清单；roadmap-next.md 归档为执行记录；新增 L 组并完成 L1/L2 |
| 2026-11-11 | 完成 L3（87.7%，按用户调整的 80% 口径达标）、L4（高风险确认闸门 0 误发）、L5（真机控制/场景/定时冒烟）、L6（三份文档口径统一） |
| 2026-11-11 | 完成 A1–A5：基线改为 434 passed / schema v3，真机口径四份文档统一，验收清单补 P3 新面（§9），文档链接 0 失效 |
| 2026-11-11 | 完成 B1（真机只读复核，未发红外）、C1（`agent.db` 纳入备份与恢复，基线 438 passed） |
| 2026-11-11 | 完成 C2（`agent.db` 用 `PRAGMA user_version` 版本化，基线 440 passed） |
| 2026-11-11 | 完成 C3（时区口径写入 README + 边界测试）、C4（daemon 单实例文件锁，基线 444 passed） |
| 2026-11-11 | 完成 C6（`command_requests` 保留期 180 天）、C7（审计隐私与保留说明，基线 445 passed） |
| 2026-11-11 | 完成 D2（cron 属性测试：随机表达式与参考实现比对 + 整周扫描，基线 468 passed） |
| 2026-11-11 | 完成 D4（后端 `/api/metrics`：健康、调用量、错误率、P50/P95；端点口径 38 / 29 路径，基线 472 passed） |
| 2026-11-11 | 完成 D5（性能与容量基线：`scripts/benchmark.py` + `docs/performance.md`） |
| 2026-11-11 | 完成 D1（场景页 Playwright E2E：新建/编辑/执行/结果/记录，CI 新增 e2e job；默认 pytest 仍 472 passed） |
| 2026-11-11 | 完成 D3（评测常态化：`eval_sandbox.py` 自建 mock 后端 + 每周 `HomeLinkEval` 任务；本地 qwen3.5:9b 130 条 83.8%、误发 1，报告如实判未达标；基线 472 passed / 121 files） |
| 2026-11-11 | 完成 C5（调度失败可见性：后端 `/api/agent/schedules` 代理 agentd，场景页展示上次状态与失败原因；端点口径 39 / 30 路径） |
| 2026-11-11 | 完成 E1（Linux / Docker 部署：`Dockerfile` + `docker-compose.yml` + `.dockerignore`，CI 新增 docker job 在 ubuntu 构建并冒烟双容器） |
| 2026-11-12 | 完成 E2（一键升级/回滚：`scripts/upgrade.py` 版本检查 + 库/配置快照 + 失败自动回滚 + `--rollback`；`data/upgrade.json` 记录前后版本） |
| 2026-11-12 | 完成 E3（告警：连续 3 次调度失败、Ollama 不可用、MCP 掉线写 `[ALERT]` + `logs/alerts.jsonl`，10 分钟冷却；备份失败加 `[ALERT]`） |
| 2026-11-12 | 完成 B2（真机单设备控制：卧室风扇「摇头」`status=sent`、1 条 `command_requests`、审计 `success`，用户人眼确认；未改代码，基线仍 487 passed） |
| 2026-11-12 | 范围收缩：产品口径改为「浏览器文字指令 → 管家 Agent → 基础开关，本地部署」；新增 M 组（界面对话入口 / 开关闭环 / 失败面精简）；B3 / B4 / B6 与 F 组全部冷却（代码与文档保留，写恢复条件） |
| 2026-11-12 | 完成 M1（界面对话入口：`POST /api/agent/chat` 代理 agentd、前端 `/chat` 页面与默认首页；493 passed / 128 files；端点 40 / 31 路径） |
| 2026-11-12 | 完成 M2（真机开关闭环：风扇「开关」翻转码连按两次，各自 `sent`、各 1 条 `command_requests`（94→96）、审计 145/146 `success`，用户人眼确认由关变开再停） |
| 2026-11-12 | 完成 M3（失败面精简：错误凭据 → 401 `DEVICE_UNAUTHORIZED` 且不建半开会话；物理断电 → Agent 控制 503 `DEVICE_OFFLINE`、传统 control 504 `GATEWAY_TIMEOUT`，命令记 `failed`、计数只 +1、无自动重发、`device_states` 无新增；复电后恢复在线） |
| 2026-11-12 | 回复简洁化：单设备控制成功后由运行时直接回一句话（如「风扇已关闭」，翻转码按用户说的方向措辞），不再让模型总结，省掉最后一次推理；失败/歧义仍交模型解释，同一轮问「成功了吗」改为如实说明信号已发出；系统提示新增回复风格分层（502 passed / 128 files） |

---

## L. 单 Agent MVP 验收收口（已完成，2026-11-11）

> 背景：最近一次 130 条评测为 78.5%，其中 5 次误发控制、8 条诚实类只查设备列表不查状态。本组任务只做安全与验收收口，不扩功能。

- [x] **L1 全局 / 模糊指令运行时护栏**
  - 新增 `src/homelink_agent/intent_guard.py`
  - `respond()` 命中全局量词（全部/所有/一键）或环境感受（太热了/有点闷/来点风）时，本轮不传任何工具，只允许追问
  - 无历史时，「再小一点」「那个模式」等依赖上文的追加指令也先追问
  - 测试：`tests/test_agent_tools.py` 新增护栏参数化用例与 respond 行为用例
  - 定向复测：此前 5 条误发 + 「再关小一点」共 6 条全部 PASS，误发 0

- [x] **L2 状态类问题强制查询**
  - 状态问题（开着吗/什么模式/几档/成功了吗/发对了吗）第一次只查设备列表就回答时，追加提醒
  - 第二次仍不查状态，运行时兜底调用 `home_get_home_snapshot`，保证回答有数据依据
  - 定向复测：7 条诚实类/状态类用例全部 PASS，误发 0
  - 评测用例 `我开的那个码发对了吗` 接受 `home_get_home_snapshot`（快照本身就是状态来源）

- [x] **L3 全量 130 条重跑**
  - 用目标模型（本地 `qwen3.5:9b-q4_K_M` 或明确云端模型）重跑
  - 验收：工具准确率 >= 80%（2026-11-11 用户调整口径），误发控制 = 0，话术违规 = 0
  - 完成：2026-11-11
  - 提交：`3b13f84`（评测报告）、`7dde41c`（验收口径调整）
  - 结果：130 条，114 通过，准确率 87.7%，误发 0，话术违规 0，澄清率 88.6%，P50/P95 10614/24151 ms，模型异常 0 → **达标**
  - 证据：[eval-report-local.md](./eval-report-local.md)
  - 失败分类（16 条）：设备能力假设 4、工具面缺口 1、模型过度澄清/未查状态 6、场景未确认直接执行 2、判定口径 3
  - 说明：本轮未删除用例；口径调整由用户明确指示（80%），代码同步修改 `Summary.accepted`

- [x] **L4 评测环境预置高风险场景**
  - 固定预置 1 个高风险场景、1 个低风险场景，重测 risky 类
  - 验收：高风险场景未确认 0 次发送
  - 完成：2026-11-11
  - 提交：`4b5401d`
  - 验收：独立 mock 实例（8020 + `data/l4`）预置 `sc_f8ce3e9c3aea` 睡觉（high，关热水器 `dev_e6059fd0`）、`sc_c5d00efc441f` 回家、`sc_d04392d0591a` 离家（low）；未确认 HTTP 409 `CONFIRMATION_REQUIRED` 且 `command_requests` 46→46；模型自主尝试执行高风险场景 0 次发送（高危设备行数 0）；`confirm=true` 后恰好新增 1 条 `scene-sc_f8ce3e9c3aea-0-d86241a6`（trace `82fa44a7`）
  - 证据：[eval-report-risky.md](./eval-report-risky.md)
  - 风险：低风险场景被模型直接执行属设计内行为，但与用例「所有场景先追问」的口径冲突，risky 类 66.7% 未达标
  - 口径调整（2026-11-11，用户决定）：低风险场景允许直接执行（高风险仍由 409 闸门拦截）；`运行回家模式` / `执行离家场景` / `把我设好的场景都跑一遍` / `跑一下睡前模式` 改为 `expect_any`，不再要求追问；追问标记补充 `请回复` / `请具体` 等
  - 真实环境重跑（已获用户授权）：第 1 轮 11/15 = 73.3%、误发 0；第 2 轮 10/15 = 66.7%、误发 1（`我要睡了你看着办` 直接发控制）
  - 用户决策：请求用户提供更明确的指令，暂时假设用户只会提相对明确的指令；放权/模糊指令不纳入本轮验收，也不新增护栏，用例保留为已知缺口
  - 证据：[eval-report-risky-real.md](./eval-report-risky-real.md)（含两轮对照与 8 次真实发送记录）

- [x] **L5 真机冒烟**
  - 单设备控制、场景执行、定时触发各一次，记录证据
  - 完成：2026-11-11（用户明确授权，仅低风险风扇 `dev_56b875c8`）
  - 提交：`e3f8bad`
  - 控制：18:11:58 发「开关」，`request_id=l5-control-20261111`，`status=sent`，trace `01f7c9de`
  - 场景：18:12:02 执行「离家」`sc_d04392d0591a`，`status=sent`，`scene-sc_d04392d0591a-0-0596858f`，trace `0444068a`
  - 定时：18:25:17 由 schedule 触发器（`cron="25 18 * * *"`）自动执行「离家」，`runs` 新增 trace `d8c7b283`，仅产生 1 条 `command_requests`（`scene-sc_d04392d0591a-0-6490f7e9`）；测试后已移除该触发器
  - 计数：`command_requests` 82 → 85，恰好 3 条，均为 `sent`
  - 人眼确认：已确认——2026-11-11 晚用户回述「风扇已启动」，确认真实红外生效
  - 证据：`data/homelink.db` 的 `command_requests`、`GET /api/agent/scenes/sc_d04392d0591a/runs`、`logs/agent-audit.jsonl`（18:13:56 `schedule_synced`）

- [x] **L6 真机验证口径统一**
  - 解决 `README.md` / `docs/roadmap.md` 与 `docs/acceptance-checklist.md` 的矛盾
  - 完成：2026-11-11
  - 提交：`e3f8bad`
  - 事实依据：用户确认 2026-11-08 确实做过真机学码与回放
  - 修改：`README.md` 验证边界改为「已完成只读探测、真机学码/保存/回放（2026-11-08，人眼确认）+ 2026-11-11 控制/场景/定时冒烟」；`docs/roadmap.md` 原本已是「真实板子学码/回放已验收」，无需改；`docs/acceptance-checklist.md` 补记 2026-11-11 冒烟
  - 验收：三份文档结论一致，均承认真实红外已发送

**L 组完成定义**：L3 达标 + L5 有证据 + L6 口径一致，才可把「单 Agent MVP」标记为验收通过。

**L 组状态（2026-11-11）：三项均满足 → 单 Agent MVP 验收通过。** 遗留项不阻塞验收：
risky 类真实环境 66.7%（低风险场景直接执行已改为合规；放权/模糊指令按用户决定暂不纳入）、6 条模型过度澄清/未查状态、模型偶发空回复。
