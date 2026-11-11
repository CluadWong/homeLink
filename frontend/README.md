# HomeLink · 红外控制器前端

Vue 3 + Vite + TypeScript + Vant 4 + Vue Router。移动优先，桌面同样可用。
只通过 HTTP 访问后端，不读数据库、不 import 后端模块。

## 环境与启动

需要 Node 20+（本项目在 Node 22.11.0 上验证）。

```bash
cd frontend
npm install
npm run dev
```

`npm run dev` 默认监听 `5173`，并把 `/api` 代理到 `http://127.0.0.1:8000`，因此开发期**不需要后端开 CORS**。

后端默认就是 mock 模式，直接启动即可联调（不会连真实板子、不会发真实红外）：

```bash
uvicorn homelink.interface.api.app:app --host 127.0.0.1 --port 8000
```

## token 配置

1. **部署注入（首选）**：复制 `.env.example` 为 `.env.local`，填写 `VITE_API_TOKEN`。
   构建时注入；刷新后从注入配置重建，不依赖浏览器存储。
2. **当次输入**：没有注入时，页面顶部的「设置」折叠区会出现一次性手动填写入口，
   值只存 `sessionStorage` 的 `homelink_api_token`（storage 不可用时仅存当前页面内存）。
   同一标签页刷新仍有效，**关闭标签页即失效**；不写 `localStorage`。
3. 代码里没有硬编码任何真实 token。

token 优先级：部署注入 > 当次输入 > 空（请求不带 `Authorization`，由后端返回 401）。

可选：`VITE_API_BASE_URL` 留空表示同源 `/api`；仅当后端与前端不同源时才需要填绝对地址（此时需自行处理 CORS，不推荐）。

## 页面清单

| 页面 | 路由 | 要点 |
| --- | --- | --- |
| 调用 | `/call` | 设备 → 功能 → 执行；成功只说「已发送」，并提示红外单向无法确认执行 |
| 设备 | `/devices` | 新增/改名/换房间/删除设备；删除学码功能二次确认并说明会删红外码；房间管理（新增/改名排序/删除空房间） |
| 学码 | `/learn` | 四步：建设备并开始 → 每秒轮询捕获 → 命名保存 → 取消；覆盖 `no_signal` / `malformed` / `gateway_unreachable`，401 板子鉴权失败单列；10 分钟 TTL 提示；保存成功给「去调用」出口 |
| 状态 | `/state` | 5 秒轮询；`unknown` 一律显示「未知」，绝不显示成「关」；传感器 `available=false` 显示「未接入」；网关在线/离线与「服务连不上」分开 |
| 校正 | `/correct` | 设备 + 当前记录 + on/off/unknown 三选 + 自由 reason；超 12 字符截断展示 |
| 审计 | `/audit` | `result` 三选（success/failed/all）+ 按设备筛选 + limit 50/100/200；只读 |

导航在主区顶部，六个入口固定；状态摘要条常驻，失败时清空而不展示陈旧数据。
服务不可达时整页降级为「管家服务连不上」+ 重试，保留导航。

## 场景页 E2E（Playwright）

场景页的关键路径（列表 → 新建 → 编辑 → 执行 → 结果弹层 → 执行记录）由 Python Playwright 驱动 Chromium 覆盖，
用真实后端（mock 网关）+ 临时数据库 + 同源托管的 `dist`，不发任何红外。

```bash
# 仓库根
pip install -e ".[dev,e2e]"
python -m playwright install chromium

cd frontend && npm run build && cd ..

python -m pytest -m e2e
```

默认 `pytest` 不跑（`pyproject.toml` 里 `-m "not e2e"`），CI 的 `e2e` job 会先构建前端再执行。

## 构建与托管

```bash
npm run build      # vue-tsc --noEmit && vite build，产物在 dist/
npm run preview    # 本地预览构建产物（仍建议接真实后端联调）
```

交给后端托管的做法（**不改后端 `src/`**）：

1. 把 `frontend/dist/` 作为静态站点交给同源的静态服务器 / 反向代理。
2. 把 `/api` 反向代理到 `http://127.0.0.1:8000`，前端与 API 同源，无需 CORS。
3. 路由使用 `createWebHistory`，静态服务器需把未知路径回退到 `index.html`。

nginx 示例：

```nginx
server {
    listen 80;
    root /srv/homelink/frontend/dist;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Trace-Id $http_x_trace_id;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

若由 Python 进程直接托管，可用任意 ASGI 静态文件中间件把 `dist` 挂到 `/`，并让 `/api` 仍走现有 FastAPI 路由；
这一步属于部署层，不要求修改后端源码。

## 契约

实现以 `docs/frontend-contract.md` 与 `docs/api-contract.md` 为准。房间 `name` 派生、学码异常处理、
整页卸载取消、部署变量、token 三档优先级与 `AGENT_UNREACHABLE` 的定位均已写入这两份契约。
