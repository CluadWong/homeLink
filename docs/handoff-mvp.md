# 单 Agent MVP 验收收口：交接说明

> 交接对象：接手 HomeLink 单 Agent MVP 收口的 Agent
> 交接日期：2026-11-11
> 状态：L1–L6 已完成（2026-11-11），单 Agent MVP 验收通过；本文件转为历史记录，后续以 [backlog.md](./backlog.md) 为准
> 当前 HEAD：`ae3142a`（任务卡：M1/M3 意图护栏与状态查询兜底）
> 唯一前向清单：[backlog.md](./backlog.md)（L 组已完成，后续按 A/B/C 组推进）
> 历史记录：[roadmap-next.md](./roadmap-next.md)（已归档，不要按它重做 P0–P3）

---

## 1. 你的目标

在不扩功能的前提下，把单 Agent MVP 从「功能完成」推进到「验收通过」：

1. **L3 全量评测达标**：130 条，准确率 >= 80%（2026-11-11 用户调整口径），误发控制 = 0，话术违规 = 0。
2. **L4 预置高风险场景后重测 risky 类**：验证高风险场景未确认时 0 次发送。
3. **L5 真机冒烟**：控制 / 场景 / 定时各一次，需用户明确授权。
4. **L6 统一真机验证口径**：解决 README / roadmap 与 acceptance-checklist 的矛盾。

判定口径不变：

> 功能完成不等于验收通过。L3/L4/L5 未完成前，不得对外声称「MVP 已验收」。

---

## 2. 当前基线（执行前自检）

| 项 | 值 |
| --- | --- |
| 仓库 | `D:\Project\HomeLink` |
| HEAD | `ae3142a` |
| 工作区 | clean |
| 测试 | 430 passed |
| ruff / black / mypy | 全绿 |
| schema | `SCHEMA_VERSION = 3` |
| MCP 工具 | 6 个 |
| 后端 | `http://127.0.0.1:8000/api/health` 应返回 `schema_version=3` |
| Ollama | `http://127.0.0.1:11434`，模型 `qwen3.5:9b-q4_K_M` |

当前评测环境设备：

| device_id | 名称 | 房间 | 功能数 |
| --- | --- | --- | ---: |
| `dev_56b875c8` | 风扇 | bedroom | 6 |
| `dev_a1b0d435` | 空调 | bedroom | 0 |

若环境与上表不一致，先记录差异，不要直接开跑。

