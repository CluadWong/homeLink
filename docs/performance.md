# 性能与容量基线（D5）

> 记录日期：2026-11-11
> 范围：后端 mock 模式（不发送红外）+ 本机 daemon 并发语义
> 复现：`python scripts/benchmark.py --rounds 60`

## 1. 口径

| 项 | 值 |
| --- | --- |
| 机器 | Windows 10（19045），本机 SSD，Python 3.12.10 |
| 后端 | FastAPI 单进程 + SQLite，`gateway.mode=mock` |
| 测量方式 | 进程内 `TestClient`（`create_app` + 临时配置/临时库），每端点 60 次 |
| 数据规模 | 20 台设备、1 个场景（20 个动作）；场景连跑 60 次，累计 1200 条动作 |
| 分位算法 | 最近秩（recent-rank），与 `/api/metrics` 一致 |
| 隔离 | 全部临时目录，不读写 `data/`，不影响正在运行的服务 |

进程内测量不含真实网络与 uvicorn 进程间开销，绝对值会优于生产；用于**同一版本内的回归对比**与容量判断，不作为线上 SLA。

## 2. 读接口延迟

| 指标 | P50 | P95 |
| --- | --- | --- |
| `GET /api/health` | 2.4 ms | 2.9 ms |
| `GET /api/state` | 2.8 ms | 3.2 ms |
| `GET /api/devices` | 3.0 ms | 3.3 ms |
| `GET /api/agent/capabilities` | 3.1 ms | 3.6 ms |
| `GET /api/agent/scenes` | 13.6 ms | 16.3 ms |
| `GET /api/agent/scenes/{id}/runs?limit=50` | – | 23.8 ms |

读路径整体在 **几十毫秒内**。`/api/agent/scenes` 高于其他读接口，因为它要把场景与设备能力聚合后再返回；`runs?limit=50` 在 1200 条指令记录下仍是 20 ms 级。

## 3. 场景执行（20 个动作）

| 指标 | 值 |
| --- | --- |
| P50 | 616.7 ms |
| P95 | 1366.0 ms |
| 结果 | 60 次全部 `status=sent`，每次 20 条 `results` |

20 个动作在 mock 下约 **0.6–1.4 s**，瓶颈不在网关（mock 无网络），而在每个动作的 SQLite 提交：一次动作会写状态、指令记录与审计。真机模式还会叠加每动作一次 ESPHome HTTP 回放（约 300 ms/次，见 B1 的网关探测延迟），因此**真机 20 动作场景按「数秒级」预估**。

## 4. 容量上限（契约里已有的硬限制）

| 维度 | 上限 | 依据 |
| --- | --- | --- |
| 场景动作数 | `items` 1–20 | `AgentSceneCreateRequest` 校验 |
| 动作组内目标数 | 每组 1–20 | `AgentSceneActionGroup.targets` |
| 场景最近执行 | `limit` 1–50 | `GET /api/agent/scenes/{id}/runs`，`limit=51` 返回 400 |
| 审计查询 | `limit` 1–200 | `GET /api/audit` |
| 场景执行并发 | 串行（单请求内逐动作） | `run_scene` 顺序执行，尽力而为 |
| daemon 对话并发 | **1** | `daemon.py` 用 `asyncio.Lock` 串行化 9B 模型请求 |

## 5. 数据库增长

| 指标 | 值 |
| --- | --- |
| 每次 20 动作场景 | 约 10.6 KB |
| 每个动作（含状态 + 指令 + 审计） | 约 543 B |
| 60 次场景后 `command_requests` | 1200 行 |
| 60 次场景后 `audit_logs` | 1200 行 |

按 543 B/动作估算：每天 100 个动作约 54 KB/天、**约 20 MB/年**；再加上状态与审计的保留期（状态 90 天、审计 365 天、指令 180 天），长期运行不会无限增长。

注：轮数很少时单次增量可能显示为 0，这是 SQLite 页预分配造成的测量噪声，请以 60 轮的总量为准。

## 6. daemon 并发（本机实测）

环境：`homelink-agentd` @ `127.0.0.1:8100`，模型 `qwen3.5:9b-q4_K_M`。

| 场景 | 结果 |
| --- | --- |
| 单次 `/api/chat`（模型冷加载） | 52.2 s |
| 两次并发 `/api/chat`（热态） | 总墙钟 9.37 s；两个请求分别完成于 3.97 s / 9.37 s |

第二个请求等第一个跑完才开始，**证实对话并发度为 1**：9B 模型单卡驻留显存，`asyncio.Lock` 串行排队。会话级状态与 `/api/health`、`DELETE /api/sessions/{id}` 不受该锁影响。生产可用 `GET /api/metrics`（daemon）读取累计请求数、错误数与 P50/P95。

## 7. 回归阈值建议

| 指标 | 建议阈值（同机、mock、60 轮） | 超过时先查 |
| --- | --- | --- |
| 读接口 P95 | < 50 ms | 场景/设备聚合是否有 N+1 查询 |
| 场景 20 动作 P95 | < 2 s（mock） | SQLite 提交次数、审计写入 |
| `runs?limit=50` P95 | < 100 ms | `command_requests` 索引与保留策略 |
| DB 增长 | < 1 KB/动作 | 是否新增了每动作多写的表 |

## 8. 复现

```powershell
.venv\Scripts\python scripts\benchmark.py --rounds 60
```

脚本会打印 JSON 与 Markdown 表；数值随机器与磁盘状态浮动，比较时请固定机器与轮数。
