"""契约同步测试：docs/agent-tools.md 第 3 节必须与 MCP 实际注册的 schema 一致。

P0-1 的 `request_id` 漂移正是缺少该测试的直接结果；任何一侧单独改动都必须让这里的断言失败。
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp")

from homelink_mcp import server

DOCS_PATH = Path(__file__).resolve().parents[2] / "docs" / "agent-tools.md"


def _document_functions() -> dict[str, dict[str, Any]]:
    """解析 agent-tools.md 第 3 节的 JSON 代码块；解析失败立即报错，不静默跳过。"""

    text = DOCS_PATH.read_text(encoding="utf-8")
    match = re.search(r"## 3\. Function Schema.*?```json\s*(\[.*?\])\s*```", text, re.DOTALL)
    if match is None:
        pytest.fail("docs/agent-tools.md 第 3 节没有找到 Function Schema JSON 代码块")
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        pytest.fail(f"docs/agent-tools.md Function Schema JSON 解析失败：{exc}")
    if not isinstance(payload, list) or not payload:
        pytest.fail("docs/agent-tools.md Function Schema 不是非空数组")
    functions: dict[str, dict[str, Any]] = {}
    for item in payload:
        function = item.get("function") if isinstance(item, dict) else None
        if not isinstance(function, dict):
            pytest.fail("docs/agent-tools.md Function Schema 存在缺 function 的条目")
        name = str(function.get("name") or "")
        if not name:
            pytest.fail("docs/agent-tools.md Function Schema 存在缺 name 的条目")
        functions[name] = function
    return functions


def _registered_schemas() -> dict[str, dict[str, Any]]:
    tools = asyncio.run(server.mcp.list_tools())
    return {tool.name: dict(tool.inputSchema) for tool in tools}


def test_tool_names_match_docs() -> None:
    documented = _document_functions()
    registered = _registered_schemas()

    assert set(documented) == set(registered), (
        f"工具面漂移：仅文档有 {sorted(set(documented) - set(registered))}，"
        f"仅代码有 {sorted(set(registered) - set(documented))}"
    )


def test_required_fields_match_docs() -> None:
    documented = _document_functions()
    registered = _registered_schemas()

    for name, function in documented.items():
        doc_required = set(function.get("parameters", {}).get("required", []))
        real_required = set(registered[name].get("required", []))
        assert (
            doc_required == real_required
        ), f"{name}.required 漂移：文档={sorted(doc_required)} 实际={sorted(real_required)}"


def _enum_values(schema: dict[str, Any]) -> set[str]:
    """收集 schema 中的枚举值；兼容顶层 enum 与 anyOf（Literal | None）两种形状。"""

    values: set[str] = set(schema.get("enum", []))
    for branch in schema.get("anyOf", []):
        values.update(branch.get("enum", []))
    return values


def test_enum_fields_match_docs() -> None:
    """文档里声明了 enum 的字段（如 device_type），取值集合必须与实际 schema 一致。"""

    documented = _document_functions()
    registered = _registered_schemas()

    for name, function in documented.items():
        doc_properties = function.get("parameters", {}).get("properties", {})
        real_properties = registered[name].get("properties", {})
        for prop, schema in doc_properties.items():
            if "enum" not in schema:
                continue
            assert prop in real_properties, f"{name}.{prop} 在实际 schema 中不存在"
            real_enum = _enum_values(real_properties[prop])
            assert (
                set(schema["enum"]) == real_enum
            ), f"{name}.{prop} 枚举漂移：文档={sorted(schema['enum'])} 实际={sorted(real_enum)}"


def test_home_run_scene_has_confirm() -> None:
    schema = _registered_schemas()["home_run_scene"]

    confirm = schema["properties"]["confirm"]
    assert confirm["type"] == "boolean"
    assert confirm.get("default") is False
    assert "confirm" not in schema.get("required", [])