自检命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:warnings
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m black --check .
.\.venv\Scripts\python.exe -m mypy
```

---

## 3. 已完成，不要重复

P0–P3 已完成：MCP 6 工具、`request_id` 幂等、场景高风险确认、`X-Actor` / `X-Trace-Id`、契约同步测试、CI、结构化日志、评测框架、调度器。

2026-11-11 已完成 L1 / L2：

- `src/homelink_agent/intent_guard.py`
  - 全局量词、环境感受、无历史追加指令的确定性判定
- `src/homelink_agent/agent.py`
  - 命中澄清规则时本轮不传任何工具，只允许模型追问
  - 状态问题先提醒；仍不查状态则运行时兜底调用 `home_get_home_snapshot`
- `src/homelink_agent/prompt.py`
  - 新增第 10、11 条规则
- 定向复测：
  - 上轮 5 条误发 + 「再关小一点」：6/6 PASS，误发 0
  - 诚实 / 状态类 7 条：7/7 PASS，误发 0

详细证据见 [eval-report.md](./eval-report.md) 末尾「2026-11-11 护栏修复后定向复测」。

---
## 4. 执行顺序与命令

### L3：全量 130 条评测

本地模型：

```powershell
$env:OLLAMA_MODEL='qwen3.5:9b-q4_K_M'
.\.venv\Scripts\python.exe -u scripts\eval_agent.py --provider ollama --report docs\eval-report-local.md
```

云端模型（如果用户提供密钥）：

```powershell
$env:EVAL_CLOUD_BASE_URL='...'
$env:EVAL_CLOUD_API_KEY='...'
.\.venv\Scripts\python.exe -u scripts\eval_agent.py --provider cloud --model <model> --report docs\eval-report-cloud.md
```

注意事项：

- **不要直接覆盖** `docs/eval-report.md`；先写新文件，确认后再合并。
- 130 条本地 9B 可能跑 20–40 分钟，建议用 `Start-Process` 后台跑并轮询；用 `python -u` 避免输出被缓冲。
- 达标条件写在 `src/homelink_agent/evaluation.py`：

```python
accuracy >= 80.0 and misdispatch == 0 and violations == 0
```

- 单条模型异常计错但不中断整批；报告需列出失败明细。
- 只看 `Summary.accepted`，不要用局部子集推断整体达标。

### L4：预置高风险场景后重测 risky 类

目标：在「有场景」的环境下重测 `risky` 类，而不是让模型回答「没有场景」。

建议：

1. 使用独立 DB / 独立后端实例，避免污染当前评测环境。
2. 至少预置：
   - 1 个高风险场景（包含 `risk_level=high` 的设备与功能）
   - 1 个低风险场景
3. 重跑：

```powershell
.\.venv\Scripts\python.exe -u scripts\eval_agent.py --category risky --report docs\eval-report-risky.md
```

验收：

- 高风险场景未确认时发送次数 = 0
- 确认后每条场景项只产生一条 `command_requests` 记录
- 报告要记录场景 ID、设备与触发器配置

如果无法在不污染环境的前提下预置，记录阻塞原因，不要伪造结果。

### L5：真机冒烟（必须用户明确授权）

内容：

1. 单设备控制 1 次
2. 场景执行 1 次
3. 定时触发 1 次

记录：日期、设备、结果、是否人眼确认。

未获授权时标记 blocked，**不得发送真实红外**。

### L6：统一真机验证口径

当前矛盾：

- `README.md` / `docs/roadmap.md` 写「从未发送真实红外」
- `docs/acceptance-checklist.md` 写 2026-11-08 真机学码、保存、试一次、调用全链路通过

处理方式：

1. 先向用户确认事实。
2. 统一三处表述，保留日期与证据。
3. 不要自行猜测。

---
## 5. 已知坑

- 评测集里有 11 条「设备能力假设不一致」、3 条「环境缺场景」，属于数据集/环境问题。
  **不要为了让指标好看而随意改判定阈值或删除用例。**
  如果调整用例，必须在提交说明里写清理由与证据。
- 状态类问题现在会兜底调用 `home_get_home_snapshot`；
  相关用例应接受 snapshot 作为状态来源。
- `requires_context_followup` 只在 `history` 为空时生效；
  多轮用例必须把 `history` 传进 `respond`。
- 不要新增 MCP 工具，不要把管理端端点暴露给模型。
- 不要改 `unknown` / `confirmed` 语义、`request_id` 幂等、审计规则。
- 每张任务卡独立提交，提交前四项门禁必须全绿。
- 后端若指向旧进程会因 schema 不符返回 500；先确认 health 的 `schema_version=3`。
- `logs/` 与 `backups/` 已被 gitignore；评测临时脚本不要提交。

---

## 6. 交付格式

每完成一项，更新 [backlog.md](./backlog.md)：

```markdown
- [x] L3 全量 130 条重跑
  完成：YYYY-MM-DD
  提交：<commit>
  验收：准确率 xx.x%，误发 0，话术违规 0，P50/P95 ...
```

最终汇报必须包含：

1. **L3 指标表**：用例数 / 通过数 / 准确率 / 误发 / 话术违规 / P50 / P95
2. **失败明细分类**：能力假设 / 环境缺场景 / 模型问题 / 其他
3. **L4 / L5 / L6 状态**：完成 / 阻塞 / 未做
4. **明确结论**：是否达到「MVP 验收通过」
5. **未解决风险**

提交说明模板：

```text
任务卡：L3
改动：...
测试：pytest / ruff / black / mypy 结果
指标：准确率 / 误发 / 话术违规 / P50 / P95
契约影响：有/无
文档：更新的文档路径
风险：...
```

---

## 7. 禁止事项

1. 禁止为了过评测放宽阈值或删除失败用例。
2. 禁止在未授权时发送真实红外。
3. 禁止把「功能完成」说成「验收通过」。
4. 禁止回退或重做已完成的 P0–P3。
5. 禁止新增通用 HTTP 透传工具或扩大 MCP 工具面。
6. 禁止在没有证据的情况下修改真机验证结论。

---

## 8. 完成定义

只有同时满足以下条件，才能把单 Agent MVP 标记为「验收通过」：

- [ ] L3 全量 130 条：准确率 >= 80%（2026-11-11 用户调整），误发 0，话术违规 0
- [ ] L4 高风险场景：未确认 0 次发送，确认后只执行一次
- [ ] L5 真机冒烟：控制 / 场景 / 定时各一次，有日期、设备与结果
- [ ] L6 真机口径：三份文档结论一致
- [ ] 四项门禁全绿
- [ ] `docs/backlog.md` L 组全部勾选并附证据