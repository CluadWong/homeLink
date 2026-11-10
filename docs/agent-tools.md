# Agent 工具描述与系统提示

面向上层智能管家（如本地 Ollama 模型）的接入说明：把 HomeLink 的 6 个粗粒度能力映射成 function schema，并给出可直接粘贴的系统提示。

> 对端点是 `/api/agent/*`，与 [api-contract.md](./api-contract.md) §2.10 一致。旧的 21 个 CRUD 端点**不要**暴露给模型：端点越多，选错概率越高。

## 1. 系统提示（直接粘贴）

```text
你是 HomeLink 家庭红外设备管家，负责把用户的口语指令转成对 /api/agent 的工具调用。

【设备与能力】
1. 不要凭记忆编造 device_id 或 command。操作设备前，先用 home_find_devices 或 home_get_home_snapshot 拿到真实数据。
2. 设备支持什么，只看返回的 capabilities；capabilities 里没有的动作就是做不到，直接告诉用户，不要猜、不要拼凑尝试。
3. capabilities[].command 是中文功能名（如「开关」「定时」「模式」），command_id 是稳定 ID；功能名有重名时接口会报 INVALID_REQUEST，此时改用 command_id。
4. 红外是单向发送，没有参数化控制：调用时 parameters 必须是空对象 {}，不要传亮度、温度、风速等数值。
5. online 表示「红外网关是否可达」，是整屋级信号；online=false 时不要发起控制。
6. state 是虚拟状态：控制成功后一律为 unknown，只有用户手动校正过才是 confirmed。永远不要声称「已确认打开/关闭」。

【控制与幂等】
7. request_id 由运行时自动注入并覆盖你传入的值：同一轮里同一设备同一命令的重试会自动复用同一幂等键，你不需要也不应该自己编造。
8. request_id 冲突返回 409 CONFLICT 时，换一轮新指令重发即可获得新幂等键（运行时会自动生成），不要改设备或改功能硬试。
9. risk_level=high 的设备（门锁、燃气、烤箱、取暖器等）：先把「设备 + 动作」复述给用户并得到明确同意，再用 confirm=true 调用；未确认时接口返回 409 CONFIRMATION_REQUIRED。
10. 执行包含高风险设备的场景前，先把场景内容复述给用户并征得明确同意；包含高风险设备的场景未确认时返回 409 CONFIRMATION_REQUIRED，得到同意后带 confirm=true 重试。

【场景与自动化】
11. 场景是预先建好的一组功能顺序执行，只能用 home_run_scene 按 scene_id 运行；结果 status 为 sent / partial / failed，按 results 逐条汇报失败项。
12. 不支持创建自动化或定时：home_create_automation 恒返回 501 NOT_SUPPORTED。用户要定时时，说明当前只能手动触发场景或由外部系统调度。

【错误处理】
13. 把错误码翻译成人话，并给出下一步：
    - NOT_FOUND 且提示「没有功能」：重新 home_find_devices 读 capabilities，告诉用户这台设备实际能做什么。
    - DEVICE_OFFLINE：网关不可达，提示检查红外板电源与网络，最多重试 1 次。
    - GATEWAY_TIMEOUT：信号可能已发出，不要自动重发；告知用户风险并建议现场确认。
    - EXECUTION_FAILED：网关返回异常，同样不要盲目重发。
    - CONFIRMATION_REQUIRED：按第 9 条先确认再重试。
    - INVALID_REQUEST：检查是否误传了 parameters，或功能名有重名。
14. 向用户排障时再展示 trace_id，其余情况不要主动输出。

【回答风格】
15. 控制成功后只陈述「信号已发出」，不断言设备当前真实开关状态。
16. 只回答用户问到的事，不主动罗列全部设备。
```

## 2. 工具 ↔ HTTP 映射

| 工具 | 方法 | 路径 | 说明 |
| --- | --- | --- | --- |
| `home_find_devices` | GET | `/api/agent/devices` | 支持 `room` / `type` / `name` 过滤；`name` 同时匹配设备名与别名 |
| `home_get_device_state` | GET | `/api/agent/devices/{device_id}/state` | 单设备虚拟状态 |
| `home_get_home_snapshot` | GET | `/api/agent/snapshot` | 网关状态 + 设备列表 + 场景列表，一次拿全 |
| `home_control_device` | POST | `/api/agent/control` | 回放一条已学码功能；`request_id` 幂等 |
| `home_run_scene` | POST | `/api/agent/scenes/{scene_id}/run` | 顺序执行场景内功能；包含高风险设备的场景须 `confirm=true` |
| `home_create_automation` | POST | `/api/agent/automations` | 固定 `501 NOT_SUPPORTED`，保留给未来 |

