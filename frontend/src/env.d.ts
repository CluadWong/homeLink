/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 部署期注入的 API token；为空时回退到 sessionStorage 的本次标签页临时输入。 */
  readonly VITE_API_TOKEN?: string
  /** 可选：后端地址（默认空串，走同源 /api 与 Vite 代理）。 */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
