"""D1 场景页 E2E：真实后端（mock 网关）+ 已构建前端 + Chromium。

覆盖：空态 → 新建（能力选择 + 勾设备）→ 编辑改名 → 执行 → 结果弹层 → 执行记录。
默认不进入 `pytest -q`；本地/CI 用 `python -m pytest -m e2e` 运行。

前置：
- `pip install -e ".[dev,e2e]"` 与 `python -m playwright install chromium`
- `cd frontend && npm run build`（后端同源托管 `dist`）
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "frontend" / "dist"


@dataclass(frozen=True)
class Server:
    base_url: str


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_config(root: Path, dist: Path) -> Path:
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    settings = {
        "gateway": {
            "mode": "mock",
            "ir": {"base_url": "", "username": "", "password": ""},
            "probe_timeout_seconds": 3,
        },
        "state": {"retention_days": 90},
        "audit": {"retention_days": 365},
        "api": {"token": ""},
        "frontend": {"enabled": True, "dist_path": str(dist)},
    }
    devices = {"rooms": [{"name": "living_room", "display_name": "客厅"}]}
    (config_dir / "settings.yaml").write_text(
        yaml.safe_dump(settings, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (config_dir / "devices.yaml").write_text(
        yaml.safe_dump(devices, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return config_dir


def _wait_health(base_url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base_url}/api/health", timeout=1.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"后端未在 {timeout} 秒内就绪：{base_url}")


def _seed_device(base_url: str) -> None:
    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        device = client.post("/api/devices", json={"name": "E2E 灯", "room": "living_room"})
        device.raise_for_status()
        device_id = device.json()["id"]
        started = client.post("/api/learn/start", json={"device_id": device_id})
        started.raise_for_status()
        saved = client.post(
            "/api/learn/save",
            json={
                "device_id": device_id,
                "name": "开",
                "raw_code": [900, 450, 900, 450],
            },
        )
        assert saved.status_code == 200, saved.text


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Server]:
    if not (DIST / "index.html").exists():
        pytest.skip("frontend/dist 不存在，先运行 cd frontend && npm run build")

    root = tmp_path_factory.mktemp("e2e")
    config_dir = _write_config(root, DIST)
    port = _free_port()
    env = {
        **os.environ,
        "HOMELINK_CONFIG_DIR": str(config_dir),
        "HOMELINK_DB_PATH": str(root / "homelink.db"),
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "homelink.interface.api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_health(base_url)
        _seed_device(base_url)
        yield Server(base_url=base_url)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest.fixture(scope="module")
def page(server: Server) -> Iterator[Any]:
    sync_api = pytest.importorskip(
        "playwright.sync_api", reason="需要 playwright（pip install -e .[e2e]）"
    )
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception as error:  # noqa: BLE001 - 缺浏览器时给出可执行的修复命令
            pytest.skip(f"Chromium 未安装，先运行 python -m playwright install chromium：{error}")
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.goto(f"{server.base_url}/scenes", wait_until="networkidle")
        yield page
        context.close()
        browser.close()


def test_scene_create_edit_run_and_history(page: Any) -> None:
    expect = pytest.importorskip("playwright.sync_api").expect

    expect(page.locator(".page-title")).to_have_text("场景")
    expect(page.get_by_text("还没有场景")).to_be_visible()

    # 新建：能力选择 + 勾设备
    page.locator(".empty-block").get_by_role("button", name="新建场景").click()
    page.get_by_placeholder("例如：离家").fill("离家")
    page.locator(".picker-trigger").click()
    picker = page.locator(".van-popup").filter(has=page.locator(".picker-row"))
    expect(picker).to_be_visible()
    picker.locator(".picker-row", has_text="开").click()
    page.locator(".check-row", has_text="E2E 灯").click()
    page.get_by_role("button", name="保存").click()
    expect(page.get_by_text("场景已创建")).to_be_visible()

    card = page.locator(".scene-card", has_text="离家")
    expect(card).to_be_visible()
    expect(card).to_contain_text("开 · 1 台")

    # 编辑：改名并保存
    card.get_by_role("button", name="编辑").click()
    expect(page.locator(".van-popup", has_text="编辑场景")).to_be_visible()
    page.get_by_placeholder("例如：离家").fill("回家")
    page.get_by_role("button", name="保存").click()
    expect(page.get_by_text("场景已更新")).to_be_visible()
    renamed = page.locator(".scene-card", has_text="回家")
    expect(renamed).to_be_visible()

    # 执行：低风险场景不弹确认，结果弹层按设备汇报
    renamed.locator(".row-actions").get_by_role("button", name="执行").click()
    result_sheet = page.locator(".van-popup", has_text="执行结果 · 回家")
    expect(result_sheet).to_be_visible()
    expect(result_sheet).to_contain_text("全部已发送")
    expect(result_sheet.locator(".run-row", has_text="E2E 灯")).to_be_visible()
    expect(result_sheet.locator(".run-row", has_text="E2E 灯")).to_contain_text("已发送")
    result_sheet.get_by_role("button", name="关闭").click()

    # 执行记录：一次执行一条，列表展示最近状态
    expect(renamed.locator(".run-history-meta")).to_contain_text("全部已发送")
    renamed.locator(".run-history-trigger").click()
    history_sheet = page.locator(".van-popup", has_text="执行记录 · 回家")
    expect(history_sheet).to_be_visible()
    expect(history_sheet.locator(".run-record")).to_have_count(1)
    expect(history_sheet.locator(".run-record").first).to_contain_text("E2E 灯")