管理端专用、**不**给模型的能力：单设备详情 `GET /api/agent/devices/{id}`、元数据写入 `PUT .../metadata`、场景增删查（`GET/POST/DELETE /api/agent/scenes`）、命令结果查询 `GET /api/agent/commands/{request_id}`。这些由人、脚本或运维流程调用。

可选请求头 `X-Actor`（≤64 字符，默认 `user`），用于把调用方写进审计；模型不必感知。

## 3. Function Schema

OpenAI function-calling 格式，可直接放进 `tools` 字段；其他框架按同名字段映射即可。

```json
[
  {
    "type": "function",
    "function": {
      "name": "home_find_devices",
      "description": "按房间、设备类型或名称（含别名）检索设备。返回设备模型，包含类型、房间、别名、风险等级、在线状态、能力列表与虚拟状态。任何控制之前先调用它拿 device_id 和可用的 command。",
      "parameters": {
        "type": "object",
        "properties": {
          "room": {
            "type": "string",
            "description": "房间标识，取值以设备返回的 room 为准，例如 bedroom（卧室）、living_room（客厅）"
          },
          "device_type": {
            "type": "string",
            "enum": [
              "air_conditioner",
              "heater",
              "fan",
              "humidifier",
              "projector",
              "speaker",
              "tv",
              "light",
              "appliance"
            ],
            "description": "设备类型；不确定时省略"
          },
          "name": {
            "type": "string",
            "description": "设备名或别名，做包含匹配，例如「客厅灯」「大灯」"
          }
        },
        "additionalProperties": false
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "home_get_device_state",
      "description": "查询单台设备的虚拟状态。红外无回传，正常情况下返回 unknown；只有用户手动校正后才是 confirmed。不要把 unknown 当成关闭。",
      "parameters": {
        "type": "object",
        "properties": {
          "device_id": {
            "type": "string",
            "description": "设备 ID，必须来自 home_find_devices 或快照的返回值"
          }
        },
        "required": ["device_id"],
        "additionalProperties": false
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "home_get_home_snapshot",
      "description": "一次获取全屋快照：网关可达性与模式、设备模型列表、场景列表。适合回答「家里现在什么情况」「我睡觉了」这类整体请求，也可用来发现场景 ID。",
      "parameters": {
        "type": "object",
        "properties": {
          "room": {
            "type": "string",
            "description": "只快照某个房间；省略表示全屋"
          }
        },
        "additionalProperties": false
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "home_control_device",
      "description": "对一台设备执行一个已学码功能。command 必须来自该设备 capabilities 中的 command（或 command_id）；parameters 必须为空对象。高风险设备需 confirm=true。",
      "parameters": {
        "type": "object",
        "properties": {
          "device_id": {
            "type": "string",
            "description": "设备 ID"
          },
          "command": {
            "type": "string",
            "description": "能力名或 command_id，例如「开关」「26度」；名称重名时改用 command_id"
          },
          "parameters": {
            "type": "object",
            "description": "必须为空对象 {}。红外码是整条回放，不支持亮度、温度等参数",
            "default": {}
          },
          "request_id": {
            "type": "string",
            "maxLength": 64,
            "minLength": 1,
            "description": "幂等键。每次新指令生成新的唯一值（UUIDv4 或随机串）；仅重试同一条指令时复用"
          },
          "confirm": {
            "type": "boolean",
            "default": false,
            "description": "风险等级为 high 的设备必须由用户明确同意后传 true，否则返回 CONFIRMATION_REQUIRED"
          }
        },
        "required": ["device_id", "command", "request_id"],
        "additionalProperties": false
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "home_run_scene",
      "description": "按顺序执行一个已存在的场景。场景 ID 来自快照的 scenes 列表。返回逐项结果与整体状态（sent / partial / failed）。包含高风险设备（contains_high_risk=true）的场景必须由用户明确同意后传 confirm=true。",
      "parameters": {
        "type": "object",
        "properties": {
          "scene_id": {
            "type": "string",
            "description": "场景 ID，例如 sc_1a2b3c4d5e6f"
          },
          "confirm": {
            "type": "boolean",
            "default": false,
            "description": "场景 contains_high_risk 为 true 时必须由用户明确同意后传 true，否则返回 CONFIRMATION_REQUIRED"
          }
        },
        "required": ["scene_id"],
        "additionalProperties": false
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "home_create_automation",
      "description": "创建自动化规则。当前服务不支持，调用固定返回 501 NOT_SUPPORTED；需要定时能力时应向用户说明并改用手动触发场景。",
      "parameters": {
        "type": "object",
        "properties": {
          "rule": {
            "type": "object",
            "description": "规则草案，服务端不解析、仅用于说明意图"
          }
        },
        "additionalProperties": false
      }
    }
  }
]
```

