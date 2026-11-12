"""Ollama /api/chat 的最小客户端（非流式 + 工具调用）。"""

from __future__ import annotations

from typing import Any

import httpx

from homelink_agent.config import AgentSettings


class OllamaError(RuntimeError):
    """Ollama 不可用或返回异常。"""


async def chat(
    settings: AgentSettings,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": settings.model,
        "messages": messages,
        "stream": False,
        # 简单指令走快路径不经过这里；回落到模型的复杂指令默认保留思考过程。
        "think": settings.think,
    }
    if settings.num_predict > 0:
        body["options"] = {"num_predict": settings.num_predict}
    if tools:
        body["tools"] = tools
    if settings.keep_alive:
        body["keep_alive"] = settings.keep_alive  # 常驻服务用它把模型留在显存里

    url = f"{settings.ollama_url}/api/chat"
    try:
        async with httpx.AsyncClient(timeout=settings.timeout) as client:
            response = await client.post(url, json=body)
    except httpx.HTTPError as exc:
        raise OllamaError(
            f"连不上 Ollama（{settings.ollama_url}），请确认 ollama serve 已启动"
        ) from exc

    if response.status_code != 200:
        raise OllamaError(f"Ollama 返回 HTTP {response.status_code}：{response.text[:200]}")

    payload = response.json()
    if "error" in payload:
        raise OllamaError(f"Ollama 报错：{payload['error']}")

    message = payload.get("message")
    if not isinstance(message, dict):
        raise OllamaError("Ollama 响应缺少 message 字段")
    return message


async def warm_model(settings: AgentSettings) -> None:
    """预载模型并按 keep_alive 驻留；只加载不生成，失败由调用方决定是否致命。"""

    url = f"{settings.ollama_url}/api/generate"
    body: dict[str, Any] = {"model": settings.model, "keep_alive": settings.keep_alive or "5m"}
    try:
        async with httpx.AsyncClient(timeout=settings.timeout) as client:
            response = await client.post(url, json=body)
    except httpx.HTTPError as exc:
        raise OllamaError(f"连不上 Ollama（{settings.ollama_url}），模型预热失败") from exc
    if response.status_code != 200:
        raise OllamaError(f"模型预热失败：HTTP {response.status_code} {response.text[:200]}")


def to_ollama_tool(tool: Any) -> dict[str, Any]:
    """把 MCP 的 Tool 转成 Ollama tools 格式，去掉模型用不到的空默认值。"""

    schema = dict(tool.inputSchema or {})
    schema.pop("title", None)
    parameters = dict(schema)
    properties = parameters.get("properties")
    if isinstance(properties, dict):
        parameters["properties"] = {name: _strip_nulls(spec) for name, spec in properties.items()}
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": parameters,
        },
    }


def _strip_nulls(spec: Any) -> Any:
    if not isinstance(spec, dict):
        return spec
    cleaned = {key: value for key, value in spec.items() if key not in {"title", "default"}}
    if "anyOf" in cleaned:
        options = [option for option in cleaned["anyOf"] if option.get("type") != "null"]
        if len(options) == 1:
            cleaned.pop("anyOf")
            cleaned.update(options[0])
    return cleaned
