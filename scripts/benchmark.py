"""D5 性能与容量基线（mock 网关，进程内 TestClient）。

用法（仓库根）：

    python scripts/benchmark.py --rounds 60

全部使用临时配置与临时数据库，不读写 `data/`，也不发送任何红外。
输出一段 JSON 与一张 Markdown 表，粘贴到 `docs/performance.md` 即可。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
os.environ.setdefault("HOMELINK_DB_PATH", str(Path(tempfile.mkdtemp()) / "boot.db"))

from fastapi.testclient import TestClient  # noqa: E402

from homelink.bootstrap.context import AppContext, build_context  # noqa: E402
from homelink.core.timeutil import utc_now_iso  # noqa: E402
from homelink.gateway.mock import TEST_WAVEFORM  # noqa: E402
from homelink.interface.api.app import create_app  # noqa: E402

_SETTINGS = {
    "gateway": {
        "mode": "mock",
        "ir": {"base_url": "", "username": "", "password": ""},
        "probe_timeout_seconds": 3,
    },
    "state": {"retention_days": 90},
    "audit": {"retention_days": 365},
    "api": {"token": ""},
    "frontend": {"enabled": False, "dist_path": "frontend/dist"},
}
_DEVICES = {"rooms": [{"name": "living_room", "display_name": "客厅"}]}


def _write_config(root: Path) -> None:
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "settings.yaml").write_text(
        yaml.safe_dump(_SETTINGS, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (config_dir / "devices.yaml").write_text(
        yaml.safe_dump(_DEVICES, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def _percentile(samples: list[float], pct: float) -> float:
    ordered = sorted(samples)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return round(ordered[min(rank, len(ordered)) - 1], 1)


def _timed(client: TestClient, method: str, url: str, **kwargs: Any) -> tuple[float, dict]:
    started = time.perf_counter()
    response = client.request(method, url, **kwargs)
    elapsed = (time.perf_counter() - started) * 1000
    if response.status_code != 200:
        raise SystemExit(f"{method} {url} -> {response.status_code}: {response.text[:300]}")
    return elapsed, response.json()


def _seed_devices(context: AppContext, client: TestClient, count: int) -> list[str]:
    """绕过学码，直接写功能与码，模拟已建好的低风险设备。"""

    device_ids: list[str] = []
    now = utc_now_iso()
    for index in range(count):
        body = client.post("/api/devices", json={"name": f"台灯{index}", "room": "living_room"})
        device_id = body.json()["id"]
        function_id = f"fn_bench_{index}"
        with context.db.transaction() as conn:
            conn.execute(
                "INSERT INTO device_functions(id, device_id, name, created_at, updated_at)"
                " VALUES (?, ?, '开', ?, ?)",
                (function_id, device_id, now, now),
            )
            conn.execute(
                "INSERT INTO ir_codes(id, device_id, function_id, raw_code, learned_at, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"code_bench_{index}",
                    device_id,
                    function_id,
                    f"[{', '.join(map(str, TEST_WAVEFORM))}]",
                    now,
                    now,
                ),
            )
        device_ids.append(device_id)
    return device_ids


def _db_bytes(db_path: Path) -> int:
    total = 0
    for candidate in db_path.parent.glob(f"{db_path.name}*"):
        total += candidate.stat().st_size
    return total


def _count_rows(context: AppContext, table: str) -> int:
    with context.db.reader() as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def main() -> None:
    parser = argparse.ArgumentParser(description="HomeLink 后端性能与容量基线")
    parser.add_argument("--rounds", type=int, default=60, help="每个端点/场景的采样次数")
    parser.add_argument("--devices", type=int, default=20, help="场景内设备数（上限 20）")
    args = parser.parse_args()

    root = Path(tempfile.mkdtemp(prefix="homelink-bench-"))
    _write_config(root)
    db_path = root / "data" / "bench.db"
    context = build_context(root / "config", db_path)
    app = create_app(context)

    with TestClient(app) as client:
        device_ids = _seed_devices(context, client, args.devices)
        scene = client.post(
            "/api/agent/scenes",
            json={
                "name": "压测场景",
                "items": [{"device_id": device_id, "command": "开"} for device_id in device_ids],
            },
        ).json()["result"]
        scene_id = scene["scene_id"]

        reads: dict[str, list[float]] = {}
        for url in (
            "/api/health",
            "/api/state",
            "/api/devices",
            "/api/agent/capabilities",
            "/api/agent/scenes",
        ):
            samples = [_timed(client, "GET", url)[0] for _ in range(args.rounds)]
            reads[url] = samples

        before_bytes = _db_bytes(db_path)
        scene_samples: list[float] = []
        for _ in range(args.rounds):
            elapsed, body = _timed(client, "POST", f"/api/agent/scenes/{scene_id}/run")
            result = body["result"]
            if result["status"] != "sent" or len(result["results"]) != args.devices:
                raise SystemExit(
                    f"场景执行结果不符合预期：{result['status']} / {len(result['results'])}"
                )
            scene_samples.append(elapsed)
        after_bytes = _db_bytes(db_path)

        runs_url = f"/api/agent/scenes/{scene_id}/runs?limit=50"
        runs_samples = [_timed(client, "GET", runs_url)[0] for _ in range(min(args.rounds, 30))]
        over_limit = client.get(f"/api/agent/scenes/{scene_id}/runs?limit=51")
        if over_limit.status_code != 400:
            raise SystemExit(f"limit=51 未被拒绝：{over_limit.status_code}")

        metrics = client.get("/api/metrics").json()
        db_growth = after_bytes - before_bytes
        report = {
            "rounds": args.rounds,
            "scene_devices": args.devices,
            "reads_p95_ms": {url: _percentile(samples, 95) for url, samples in reads.items()},
            "reads_p50_ms": {url: _percentile(samples, 50) for url, samples in reads.items()},
            "scene_p95_ms": _percentile(scene_samples, 95),
            "scene_p50_ms": _percentile(scene_samples, 50),
            "runs_limit_50_p95_ms": _percentile(runs_samples, 95),
            "db_growth_bytes_per_run": round(db_growth / args.rounds),
            "db_growth_bytes_per_action": round(db_growth / args.rounds / args.devices),
            "command_requests_rows": _count_rows(context, "command_requests"),
            "audit_rows": _count_rows(context, "audit_logs"),
            "metrics_requests_total": metrics["requests"]["total"],
            "limits": {
                "scene_items": "1–20",
                "action_group_targets": "每组 1–20",
                "scene_runs_limit": "1–50",
                "audit_limit": "1–200",
            },
        }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print()
    print("| 指标 | P50 | P95 |")
    print("| --- | --- | --- |")
    for url in reads:
        print(
            f"| `GET {url}` | {_percentile(reads[url], 50)} ms | {_percentile(reads[url], 95)} ms |"
        )
    print(
        f"| `POST /api/agent/scenes/{{id}}/run`（{args.devices} 个动作）"
        f" | {report['scene_p50_ms']} ms | {report['scene_p95_ms']} ms |"
    )
    print(f"| `GET .../runs?limit=50` | – | {report['runs_limit_50_p95_ms']} ms |")


if __name__ == "__main__":
    main()