## 4. 返回结构

成功统一为 `{success: true, result: ..., trace_id}`，失败统一为 `{success: false, error: "...", message: "...", trace_id}`。

### 4.1 设备模型（find_devices / snapshot 中的元素）

```json
{
  "device_id": "dev_56b875c8",
  "type": "fan",
  "room": "bedroom",
  "name": "风扇",
  "aliases": [],
  "risk_level": "low",
  "online": true,
  "online_scope": "gateway",
  "capabilities": [
    { "command": "开关", "command_id": "fn_c4a71e4a", "parameters": {} },
    { "command": "定时", "command_id": "fn_54a6b63c", "parameters": {} }
  ],
  "state": { "power": "unknown" },
  "state_source": "unknown",
  "state_updated_at": "2026-11-09T13:24:15Z",
  "state_readback": false
}
```

要点：

- `type` 优先取人工设置的元数据，否则按设备名推断，识别不到为 `appliance`。
- `risk_level` 为 `high` 的类型：`heater` / `oven` / `lock` / `gas`，也可人工覆盖。
- `online` 只代表网关可达（`online_scope: gateway`），不代表这台设备真的通电。
- `capabilities[].parameters` 恒为 `{}`，仅作占位；有参数即会被拒绝。

### 4.2 单设备状态（get_device_state）

```json
{
  "device_id": "dev_56b875c8",
  "state": { "power": "unknown" },
  "state_source": "unknown",
  "state_updated_at": "2026-11-09T13:24:15Z",
  "state_readback": false
}
```

### 4.3 控制结果（control_device）

```json
{
  "request_id": "3f6c1d0e-8b7a-4c2d-9f11-1a2b3c4d5e6f",
  "status": "sent",
  "replayed": false,
  "device_id": "dev_56b875c8",
  "function_id": "fn_c4a71e4a",
  "command": "开关",
  "state": "unknown",
  "message": "信号已发出",
  "trace_id": "8aea90a0"
}
```

- `status` 只有 `sent`（红外已发出）；失败走错误信封，不会返回 `failed`。
- 相同 `request_id` + 相同指令重放时 `replayed=true`，不会重复发送红外。

### 4.4 场景执行（run_scene）

场景列表（快照 `scenes`）中的每个场景附带风险聚合：`risk_level`（`low` / `high`）与 `contains_high_risk`；包含高风险设备的场景未带 `confirm=true` 会返回 409 `CONFIRMATION_REQUIRED`，不会执行任何一项。

```json
{
  "scene_id": "sc_1a2b3c4d5e6f",
  "status": "partial",
  "results": [
    { "device_id": "dev_56b875c8", "command": "开关", "status": "sent", "request_id": "scene-sc_1a2b3c4d5e6f-0-9f11ab23", "message": "信号已发出" },
    { "device_id": "dev_a1b0d435", "command": "关机", "status": "failed", "error_code": "DEVICE_OFFLINE", "message": "网关当前不可达，请检查板子电源与网络" }
  ]
}
```

### 4.5 自动化（create_automation）

```json
{
  "success": false,
  "error": "NOT_SUPPORTED",
  "message": "自动化规则暂不支持；当前只提供场景（一组功能的顺序执行）",
  "trace_id": "b1c2d3e4"
}
```

## 5. 错误码与 Agent 处置

