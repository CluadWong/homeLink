"""HomeLink 管家 Agent（M3 MVP）：本地 Ollama + MCP 工具，独立进程。

不 import `homelink`、不读写数据库；只通过 `homelink_mcp` 子进程调用 HTTP API。
"""
