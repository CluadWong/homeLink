# API 契约

## 1. 通用规则

- 全部端点前缀 `/api`，请求与响应均为 UTF-8 JSON；无文件上传、无表单。
- 鉴权：`Authorization: Bearer <token>`。`api.token` 为空（仓库默认）时**完全不校验**；非空时所有 `/api/*` 端点一律校验，失败 401 `UNAUTHORIZED`（`/docs`、`/redoc`、`/openapi.json` 与同源静态页面不受限）。鉴权值只能改 `settings.local.yaml` 并重启，**没有写 API**。前端 token 来源（`VITE_API_TOKEN` 注入或 sessionStorage 当次输入）见 `frontend-contract.md` §8；本接口只校验请求头。
- 时间一律 ISO 8601 UTC，不带本地时区。
- `trace_id`：请求可带 `X-Trace-Id`，缺失时服务端生成 8 位十六进制。除 `GET /api/state` 与 `POST /api/correct` 外，所有响应都回带 `trace_id`。
- 错误响应只有一个形状：`{"success": false, "error": "<CODE>", "message": "...", "trace_id": "..."}`。
- 请求体严格：出现未声明字段即 400 `INVALID_REQUEST`；`INVALID_REQUEST` 只用于请求字段本身的问题（缺失、未声明、必填为空），不用于业务取值判断。
- 幂等：`GET` 无副作用；`PUT /api/gateway` 只写本地覆盖层；`cancel` 可重复调用；`save` **不幂等**（允许重名）。
- `message` 是给人看的一句话，可带具体原因；不得出现内部模块名、级别或堆栈。`result` 是自由对象，前端按语义解读，不依赖固定键。
- 单位固定：时长秒、波形微秒，不带后缀。
- `/api/state` 没有 `success` 字段，按 HTTP 200 判定；其余端点成功时 `success=true`。
- 列表上限：审计默认 50、上限 200；设备与房间全量返回，不保证顺序。

### 状态码映射

| HTTP | 含义 |
| --- | --- |
| 200 | 成功；含 `captured=false`、`receiver_stopped=false` 这类业务结果 |
| 400 | 请求体字段缺失、未声明字段（`INVALID_REQUEST`） |
| 401 | `UNAUTHORIZED`（前端 token）或 `DEVICE_UNAUTHORIZED`（板子拒绝凭证） |
| 404 | `NOT_FOUND`（设备、房间、功能或码不存在） |
| 409 | `CONFLICT`（房间重名、非空房间、request_id 复用）或 `CONFIRMATION_REQUIRED`（高风险设备未确认） |
| 500 | `INTERNAL_ERROR`（不回栈） |
| 501 | `NOT_SUPPORTED`（自动化规则） |
| 502 | `EXECUTION_FAILED`（网关返回异常响应） |
| 503 | `DEVICE_OFFLINE`（发送/学码时连接失败） |
| 504 | `GATEWAY_TIMEOUT`（连接超时） |

### 错误码全表（重建版）

| 码 | 状态 | 含义 |
| --- | --- | --- |
| `INVALID_REQUEST` | 400 | 请求体字段缺失或出现未声明字段 |
| `UNAUTHORIZED` | 401 | 前端 token 缺失或不匹配 |
| `NOT_FOUND` | 404 | 设备 / 房间 / 功能 / 码不存在 |
| `CONFLICT` | 409 | 房间重名 / 非空房间 / request_id 已用于其他命令 |
| `DEVICE_OFFLINE` | 503 | 设备无响应 / 连接失败 |
| `DEVICE_UNAUTHORIZED` | 401 | 板子在线但鉴权被拒 |
| `GATEWAY_TIMEOUT` | 504 | 连接超时 |
| `EXECUTION_FAILED` | 502 | 网关返回无法识别的响应 |
| `CONFIRMATION_REQUIRED` | 409 | 高风险设备未带 confirm=true |
| `NOT_SUPPORTED` | 501 | 自动化规则本期不支持 |
| `INTERNAL_ERROR` | 500 | 内部错误 |

重建版清退 `POLICY_DENIED`、`FORBIDDEN`、`TIMEOUT`、`MODEL_UNAVAILABLE`、`FALLBACK_EXHAUSTED`、`REDACT_FAILED`、`BUDGET_EXCEEDED`、`P3_NOT_ALLOWED`；**当前代码仍保留这些码**，重建时一次删掉。

**`AGENT_UNREACHABLE` 是客户端展示码，不属于上表 11 个服务端错误码**：服务端从不返回、也不接受请求携带该值；前端仅在请求未到达后端（网络不可达、超时）时本地生成，用于与 `DEVICE_OFFLINE`（后端正常、板子不通）区分。

### 1.1 静态托管（同源）