| error | HTTP | 含义 | Agent 应该做什么 |
| --- | --- | --- | --- |
| `INVALID_REQUEST` | 400 | 参数不合法（多了字段、`parameters` 非空、功能名重名） | 修正参数；`parameters` 保持 `{}`；重名改用 `command_id` |
| `UNAUTHORIZED` | 401 | 请求 token 不匹配 | 不要重试，告知配置问题 |
| `NOT_FOUND` | 404 | 设备 / 功能 / 场景 / 请求不存在，或功能还没学码 | 重新查设备与能力，向用户说明可选动作 |
| `CONFLICT` | 409 | `request_id` 被占用或场景重名 | 换新的 `request_id` 重发 |
| `CONFIRMATION_REQUIRED` | 409 | 高风险设备或包含高风险设备的场景未确认 | 复述操作并征得同意后带 `confirm=true` 重试 |
| `DEVICE_OFFLINE` | 503 | 网关不可达 | 提示检查电源与网络；最多重试 1 次 |
| `DEVICE_UNAUTHORIZED` | 401 | 网关鉴权失败 | 告知需要修正网关凭据，不要重试 |
| `GATEWAY_TIMEOUT` | 504 | 发送超时，信号可能已发出 | 不要自动重发，提示用户现场确认 |
| `EXECUTION_FAILED` | 502 | 网关返回异常响应 | 不要自动重发，报告失败原因 |
| `NOT_SUPPORTED` | 501 | 自动化等能力未开放 | 说明当前只支持场景 |
| `INTERNAL_ERROR` | 500 | 服务内部错误 | 告知用户稍后重试，附 `trace_id` |

## 6. 典型调用序列

**A. 「把卧室风扇打开」**

1. `home_find_devices({room: "bedroom", device_type: "fan"})`
2. 从 `capabilities` 里选「开关」→ `home_control_device({device_id, command: "开关", parameters: {}})`，`request_id` 由运行时自动注入
3. 回复「信号已发出」，不承诺风扇现在一定在转。

**B. 「客厅灯调暗一点」**

1. `home_find_devices({room: "living_room", device_type: "light"})`
2. `capabilities` 里只有「开灯 / 关灯」这类整码功能，没有亮度 → 直接说明「这台灯只能整条切换，不支持调光」。

**C. 「我睡觉了」**

1. `home_get_home_snapshot({})`，查看 `scenes` 是否有「睡觉」
2. 有 → `home_run_scene({scene_id})`，按 `results` 汇报；没有 → 询问用户想关哪些设备。

**D. 高风险确认**

1. 用户「打开取暖器」→ `home_find_devices` 得到 `risk_level: "high"`
2. 先问「即将打开取暖器，确认吗？」
3. 用户同意 → `home_control_device({..., confirm: true})`

**E. 超时后重试**

1. 收到 `GATEWAY_TIMEOUT` → 不要在同一轮自动重发
2. 告知用户「信号可能已发出，请确认设备状态」；用户要求再发一次时在新一轮里重新调用，运行时会生成新的 `request_id`。

## 7. MCP 服务（homelink_mcp）

上面 6 个能力已封装成 MCP 工具，传输方式为 stdio。

```powershell
pip install -e ".[mcp]"          # mcp 是可选依赖，不影响服务端本体
python -m homelink_mcp           # 或 homelink-mcp
```

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `HOMELINK_BASE_URL` | `http://127.0.0.1:8000` | 服务地址，不含 `/api` |
| `HOMELINK_API_TOKEN` | 空 | 与服务端 `api.token` 一致；空 = 不鉴权 |
| `HOMELINK_TIMEOUT` | `10` | 单次 HTTP 超时（秒） |

用 Inspector 验证：

```powershell
npx @modelcontextprotocol/inspector
# 命令行等价：--cli "<venv>\Scripts\python.exe" -m homelink_mcp --method tools/list
```

注意：Inspector 用白名单环境启动子进程，自定义环境变量不会透传。要连非默认地址（例如 mock 实例），用一个设置变量的包装脚本（`.ps1`）作为 Inspector 的命令，不要指望直接 `set HOMELINK_BASE_URL` 生效。

工具面的错误以 MCP `isError` 返回，文本形如 `[NOT_FOUND] ... 先调用 home_find_devices ... trace_id=...`，模型可直接照着下一步行动。

