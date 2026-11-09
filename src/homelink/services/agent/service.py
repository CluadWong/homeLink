"""Agent 粗粒度能力：查设备、查状态、快照、控制、场景。

对上层只暴露少量稳定动作；能力来自已学码的功能，不猜测设备支持什么。
"""

from __future__ import annotations

import json
import secrets
from typing import Any

from homelink.core.errors import AppError, ErrorCode
from homelink.core.timeutil import utc_now_iso
from homelink.gateway.base import GatewayError
from homelink.gateway.probe import GatewayProbe
from homelink.services.agent.catalog import build_device_model, matches_name
from homelink.services.agent.store import CommandRequestStore, SceneStore
from homelink.services.device_management.control import ControlService
from homelink.services.device_management.devices import DeviceStore
from homelink.services.device_management.states import DeviceStateStore

_RISK_LEVELS = {"low", "high"}


class AgentService:
    def __init__(
        self,
        devices: DeviceStore,
        states: DeviceStateStore,
        control: ControlService,
        scenes: SceneStore,
        commands: CommandRequestStore,
        probe: GatewayProbe,
    ) -> None:
        self._devices = devices
        self._states = states
        self._control = control
        self._scenes = scenes
        self._commands = commands
        self._probe = probe

    # ---- 查询 ----

    def find_devices(
        self,
        *,
        room: str | None = None,
        device_type: str | None = None,
        name: str | None = None,
    ) -> list[dict[str, Any]]:
        devices = self._devices.list(room)
        online = self._online()
        states = self._states_by_id()
        models = [
            build_device_model(device, states.get(device["id"]), online=online)
            for device in devices
        ]
        if device_type:
            needle = device_type.casefold()
            models = [model for model in models if model["type"].casefold() == needle]
        if name:
            models = [model for model in models if matches_name(model, name)]
        return models

    def get_device(self, device_id: str) -> dict[str, Any]:
        device = self._devices.get(device_id)
        return build_device_model(
            device,
            self._states_by_id().get(device_id),
            online=self._online(),
        )

    def get_state(self, device_id: str) -> dict[str, Any]:
        self._devices.get(device_id)
        row = self._states_by_id().get(device_id)
        return {
            "device_id": device_id,
            "state": {"power": row["state"] if row else "unknown"},
            "state_source": row["state_source"] if row else "unknown",
            "state_updated_at": row["updated_at"] if row else None,
            "state_readback": False,
        }

    def snapshot(self, *, room: str | None = None) -> dict[str, Any]:
        return {
            "gateway": self._probe.status(),
            "devices": self.find_devices(room=room),
            "scenes": self._scenes.list_all(),
            "generated_at": utc_now_iso(),
        }

    # ---- 控制 ----

    def control(
        self,
        *,
        device_id: str,
        command: str,
        parameters: dict[str, Any],
        request_id: str,
        confirm: bool,
        actor: str,
        trace_id: str,
    ) -> dict[str, Any]:
        device = self._devices.get(device_id)
        model = build_device_model(device, None, online=True)
        if model["risk_level"] == "high" and not confirm:
            raise AppError(
                ErrorCode.CONFIRMATION_REQUIRED,
                f"「{model['name']}」属于高风险设备，请先向用户复述并确认，再带 confirm=true 重试",
            )
        function = self._resolve_function(device, command)
        if parameters:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "该设备的命令是整条红外码，不支持参数；请省略 parameters",
            )

        existing = self._commands.get(request_id)
        if existing is not None:
            return self._replay(existing, device_id, function["id"], parameters)

        if not self._online():
            self._record_failure(
                request_id=request_id,
                device_id=device_id,
                function=function,
                parameters=parameters,
                actor=actor,
                trace_id=trace_id,
                error=AppError(ErrorCode.DEVICE_OFFLINE, "网关当前不可达，请检查板子电源与网络"),
            )
            raise AppError(ErrorCode.DEVICE_OFFLINE, "网关当前不可达，请检查板子电源与网络")

        try:
            self._commands.create(
                request_id=request_id,
                device_id=device_id,
                function_id=function["id"],
                command=function["name"],
                parameters=parameters,
                actor=actor,
                trace_id=trace_id,
            )
        except Exception as exc:  # 并发下同一 request_id 的竞态：退回重放
            if "UNIQUE" not in str(exc).upper():
                raise
            return self._replay(
                self._commands.get(request_id) or {},
                device_id,
                function["id"],
                parameters,
            )

        try:
            result = self._control.execute(function["id"], trace_id=trace_id)
        except AppError as exc:
            self._commands.finish(
                request_id,
                "failed",
                error_code=exc.code.value,
                error_message=exc.message,
            )
            raise
        except GatewayError as exc:  # 兜底：Controller 未映射的网关异常
            mapped = _map_gateway_error(exc)
            self._commands.finish(
                request_id,
                "failed",
                error_code=mapped.code.value,
                error_message=mapped.message,
            )
            raise mapped from exc

        self._commands.finish(request_id, "sent")
        return {
            "request_id": request_id,
            "status": "sent",
            "replayed": False,
            "device_id": device_id,
            "function_id": function["id"],
            "command": function["name"],
            "state": result["state"],
            "message": result["message"],
            "trace_id": trace_id,
        }

    def command_status(self, request_id: str) -> dict[str, Any]:
        record = self._commands.get(request_id)
        if record is None:
            raise AppError(ErrorCode.NOT_FOUND, f"请求 {request_id} 不存在")
        return _command_payload(record, replayed=False)

    def update_metadata(
        self,
        device_id: str,
        *,
        device_type: str | None = None,
        aliases: list[str] | None = None,
        risk_level: str | None = None,
    ) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        if device_type is not None:
            updates["type"] = device_type.strip()
        if aliases is not None:
            updates["aliases"] = [alias.strip() for alias in aliases if alias.strip()]
        if risk_level is not None:
            if risk_level not in _RISK_LEVELS:
                raise AppError(ErrorCode.INVALID_REQUEST, "risk_level 只能是 low 或 high")
            updates["risk_level"] = risk_level
        if not updates:
            raise AppError(ErrorCode.INVALID_REQUEST, "至少要修改 type / aliases / risk_level 之一")
        self._devices.update_extra(device_id, updates)
        return self.get_device(device_id)

    # ---- 场景 ----

    def list_scenes(self) -> list[dict[str, Any]]:
        return self._scenes.list_all()

    def delete_scene(self, scene_id: str) -> dict[str, Any]:
        return self._scenes.delete(scene_id)

    def create_scene(self, name: str, items: list[dict[str, str]]) -> dict[str, Any]:
        resolved: list[dict[str, str]] = []
        for item in items:
            device = self._devices.get(item["device_id"])
            function = self._resolve_function(device, item["command"])
            resolved.append({"device_id": device["id"], "function_id": function["id"]})
        return self._scenes.create(name.strip(), resolved)

    def run_scene(self, scene_id: str, *, actor: str, trace_id: str) -> dict[str, Any]:
        scene = self._scenes.get(scene_id)
        if not scene["items"]:
            raise AppError(ErrorCode.INVALID_REQUEST, "场景没有任何可执行功能")
        results: list[dict[str, Any]] = []
        for index, item in enumerate(scene["items"]):
            request_id = f"scene-{scene_id}-{index}-{secrets.token_hex(4)}"
            try:
                result = self.control(
                    device_id=item["device_id"],
                    command=item["function_id"],
                    parameters={},
                    request_id=request_id,
                    confirm=True,
                    actor=actor,
                    trace_id=trace_id,
                )
            except AppError as exc:
                results.append(
                    {
                        "device_id": item["device_id"],
                        "command": item["command"],
                        "status": "failed",
                        "error_code": exc.code.value,
                        "message": exc.message,
                    }
                )
                continue
            results.append(
                {
                    "device_id": item["device_id"],
                    "command": item["command"],
                    "status": "sent",
                    "request_id": result["request_id"],
                    "message": result["message"],
                }
            )
        sent = sum(1 for item in results if item["status"] == "sent")
        overall = "sent" if sent == len(results) else ("failed" if sent == 0 else "partial")
        return {"scene_id": scene_id, "status": overall, "results": results}

    # ---- 内部 ----

    def _online(self) -> bool:
        return bool(self._probe.status()["reachable"])

    def _states_by_id(self) -> dict[str, dict[str, Any]]:
        return {row["id"]: row for row in self._states.list_with_devices()}

    def _resolve_function(self, device: dict[str, Any], command: str) -> dict[str, Any]:
        functions = device.get("functions", [])
        exact = [item for item in functions if item["id"] == command]
        if exact:
            return exact[0]
        needle = command.strip().casefold()
        matches = [item for item in functions if item["name"].strip().casefold() == needle]
        if not matches:
            raise AppError(
                ErrorCode.NOT_FOUND,
                f"设备「{device['name']}」没有功能「{command}」；"
                f"现有功能：{', '.join(item['name'] for item in functions) or '无'}",
            )
        if len(matches) > 1:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                f"功能名「{command}」在设备「{device['name']}」下有多个匹配，请改用 command_id",
            )
        return matches[0]

    def _record_failure(
        self,
        *,
        request_id: str,
        device_id: str,
        function: dict[str, Any],
        parameters: dict[str, Any],
        actor: str,
        trace_id: str,
        error: AppError,
    ) -> None:
        self._commands.create(
            request_id=request_id,
            device_id=device_id,
            function_id=function["id"],
            command=function["name"],
            parameters=parameters,
            actor=actor,
            trace_id=trace_id,
        )
        self._commands.finish(
            request_id,
            "failed",
            error_code=error.code.value,
            error_message=error.message,
        )

    def _replay(
        self,
        record: dict[str, Any],
        device_id: str,
        function_id: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        if not record:
            raise AppError(ErrorCode.CONFLICT, "同一 request_id 正在执行，请稍后查询结果")
        same = (
            record["device_id"] == device_id
            and record["function_id"] == function_id
            and record["parameters"] == _dump(parameters)
        )
        if not same:
            raise AppError(
                ErrorCode.CONFLICT,
                "该 request_id 已被另一条命令使用，请为每次新指令生成新的 request_id",
            )
        if record["status"] == "running":
            raise AppError(ErrorCode.CONFLICT, "同一 request_id 正在执行，请稍后查询结果")
        if record["status"] == "failed":
            code = _error_code(record.get("error_code"))
            raise AppError(code, record.get("error_message") or "上次执行失败")
        return _command_payload(record, replayed=True)


def _dump(parameters: dict[str, Any]) -> str:
    return json.dumps(parameters, ensure_ascii=False)


def _command_payload(record: dict[str, Any], *, replayed: bool) -> dict[str, Any]:
    return {
        "request_id": record["request_id"],
        "status": record["status"],
        "replayed": replayed,
        "device_id": record["device_id"],
        "function_id": record["function_id"],
        "command": record["command"],
        "error_code": record.get("error_code"),
        "error_message": record.get("error_message"),
        "trace_id": record["trace_id"],
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
    }


def _error_code(raw: str | None) -> ErrorCode:
    if not raw:
        return ErrorCode.INTERNAL_ERROR
    try:
        return ErrorCode(raw)
    except ValueError:
        return ErrorCode.INTERNAL_ERROR


def _map_gateway_error(exc: GatewayError) -> AppError:
    if exc.kind == "timeout":
        return AppError(ErrorCode.GATEWAY_TIMEOUT, exc.message)
    if exc.kind == "unauthorized":
        return AppError(ErrorCode.DEVICE_UNAUTHORIZED, exc.message)
    if exc.kind == "bad_response":
        return AppError(ErrorCode.EXECUTION_FAILED, exc.message)
    return AppError(ErrorCode.DEVICE_OFFLINE, exc.message)