`frontend.enabled`（默认 `true`）+ `frontend.dist_path`（默认 `frontend/dist`，相对配置目录的上一级解析）控制生产托管：

- dist 内存在 `index.html` 时：`GET /` 与任何未匹配的**非 `/api`** 路径都返回 `index.html`（history 路由回退）；dist 根下的真实文件与 `/assets/*` 按文件返回。
- **`/api/*` 永远不会落到 `index.html`**：未匹配的 `/api/*` 仍返回 404 JSON 错误信封（`NOT_FOUND`），`/api` 本身同理。
- `frontend.enabled=false`：不挂静态路由，服务是纯 API。
- 开启但缺 `dist/index.html`：启动日志打 WARNING，非 API 路径返回 **503 纯文本**「前端未构建…请先 `npm run build`」，`/api` 不受影响（**不是 500**）。
- 生产**不使用 Vite dev server**：页面与 API 同源同端口，构建产物由后端直接提供；静态页面与 `/docs` 不受 token 约束。

## 2. 端点清单（33 个）

### 2.1 健康检查

**`GET /api/health`** — 存活探针，恒 200；同样受 Bearer 鉴权约束。
成功：`{success:true,status:"ok",schema_version:1,trace_id}`。无请求参数、无副作用；`schema_version` 与 `SCHEMA_VERSION` 同值。

### 2.2 状态

**`GET /api/state`** — 全部设备状态与传感器占位。
成功：`{devices:[{id,name,state,state_source,updated_at}], sensors:{available,temperature,humidity,presence}}`。
`state_source` 只有 `unknown` / `confirmed`；传感器本期不接入：`available=false`，三项读数恒 `null`（**不是 0**）。无筛选参数、无 `trace_id`。

### 2.3 设备（6）

**`GET /api/devices?room=`** — 设备列表。`room` 可选。
成功：`{devices:[...], trace_id}`；设备字段 `id,name,room,adapter,notes,extra,functions[]`；功能字段只有 `id,name`。

**`POST /api/devices`** — 新增设备。
请求：`{name,room,notes?}`；**无 id、无 adapter**，服务端生成 `dev_*`，`adapter` 恒 `ir`。
- 成功：`DeviceDetail`，带 `trace_id`。错误：400 未声明字段；404 房间不存在。
- 设备只在这里创建；学码不创建设备，也不修改设备字段。

**`GET /api/devices/{id}`** — 详情，含功能列表。错误：404。

**`PATCH /api/devices/{id}`** — 只接受 `name` / `room` / `notes`；其它字段一律 400。
错误：400 未声明字段；404 设备或房间不存在。

**`DELETE /api/devices/{id}`** — 级联删功能与码。错误：404。设备全部可删。

**`DELETE /api/devices/{id}/functions/{function_id}`** — 删除学码功能，级联删码。
成功：`{success,deleted:{function_id,codes},trace_id}`，`deleted.codes` 为被级联删除的码条数；错误：404。

### 2.4 房间（4）

**`GET /api/rooms`** — `{rooms:[{name,display_name,sort_order,device_count}], trace_id}`。
**`POST /api/rooms`** — `{name,display_name,sort_order?}`；409 重名。
**`PATCH /api/rooms/{name}`** — 改 `name/display_name/sort_order`；重命名同步设备 `room`；404 / 409。
**`DELETE /api/rooms/{name}`** — 空房间才可删；409 / 404。

房间 `name` 的字符规则由前端派生保证（`[a-z0-9_]`、唯一，见 `frontend-contract.md` §2）；服务端只校验必填与重名 409，接口本身不限制字符集。

### 2.5 学码（4）

**`POST /api/learn/start`** — `{device_id}`。**只开会话，不创建资源**；设备必须已在设备页存在。成功 `{success,device_id,message,trace_id}`；错误 404 设备不存在、503 网关开不起接收态、**401 `DEVICE_UNAUTHORIZED` 板子鉴权被拒**（此时会话不建立，message 会指明 `gateway.ir.username / gateway.ir.password`）。

**`GET /api/learn/capture?device_id=`** — **会话已存在**时，除“板子拒绝凭证”外恒 200。成功 `{captured,device_id,raw_code,message,reason,trace_id}`；`captured=false` 不是错误；`reason` 三值 `no_signal` / `malformed` / `gateway_unreachable`；`malformed` 时 `message` 会带「（空值）/（格式不认识）/（长度不足）」细分，便于定位。轮询期间网关连不上时**仍是 200**，用 `reason=gateway_unreachable` 表达，不升级为错误。**会话已存在但轮询读波形时板子拒绝凭证 → 401 `DEVICE_UNAUTHORIZED`。没有活动会话（未 `start`、`start` 因鉴权失败未建立、已 `save` / `cancel` 或已被 TTL 回收）→ 404 `NOT_FOUND`「学码会话不存在或已结束」**。

- 设计意图：**鉴权错误在 `start` 阶段暴露，不在每秒轮询里反复报**。`start` 需要 `turn_on` 学习模式，鉴权失败即 401 且不建会话；客户端因此看到的是“开始失败”而不是“轮询失败”，一次即可定位到 `settings.local.yaml` 的用户名/密码，也避免留下一个永远读不到波形却仍在轮询的半开会话。

**`POST /api/learn/save`** — `{device_id,name,raw_code}`。`name` 非空且 **≤ 40 字符**（与前端输入框上限一致）。成功 `{function_id,name,receiver_stopped,receiver_error,trace_id}`；先落库再关接收，关失败不回滚码。错误 400 空波形 / 空名 / 超长名 / 404 / 500。

**`POST /api/learn/cancel`** — `{device_id}`，幂等。成功 `{success,device_id,message,trace_id}`；只停接收态，不删除设备。

### 2.6 控制（1）

**`POST /api/control`** — `{function_id}`。
只接受 `function_id`；设备由功能归属推出，**没有 `device_id`、`action`、`params`**，也没有参数校验。
成功：`{success,result,trace_id}`，其中 `result` 固定为 `{function_id,device_id,state,message}`（内部键冻结；`state` 恒 `unknown`，`message` 为「信号已发出」）。错误：400 字段缺失/未声明；404 功能不存在或该功能还没有码；503 发送失败。

### 2.7 校正（1）

**`POST /api/correct`** — `{device_id,state,reason?}`。
`state` 是**非空自由字符串**（建议 `on` / `off` / `unknown`），不做枚举校验；空串按字段缺失 400，长度上限 **64**（与 `device_states.state` 的 CHECK 一致）。`reason` 可省略，上限 **128**。
成功：`{success,device_id,state,state_source}`，`state_source` 恒 `confirmed`，无 `trace_id`。错误：404 设备不存在。

### 2.8 网关（2）

**`GET /api/gateway?refresh=`** — 只读探测，无副作用；mock 模式也返回。
成功：`{success,mode,base_url,reachable,checked_at,latency_ms,error,last_success_at,simulated,pending_restart,trace_id}`。
`base_url` 表示**当前配置值**：`PUT` 之后即使尚未重启，也返回已保存的新地址，并用 `pending_restart=true` 标明「已保存、未生效」；重启后该值自然变为有效地址且 `pending_restart=false`。**`pending_restart=true` 时 `reachable` / `latency_ms` / `error` 仍是对旧地址的探测结果**（探测链路只在重启后切换），不能用来判断新地址是否可用。mock 模式默认 `base_url` 为空串、`simulated=true`。探测结果缓存 **15 秒**；`refresh=true` 绕过缓存。探测只影响展示，**不是控制前置条件**。

**`PUT /api/gateway`** — `{base_url}`，写 `settings.local.yaml` 并落 `config_audit`。
`base_url` 必须是 **`http` / `https` 且 host 非空**的地址；非法（含空串、`ftp://`、无 host）一律 400 `INVALID_REQUEST`。
成功：`{success,mode,base_url,restart_required,message,trace_id}`。鉴权值没有写 API。

### 2.9 审计（1）

**`GET /api/audit?limit=&result=&device_id=`** — 按写入倒序。
成功：`{records:[{created_at,action,device_id,result,reason,trace_id}], trace_id}`。
`limit` 1–200（默认 50）；`result` 三值 `success` / `failed` / `all`（`all` 或缺省表示不筛选）；`device_id` 按 `audit_logs.device_id` **精确匹配**（空值记录不参与）。非法 → 400。

### 2.10 Agent 能力（12）

前缀 `/api/agent`，成功统一返回 `{success, result, trace_id}`。请求头可选 `X-Actor`（≤ 64 字符，默认 `user`），只用于记录调用方，不参与鉴权。

| 能力 | 端点 |
| --- | --- |
| 查设备 | `GET /api/agent/devices?room=&type=&name=` |
| 查单个设备 | `GET /api/agent/devices/{device_id}` |
| 查状态 | `GET /api/agent/devices/{device_id}/state` |
| 家庭快照 | `GET /api/agent/snapshot?room=` |
| 控设备 | `POST /api/agent/control` |
| 查命令结果 | `GET /api/agent/commands/{request_id}` |
| 场景列表 | `GET /api/agent/scenes` |
| 创建场景 | `POST /api/agent/scenes` |
| 删除场景 | `DELETE /api/agent/scenes/{scene_id}` |
| 跑场景 | `POST /api/agent/scenes/{scene_id}/run` |
| 设备元数据 | `PUT /api/agent/devices/{device_id}/metadata` |
| 自动化（未支持） | `POST /api/agent/automations` → 501 |

- 设备模型：`device_id / type / room / name / aliases / risk_level / online / online_scope / capabilities / state / state_source / state_updated_at / state_readback`。
- `online` 取网关探测结果，同一网关下所有设备一致，`online_scope` 恒为 `gateway`；不承诺逐设备在线。
- `capabilities` 只列已学码功能，形状 `{command, command_id, parameters:{}}`；红外一条码即一个完整动作，**不支持连续参数**。
- `state` 是虚拟状态：控制成功写 `unknown`，只有校正可写 `confirmed`；`state_readback` 恒为 `false`。
- `type / aliases / risk_level` 存于 `devices.extra`；`type` 缺省按设备名推断，`risk_level` 缺省按类型推断（取暖器/烤箱/门锁/燃气为 `high`）。
- `control`：`command` 可传 `command_id` 或功能名（精确匹配）；`parameters` 必须为空对象；`request_id` 1–64 字符，作为幂等键。
- 同一 `request_id` 重放：原命令成功 → 200 且 `replayed=true`；原命令失败 → 返回原错误码；`device_id/function_id/parameters` 不一致 → 409 `CONFLICT`。
- 高风险设备未带 `confirm=true` → 409 `CONFIRMATION_REQUIRED`；Agent 必须先复述并取得用户确认。
- 网关不可达 → 503 `DEVICE_OFFLINE` 且命令记为 `failed`；超时 → 504；网关响应异常 → 502。
- 场景：`items` 1–20 条，创建时即解析功能；运行逐条发送并返回 `results`，整体 `status` 为 `sent / partial / failed`。场景不引入触发条件与调度。
- 自动化规则本期不实现，固定 501 `NOT_SUPPORTED`。

## 3. 学码契约

- 设备由设备页（`POST /api/devices`）创建；`start` 只在已有设备上开会话，不创建、不修改设备。
- 功能名由用户在**开始学码前**确定；`save` 直接沿用该名字，不要求捕获后二次输入。`name` 非空且 ≤ 40 字符，空白名或超长名一律 400 `INVALID_REQUEST`。
- `captured=false` 是 200 的正常结果（用户还没按键）；唯一例外是 401 鉴权失败。
- `reason` 三值：`no_signal` / `malformed` / `gateway_unreachable`，只解释「为什么没捕获」。
- `save` 原子：校验与落库通过后**会话即结束**，再关接收；关失败仍 200，用 `receiver_stopped=false` + `receiver_error` 告知。此时 `capture` 已 404，用户直接重新 `start` 即可（会再次下发 `turn_on`）；`cancel` 仍是幂等的兜底，可在板子恢复后重试关闭接收态。板子断电恢复后 `ir_learn_mode` 的实际取值、以及再次 `turn_on` 能否覆盖残留接收态，待真机验证。
- `capture` 的“除鉴权失败外恒 200”只约束业务结果，不承诺网络与 5xx 永不发生：出现网络中断、超时或 5xx 时，前端按 `frontend-contract.md` 的学码异常规则停止轮询、尽力 `cancel` 并展示人话，服务端不新增语义。
- `cancel` 只停接收态并结束会话，**不删除设备**；设备生命周期完全由设备页的增删改管理。
- `save` 只落功能与码，不改设备其他字段。
- 空闲 TTL 到期由服务端自生成 `trace_id`（原请求链已断），只停接收态、不动设备；审计固定 `who=system`、`action=learn.idle_timeout`，与用户主动 `cancel` 区分。

## 4. 控制契约

- `function_id` 必填且唯一寻址；设备由功能归属推出。出现 `device_id`、`action`、`params` 一律按未声明字段 400。
- 调用只做一件事：取该功能最新一条码并经 `ir_proxy_tx` 回放；没有参数、没有能力校验、没有越界判断。
- 功能不存在 → 404；功能存在但没有任何码 → 404（文案说明「该功能还没有可用码」）。
- 回放成功只代表「信号已发出」，状态写 `unknown`；连接失败 → 503 `DEVICE_OFFLINE`，超时 → 504 `GATEWAY_TIMEOUT`，网关响应异常 → 502 `EXECUTION_FAILED`；探测结果**不参与**控制前置判断。
- 成功与失败都写 `audit_logs`（`who=user`），没有策略拒绝分支。

## 5. 演进规则

- 只做加法：新增端点、可选字段、枚举值都不升版本；破坏性变更必须升版本并保留旧端点一个周期。
- 字段命名冻结：用户侧 `message`、设备侧 `id`；错误码只增不改语义，新增码同时进本表与前端人话表。
- 契约文档是唯一来源：代码与本文冲突时，先改文档再改代码。
