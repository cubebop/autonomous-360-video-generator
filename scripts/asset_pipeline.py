"""Asset task contracts and validation helpers for Autonomous 360 Video Generator.

This module sits between scene planning and rendering:

- turn a scene plan into structured asset tasks
- generate Claude/Blender-ready prompts
- validate exported assets after generation
- prepare data that can be folded into a manifest later
- optionally invoke an external Claude/Blender bridge for generation
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
import uuid
import socket
import base64
import os as _os
import struct
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GENERATE_NEW = "generate_new"
REUSE_EXISTING = "reuse_existing"
EXECUTOR_ENV_VAR = "A360VG_ASSET_EXECUTOR"
CLAUDE_CODE_EXECUTOR = "claude_code"
WINDOWS_CLAUDE_CODE_EXECUTOR = "windows_claude_code"
ASSET_SKILL_PATH = PROJECT_ROOT / "codex_notes" / "SKILL_asset_builder.md"
CLAUDE_PRINT_ARGS_ENV = "CLAUDE_PRINT_ARGS"
MAX_AUTORUN_ENV = "A360VG_MAX_AUTORUNS"
_autorun_count = 0
BLENDER_MCP_DIRECT_EXECUTOR = "blender_mcp_direct"
MCP_HOST_ENV = "A360VG_MCP_HOST"
MCP_PORT_ENV = "A360VG_MCP_PORT"
MCP_MESSAGE_FORMAT_ENV = "A360VG_MCP_MESSAGE_FORMAT"
OLLAMA_BASE_URL_ENV = "OLLAMA_BASE_URL"
OLLAMA_MODEL_ENV = "OLLAMA_MODEL"
OLLAMA_TIMEOUT_ENV = "OLLAMA_TIMEOUT"
DEFAULT_OLLAMA_MODEL = "llama3.2"


def default_service_host() -> str:
    """Return the Windows host gateway under WSL, otherwise localhost.

    Blender normally runs on Windows while this pipeline runs inside WSL. The
    WSL default route points at the Windows host, so using it avoids embedding
    a machine-specific address in the project. Environment variables still
    take precedence wherever this value is used.
    """
    if not os.getenv("WSL_DISTRO_NAME"):
        return "127.0.0.1"

    try:
        completed = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True,
            text=True,
            check=False,
        )
        match = re.search(r"\bvia\s+(\S+)", completed.stdout)
        if match:
            return match.group(1)
    except OSError:
        pass

    return "127.0.0.1"


DEFAULT_OLLAMA_BASE_URL = f"http://{default_service_host()}:11434"


def slugify(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return text or "asset"


def first_value(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def normalize_action(value: Any, *, default: str = GENERATE_NEW) -> str:
    text = str(value or default).strip().lower()
    if text in {GENERATE_NEW, "generate", "new"}:
        return GENERATE_NEW
    if text in {REUSE_EXISTING, "reuse", "existing"}:
        return REUSE_EXISTING
    raise ValueError(f"Unsupported asset action: {value!r}")


def to_repo_relative_path(path: str) -> str:
    normalized = path.replace("\\", "/").strip()
    if normalized.startswith("public/"):
        return normalized
    if normalized.startswith("/assets/") or normalized.startswith("/manifests/"):
        return f"public{normalized}"
    if normalized.startswith("assets/") or normalized.startswith("manifests/"):
        return f"public/{normalized}"
    return normalized.lstrip("./")


def absolute_asset_path(relative_path: str) -> Path:
    return PROJECT_ROOT / to_repo_relative_path(relative_path)


def windows_unc_path(path: Path) -> str:
    """Convert a WSL path into the UNC path Windows Blender can write to."""
    distro = os.getenv("WSL_DISTRO_NAME", "Ubuntu")
    return f"\\\\wsl$\\{distro}{str(path.resolve()).replace('/', '\\')}"


def environment_output_path(scene_plan: dict[str, Any]) -> str:
    scene_id = slugify(scene_plan.get("scene_id", "scene"))
    return f"public/assets/environments/{scene_id}_environment.glb"


def entity_output_path(asset_id: str) -> str:
    return f"public/assets/generated/{slugify(asset_id)}.glb"


def task_requires_generation(task: dict[str, Any]) -> bool:
    return normalize_action(task.get("action")) == GENERATE_NEW


def hex_to_rgba(hex_color: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    """Convert a CSS hex color string to a Blender RGBA tuple (0.0–1.0 per channel)."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r = int(h[0:2], 16) / 255.0
    g = int(h[2:4], 16) / 255.0
    b = int(h[4:6], 16) / 255.0
    return (round(r, 4), round(g, 4), round(b, 4), alpha)


# Maps shape names (as Ollama might output them) to Blender primitive calls.
# All must be safe on Blender 3.x and 4.x.
_SHAPE_OPS: dict[str, str] = {
    "ico_sphere":  "bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=1, location=(0, 0, 0))",
    "icosphere":   "bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=1, location=(0, 0, 0))",
    "sphere":      "bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=1, location=(0, 0, 0))",
    "cube":        "bpy.ops.mesh.primitive_cube_add(size=1.5, location=(0, 0, 0))",
    "box":         "bpy.ops.mesh.primitive_cube_add(size=1.5, location=(0, 0, 0))",
    "cone":        "bpy.ops.mesh.primitive_cone_add(vertices=6, radius1=1, radius2=0, depth=2, location=(0, 0, 0))",
    "cylinder":    "bpy.ops.mesh.primitive_cylinder_add(vertices=8, radius=0.8, depth=2, location=(0, 0, 0))",
    "torus":       "bpy.ops.mesh.primitive_torus_add(major_radius=1, minor_radius=0.3, major_segments=8, minor_segments=4)",
}
_DEFAULT_SHAPE_OP = _SHAPE_OPS["ico_sphere"]


def build_environment_task(scene_plan: dict[str, Any]) -> dict[str, Any]:
    spec = scene_plan.get("environment_asset", {}) or {}
    action = normalize_action(
        first_value(
            spec.get("action"),
            spec.get("mode"),
            spec.get("asset_action"),
            scene_plan.get("environment_asset_action"),
        )
    )
    asset_id = first_value(spec.get("asset_id"), f"{scene_plan['scene_id']}_environment")
    description = first_value(spec.get("description"), scene_plan.get("environment_asset_description", ""))

    if action == REUSE_EXISTING:
        asset_path = first_value(
            spec.get("path"),
            spec.get("asset_path"),
            spec.get("source_path"),
            spec.get("reuse_path"),
            scene_plan.get("environment_reuse_path"),
        )
        if not asset_path:
            raise ValueError("Environment reuse requires an existing asset path.")
        normalized_path = to_repo_relative_path(str(asset_path))
        return {
            "task_type": "environment",
            "asset_id": asset_id,
            "action": action,
            "asset_path": normalized_path,
            "target_path": normalized_path,
            "source_path": normalized_path,
            "description": description,
            "scene_id": scene_plan["scene_id"],
            "title": scene_plan.get("title", ""),
            "mood": scene_plan.get("mood", ""),
            "sky_color": spec.get("sky_color"),
            "ground_color": spec.get("ground_color"),
        }

    target_path = to_repo_relative_path(str(first_value(spec.get("target_path"), environment_output_path(scene_plan))))
    return {
        "task_type": "environment",
        "asset_id": asset_id,
        "action": action,
        "asset_path": target_path,
        "target_path": target_path,
        "source_path": None,
        "description": description,
        "scene_id": scene_plan["scene_id"],
        "title": scene_plan.get("title", ""),
        "mood": scene_plan.get("mood", ""),
        "sky_color": spec.get("sky_color"),
        "ground_color": spec.get("ground_color"),
    }


def build_entity_task(scene_plan: dict[str, Any], entity: dict[str, Any]) -> dict[str, Any]:
    action = normalize_action(
        first_value(
            entity.get("action"),
            entity.get("mode"),
            entity.get("asset_action"),
            entity.get("generation_mode"),
        )
    )
    asset_id = entity["asset_id"]
    description = entity.get("description", "")

    if action == REUSE_EXISTING:
        asset_path = first_value(
            entity.get("path"),
            entity.get("asset_path"),
            entity.get("source_path"),
            entity.get("reuse_path"),
        )
        if not asset_path:
            raise ValueError(f"Entity reuse requires an existing asset path for {asset_id}.")
        normalized_path = to_repo_relative_path(str(asset_path))
        return {
            "task_type": "entity",
            "asset_id": asset_id,
            "action": action,
            "asset_path": normalized_path,
            "target_path": normalized_path,
            "source_path": normalized_path,
            "description": description,
            "scene_id": scene_plan["scene_id"],
            "title": scene_plan.get("title", ""),
            "mood": scene_plan.get("mood", ""),
            "color": entity.get("color"),
            "shape": entity.get("shape", "ico_sphere"),
        }

    target_path = to_repo_relative_path(str(first_value(entity.get("target_path"), entity_output_path(asset_id))))
    return {
        "task_type": "entity",
        "asset_id": asset_id,
        "action": action,
        "asset_path": target_path,
        "target_path": target_path,
        "source_path": None,
        "description": description,
        "scene_id": scene_plan["scene_id"],
        "title": scene_plan.get("title", ""),
        "mood": scene_plan.get("mood", ""),
        "color": entity.get("color"),
        "shape": entity.get("shape", "ico_sphere"),
    }


def build_asset_tasks(scene_plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Create one environment task plus one task per entity."""
    tasks = [build_environment_task(scene_plan)]

    for entity in scene_plan.get("entity_assets", []):
        tasks.append(build_entity_task(scene_plan, entity))

    return tasks


def build_claude_prompt(task: dict[str, Any]) -> str:
    """Generate a concrete asset-generation prompt for Claude + Blender MCP."""
    if not task_requires_generation(task):
        raise ValueError(f"Task {task['asset_id']} reuses an existing asset and does not need a generation prompt.")

    skill_text = ""
    if ASSET_SKILL_PATH.exists():
        skill_text = ASSET_SKILL_PATH.read_text(encoding="utf-8").strip()

    asset_kind = "environment" if task["task_type"] == "environment" else "entity asset"
    linux_export_path = absolute_asset_path(task["asset_path"])
    blender_export_path = windows_unc_path(linux_export_path)
    prompt_body = (
        f"Create one {asset_kind} for Autonomous 360 Video Generator.\n\n"
        f"Scene ID: {task['scene_id']}\n"
        f"Scene title: {task['title']}\n"
        f"Mood: {task['mood']}\n"
        f"Asset ID: {task['asset_id']}\n"
        f"Description: {task['description']}\n"
        f"Repo export target: {task['target_path']}\n"
        f"Linux validation path: {linux_export_path}\n"
        f"Windows Blender export path: {blender_export_path}\n\n"
        "Requirements:\n"
        "- low-poly style\n"
        "- clean pivot/origin placement\n"
        "- export as .glb\n"
        "- keep the asset self-contained and production-usable\n"
        "- do not change unrelated files\n"
        f"- because Blender runs on Windows, export the GLB to this exact UNC path: {blender_export_path}\n"
    )
    if skill_text:
        return f"{skill_text}\n\n{prompt_body}"
    return prompt_body


def build_generation_prompt_payloads(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "asset_id": task["asset_id"],
            "task_type": task["task_type"],
            "action": task["action"],
            "target_path": task["target_path"],
            "expected_output_unc_path": windows_unc_path(absolute_asset_path(task["asset_path"])),
            "prompt": build_claude_prompt(task),
        }
        for task in tasks
        if task_requires_generation(task)
    ]


def handoff_output_path(task: dict[str, Any]) -> Path:
    return PROJECT_ROOT / "output" / "asset-handoffs" / task["scene_id"] / f"{slugify(task['asset_id'])}.json"


def save_handoff_request(task: dict[str, Any], prompt: str) -> Path:
    request_path = handoff_output_path(task)
    request_path.parent.mkdir(parents=True, exist_ok=True)
    expected_output_path = absolute_asset_path(task["asset_path"])
    payload = {
        "project_root": str(PROJECT_ROOT),
        "task": task,
        "prompt": prompt,
        "expected_output_path": str(expected_output_path),
        "expected_output_unc_path": windows_unc_path(expected_output_path),
    }
    request_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return request_path


def executor_command_from_env() -> str | None:
    value = os.getenv(EXECUTOR_ENV_VAR, "").strip()
    return value or None


def is_claude_code_executor(command: str | None) -> bool:
    normalized = (command or "").strip().lower()
    return normalized in {CLAUDE_CODE_EXECUTOR, "claude-code"}


def is_windows_claude_code_executor(command: str | None) -> bool:
    normalized = (command or "").strip().lower()
    return normalized in {WINDOWS_CLAUDE_CODE_EXECUTOR, "windows-claude-code"}


def is_blender_mcp_direct_executor(command: str | None) -> bool:
    normalized = (command or "").strip().lower()
    return normalized in {BLENDER_MCP_DIRECT_EXECUTOR, "blender-mcp-direct"}


def claude_print_args() -> list[str]:
    default_args = "-p --output-format text --permission-mode auto"
    raw = os.getenv(CLAUDE_PRINT_ARGS_ENV, default_args).strip()
    return raw.split()


def claude_output_indicates_failure(stdout: str, stderr: str) -> bool:
    combined = f"{stdout}\n{stderr}".lower()
    failure_markers = [
        "not logged in",
        "please run /login",
        "authentication",
        "unauthorized",
        "hit your limit",
        "you've hit your limit",
        "resets",
    ]
    return any(marker in combined for marker in failure_markers)


def execute_blender_mcp_code(
    python_code: str,
    *,
    host: str | None = None,
    port: int | None = None,
    timeout_seconds: int = 60,
    expect_response: bool = False,
) -> dict[str, Any]:
    """Send Python code directly to a running Blender MCP socket server."""
    target_host = host or os.getenv(MCP_HOST_ENV) or default_service_host()
    target_port = int(port or os.getenv(MCP_PORT_ENV, "9876"))
    message_format = os.getenv(MCP_MESSAGE_FORMAT_ENV, "execute_code").strip().lower()
    if message_format in {"execute_code", "execute-code", "code"}:
        payload_obj = {"type": "execute_code", "params": {"code": python_code}}
    elif message_format in {"jsonrpc", "json-rpc"}:
        payload_obj = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "execute",
            "params": {"code": python_code},
        }
    elif message_format in {"execute_code_flat", "execute-code-flat"}:
        payload_obj = {"type": "execute_code", "code": python_code}
    elif message_format in {"length_prefixed", "length-prefixed", "len"}:
        payload_obj = {"type": "execute", "code": python_code}
    else:
        payload_obj = {"type": "execute", "code": python_code}
    payload = json.dumps(payload_obj)

    try:
        if not expect_response:
            # Fire-and-forget: send and don't wait for a full JSON response.
            response = _execute_blender_mcp_raw_json(
                target_host, target_port, payload, timeout_seconds, expect_json=False
            )
            return {
                "status": "success",
                "host": target_host,
                "port": target_port,
                "protocol": "raw_json_fire_and_forget",
                "response": response,
                "raw_response": "",
            }

        # Use the proven socket pattern (no shutdown) when we need a response.
        response = _execute_blender_mcp_proven(
            target_host, target_port, payload, timeout_seconds
        )
        return {
            "status": "success",
            "host": target_host,
            "port": target_port,
            "protocol": "proven_raw_json",
            "response": response,
            "raw_response": "",
        }

        raw_response = _execute_blender_mcp_raw(
            target_host, target_port, payload, timeout_seconds
        )

        if not raw_response:
            ws_response = _execute_blender_mcp_websocket(
                target_host, target_port, payload, timeout_seconds
            )
            return {
                "status": "success",
                "host": target_host,
                "port": target_port,
                "protocol": "websocket",
                "response": ws_response,
                "raw_response": "",
            }

        try:
            response = json.loads(raw_response)
        except json.JSONDecodeError:
            response = {"raw_response": raw_response}

        return {
            "status": "success",
            "host": target_host,
            "port": target_port,
            "protocol": "raw",
            "response": response,
            "raw_response": raw_response,
        }
    except Exception as exc:
        return {
            "status": "error",
            "host": target_host,
            "port": target_port,
            "error": str(exc),
        }


def _execute_blender_mcp_proven(
    host: str,
    port: int,
    payload: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Send code to Blender MCP using the proven working socket pattern.

    Key differences from other variants:
    - Does NOT call shutdown(SHUT_WR) — avoids sending TCP FIN before Blender runs the code
    - Reads in a loop accumulating chunks, breaks as soon as valid JSON is assembled
    - Sets timeout on the socket itself before connect
    """
    import urllib.error

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout_seconds)
    try:
        s.connect((host, port))
        s.sendall(payload.encode("utf-8"))
        buffer = b""
        while True:
            try:
                chunk = s.recv(8192)
            except socket.timeout:
                break
            if not chunk:
                break
            buffer += chunk
            try:
                return json.loads(buffer.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
    finally:
        s.close()
    raw = buffer.decode("utf-8", errors="replace").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw_response": raw}


def _execute_blender_mcp_raw(
    host: str,
    port: int,
    payload: str,
    timeout_seconds: int,
) -> str:
    with socket.create_connection((host, port), timeout=timeout_seconds) as sock:
        sock.sendall((payload + "\n").encode("utf-8"))
        sock.shutdown(socket.SHUT_WR)

        chunks: list[bytes] = []
        sock.settimeout(timeout_seconds)
        while True:
            try:
                data = sock.recv(65536)
            except socket.timeout:
                break
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks).decode("utf-8", errors="replace").strip()


def _execute_blender_mcp_fire_and_forget(
    host: str,
    port: int,
    payload: str,
    timeout_seconds: int,
) -> None:
    with socket.create_connection((host, port), timeout=timeout_seconds) as sock:
        sock.sendall((payload + "\n").encode("utf-8"))
        sock.shutdown(socket.SHUT_WR)


def _execute_blender_mcp_raw_json(
    host: str,
    port: int,
    payload: str,
    timeout_seconds: int,
    *,
    expect_json: bool,
) -> dict[str, Any]:
    with socket.create_connection((host, port), timeout=timeout_seconds) as sock:
        sock.sendall(payload.encode("utf-8"))
        sock.shutdown(socket.SHUT_WR)

        chunks: list[bytes] = []
        sock.settimeout(timeout_seconds)
        while True:
            try:
                data = sock.recv(8192)
            except socket.timeout:
                break
            if not data:
                break
            chunks.append(data)
            if expect_json:
                try:
                    json.loads(b"".join(chunks).decode("utf-8", errors="replace"))
                    break
                except json.JSONDecodeError:
                    continue

    raw_response = b"".join(chunks).decode("utf-8", errors="replace").strip()
    if not raw_response:
        return {}
    try:
        return json.loads(raw_response)
    except json.JSONDecodeError:
        return {"raw_response": raw_response}


def _execute_blender_mcp_length_prefixed(
    host: str,
    port: int,
    payload: str,
    timeout_seconds: int,
) -> None:
    payload_bytes = payload.encode("utf-8")
    header = struct.pack("!I", len(payload_bytes))
    with socket.create_connection((host, port), timeout=timeout_seconds) as sock:
        sock.sendall(header + payload_bytes)
        sock.shutdown(socket.SHUT_WR)


def _execute_blender_mcp_websocket(
    host: str,
    port: int,
    payload: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    key = base64.b64encode(_os.urandom(16)).decode("ascii")
    request = (
        "GET / HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )

    with socket.create_connection((host, port), timeout=timeout_seconds) as sock:
        sock.sendall(request.encode("ascii"))
        sock.settimeout(timeout_seconds)

        response = _recv_http_headers(sock)
        if "101" not in response:
            return {"error": "websocket_handshake_failed", "response": response}

        _send_ws_text(sock, payload)
        ws_message = _recv_ws_text(sock)
        if not ws_message:
            return {}
        try:
            return json.loads(ws_message)
        except json.JSONDecodeError:
            return {"raw_response": ws_message}


def _recv_http_headers(sock: socket.socket) -> str:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(1024)
        if not chunk:
            break
        data += chunk
    return data.decode("utf-8", errors="replace")


def _send_ws_text(sock: socket.socket, message: str) -> None:
    payload = message.encode("utf-8")
    fin_opcode = 0x81  # FIN + text
    mask_bit = 0x80
    length = len(payload)
    header = bytearray([fin_opcode])
    if length < 126:
        header.append(mask_bit | length)
    elif length < (1 << 16):
        header.append(mask_bit | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(mask_bit | 127)
        header.extend(struct.pack("!Q", length))

    mask_key = _os.urandom(4)
    header.extend(mask_key)
    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    sock.sendall(header + masked)


def _recv_ws_text(sock: socket.socket) -> str:
    first = sock.recv(2)
    if len(first) < 2:
        return ""
    fin_opcode, length_byte = first
    length = length_byte & 0x7F
    if length == 126:
        length = struct.unpack("!H", sock.recv(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", sock.recv(8))[0]

    masked = length_byte & 0x80
    mask_key = sock.recv(4) if masked else b""
    payload = sock.recv(length) if length else b""
    if masked and mask_key:
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return payload.decode("utf-8", errors="replace")


def _execute_blender_mcp_websocket_fire_and_forget(
    host: str,
    port: int,
    payload: str,
    timeout_seconds: int,
) -> str | None:
    key = base64.b64encode(_os.urandom(16)).decode("ascii")
    request = (
        "GET / HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )

    try:
        with socket.create_connection((host, port), timeout=timeout_seconds) as sock:
            sock.sendall(request.encode("ascii"))
            sock.settimeout(timeout_seconds)

            response = _recv_http_headers(sock)
            if "101" not in response:
                return "websocket_handshake_failed"

            _send_ws_text(sock, payload)
            return None
    except Exception as exc:
        return str(exc)


def execute_via_claude_code(asset_prompt: str, expected_output_path: str, timeout: int = 900) -> dict[str, Any]:
    """Shell out to Claude Code CLI to build an asset via Blender MCP."""
    try:
        completed = subprocess.run(
            ["claude", *claude_print_args()],
            cwd=PROJECT_ROOT,
            capture_output=True,
            input=asset_prompt,
            text=True,
            timeout=timeout,
            check=False,
        )
        asset_exists = os.path.exists(expected_output_path) and os.path.getsize(expected_output_path) > 0
        claude_failed = claude_output_indicates_failure(completed.stdout, completed.stderr)
        return {
            "status": "success" if completed.returncode == 0 and asset_exists and not claude_failed else "failed",
            "command": CLAUDE_CODE_EXECUTOR,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "asset_exists": asset_exists,
            "claude_failed": claude_failed,
            "expected_path": expected_output_path,
            "expected_unc_path": windows_unc_path(Path(expected_output_path)),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "timeout",
            "command": CLAUDE_CODE_EXECUTOR,
            "returncode": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "asset_exists": False,
            "claude_failed": True,
            "expected_path": expected_output_path,
            "expected_unc_path": windows_unc_path(Path(expected_output_path)),
        }
    except Exception as exc:
        return {
            "status": "error",
            "command": CLAUDE_CODE_EXECUTOR,
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
            "asset_exists": False,
            "claude_failed": True,
            "expected_path": expected_output_path,
            "expected_unc_path": windows_unc_path(Path(expected_output_path)),
        }


def wslpath_to_windows(path: str | Path) -> str:
    completed = subprocess.run(
        ["wslpath", "-w", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def execute_via_windows_claude_code(
    asset_prompt: str,
    expected_output_path: str,
    timeout: int = 120,
) -> dict[str, Any]:
    """Call Windows Claude Code from WSL via a PowerShell bridge."""
    prompt_file_path: str | None = None

    try:
        filename = f"asset_prompt_{uuid.uuid4().hex[:8]}.txt"
        prompt_file_path = f"/tmp/{filename}"
        prompt_file_windows_path = f"\\\\wsl$\\Ubuntu\\tmp\\{filename}"

        with open(prompt_file_path, "w", encoding="utf-8") as prompt_file:
            prompt_file.write(asset_prompt)

        cli_args = " ".join(claude_print_args())
        ps_command = (
            f'$prompt = Get-Content -Path "{prompt_file_windows_path}" -Raw; '
            f'$prompt | claude {cli_args}; exit 0'
        )
        try:
            completed = subprocess.run(
                ["powershell.exe", "-ExecutionPolicy", "Bypass", "-Command", ps_command],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            stdout = completed.stdout
            stderr = completed.stderr
        except subprocess.TimeoutExpired:
            stdout = "timeout"
            stderr = ""

        asset_exists = os.path.exists(expected_output_path) and os.path.getsize(expected_output_path) > 0
        claude_failed = claude_output_indicates_failure(stdout, stderr)
        return {
            "status": "success" if asset_exists and not claude_failed else "failed",
            "command": WINDOWS_CLAUDE_CODE_EXECUTOR,
            "returncode": None,
            "stdout": stdout,
            "stderr": stderr,
            "asset_exists": asset_exists,
            "claude_failed": claude_failed,
            "expected_path": expected_output_path,
            "expected_unc_path": windows_unc_path(Path(expected_output_path)),
            "prompt_file_windows_path": prompt_file_windows_path,
            "bridge_note": "PowerShell reads prompt from prompt_file_windows_path",
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "timeout",
            "command": WINDOWS_CLAUDE_CODE_EXECUTOR,
            "returncode": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "asset_exists": False,
            "claude_failed": True,
            "expected_path": expected_output_path,
            "expected_unc_path": windows_unc_path(Path(expected_output_path)),
        }
    except Exception as exc:
        return {
            "status": "error",
            "command": WINDOWS_CLAUDE_CODE_EXECUTOR,
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
            "asset_exists": False,
            "claude_failed": True,
            "expected_path": expected_output_path,
            "expected_unc_path": windows_unc_path(Path(expected_output_path)),
        }
    finally:
        if prompt_file_path and os.path.exists(prompt_file_path):
            os.unlink(prompt_file_path)


def _ollama_generate_blender_code(
    task: dict[str, Any],
    unc_path: str,
    *,
    ollama_url: str,
    ollama_model: str,
    ollama_timeout: int,
) -> str:
    """Call Ollama to generate Blender Python code for an asset task.

    Returns raw code string (may still have markdown fences — strip before use).
    Raises RuntimeError on network or API failure.
    """
    import urllib.request

    asset_kind = "environment" if task["task_type"] == "environment" else "entity"

    system_prompt = (
        "You are a Blender Python code generator.\n"
        "Return ONLY valid Python code. No markdown fences. No explanation.\n"
        "The code runs INSIDE Blender with bpy already imported.\n"
        "Required steps:\n"
        "1. Clear scene: bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)\n"
        "2. Build the asset geometry using primitive ops or from_pydata\n"
        "3. Apply solid-color Principled BSDF materials (Roughness=1.0, Specular=0.0)\n"
        "4. Join all parts into one object\n"
        "5. Set origin: obj.location = (0, 0, 0)\n"
        "6. Create export directory: import os; os.makedirs(os.path.dirname(export_path), exist_ok=True)\n"
        "7. Export: bpy.ops.export_scene.gltf(filepath=export_path, export_format='GLB', "
        "export_apply=True, export_materials='EXPORT', export_cameras=False, export_lights=False)\n"
        "For environments: inverted cube using bpy.ops.mesh.primitive_cube_add, scale to (16,9,16), "
        "enter edit mode, select all, bpy.ops.mesh.flip_normals(), exit edit mode.\n"
        "For entities: simple low-poly shape at origin, join all parts.\n"
    )

    user_prompt = (
        f"Asset kind: {asset_kind}\n"
        f"Scene ID: {task.get('scene_id', 'scene')}\n"
        f"Asset ID: {task['asset_id']}\n"
        f"Description: {task.get('description', '')}\n"
        f"Export UNC path: {unc_path}\n"
        "Requirements:\n"
        "- Low poly, solid colors only (no textures)\n"
        "- Roughness=1.0, Specular=0.0\n"
        "- Join all parts before export\n"
        "- Use the exact UNC path above for the GLB export filepath\n"
        "- Return only runnable Python, no markdown fences\n"
    )

    body = json.dumps({
        "model": ollama_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {"temperature": 0},
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{ollama_url.rstrip('/')}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=ollama_timeout) as resp:
            response_data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Ollama request failed: {exc}") from exc

    raw_code = response_data.get("message", {}).get("content", "")
    if not raw_code:
        raise RuntimeError(f"Ollama returned empty content. Full response: {response_data}")
    return raw_code


def _strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = cleaned.rstrip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].rstrip()
    return cleaned.strip()


# ---------------------------------------------------------------------------
# Safe Blender script templates
# ---------------------------------------------------------------------------
# These produce proven-working Blender Python scripts that:
#   - avoid Ollama hallucination bugs
#   - handle Blender 3.x / 4.x Specular input rename
#   - never use multi-line f-string literals (safe on Python < 3.12)
# ---------------------------------------------------------------------------

def _make_material_lines(indent: str = "") -> list[str]:
    """Return the lines of helper functions that create flat-shaded + unlit materials.

    _make_mat: standard Principled BSDF (lit by scene lights) for the floor.
    _make_unlit_mat: emission-dominant material for the sky sphere — renders
    near-uniform regardless of lighting, so the low-poly icosphere facets
    don't show as adjacent-face shading differences.

    Both work on Blender 3.x ('Specular' / 'Emission') and 4.x
    ('Specular IOR Level' / 'Emission Color').
    """
    i = indent
    return [
        f"{i}def _make_mat(name, color):",
        f"{i}    mat = bpy.data.materials.new(name)",
        f"{i}    mat.use_nodes = True",
        f"{i}    bsdf = mat.node_tree.nodes.get('Principled BSDF')",
        f"{i}    bsdf.inputs['Base Color'].default_value = color",
        f"{i}    bsdf.inputs['Roughness'].default_value = 1.0",
        f"{i}    for _k in ('Specular', 'Specular IOR Level'):",
        f"{i}        if _k in bsdf.inputs:",
        f"{i}            bsdf.inputs[_k].default_value = 0.0",
        f"{i}            break",
        f"{i}    return mat",
        f"{i}def _make_unlit_mat(name, color):",
        f"{i}    mat = bpy.data.materials.new(name)",
        f"{i}    mat.use_nodes = True",
        f"{i}    bsdf = mat.node_tree.nodes.get('Principled BSDF')",
        f"{i}    bsdf.inputs['Base Color'].default_value = (0.0, 0.0, 0.0, 1.0)",
        f"{i}    bsdf.inputs['Roughness'].default_value = 1.0",
        f"{i}    for _k in ('Specular', 'Specular IOR Level'):",
        f"{i}        if _k in bsdf.inputs:",
        f"{i}            bsdf.inputs[_k].default_value = 0.0",
        f"{i}            break",
        f"{i}    for _k in ('Emission', 'Emission Color'):",
        f"{i}        if _k in bsdf.inputs:",
        f"{i}            bsdf.inputs[_k].default_value = color",
        f"{i}            break",
        f"{i}    if 'Emission Strength' in bsdf.inputs:",
        f"{i}        bsdf.inputs['Emission Strength'].default_value = 1.0",
        f"{i}    return mat",
    ]


def build_safe_environment_script(
    unc_path: str,
    asset_id: str,
    sky_color: str | None = None,
    ground_color: str | None = None,
    floor_mode: str | None = None,
) -> str:
    """Return Blender Python that builds a low-poly inverted icosphere environment.

    Spherical sky aligns with the equirectangular projection — no corner seams,
    no wall bowing, uniform feel in every direction. Radius 96. Sky uses an
    emission-dominant (unlit-looking) material so the low-poly facets don't
    show adjacent-face shading differences.

    A 128x128 checker floor sits at y = -32, inscribed inside the sphere's
    cross-section (cross-section radius at y=-32 is ~90.5, floor corners at
    distance 64*sqrt(2) ~= 90.5 so they meet the sphere; edges at distance 64
    leave a visible horizon gap). That gap reads as the natural ground-meets-
    sky horizon line.

    sky_color / ground_color are optional CSS hex strings (e.g. "#1a3a5c").
    Falls back to warm defaults when not provided.

    floor_mode selects how the checker floor is coloured:
      - "checker"  (default): tile A = ground_color, tile B = 25%-lighter variant
      - "infinity": both tiles = sky_color, making the floor visually vanish
    Falls back to the A360VG_FLOOR_MODE env var if the param is not passed.
    """
    mode = (floor_mode or os.environ.get("A360VG_FLOOR_MODE", "checker")).lower()
    sky_rgba   = hex_to_rgba(sky_color)   if sky_color   else (0.53, 0.81, 0.98, 1.0)
    if mode == "infinity":
        tile_a = sky_rgba
        tile_b = sky_rgba
    else:
        tile_a = hex_to_rgba(ground_color) if ground_color else (0.85, 0.75, 0.60, 1.0)
        # Tile B: lighten tile A toward white by 25% for retro checker contrast.
        tile_b = tuple(round(tile_a[i] + (1.0 - tile_a[i]) * 0.25, 4) for i in range(3)) + (1.0,)
    sky_str   = f"({sky_rgba[0]}, {sky_rgba[1]}, {sky_rgba[2]}, {sky_rgba[3]})"
    tile_a_str = f"({tile_a[0]}, {tile_a[1]}, {tile_a[2]}, {tile_a[3]})"
    tile_b_str = f"({tile_b[0]}, {tile_b[1]}, {tile_b[2]}, {tile_b[3]})"
    lines = [
        "import bpy",
        "import os",
        "import traceback",
        "",
        f"_log = r\"{unc_path}\" + \".log\"",
        "",
        "# --- clear scene ---",
        "bpy.ops.object.select_all(action='SELECT')",
        "bpy.ops.object.delete(use_global=False)",
        "",
        "# --- inverted icosphere sky (radius 96, subdivisions 3 = 320 faces) ---",
        "# Spherical geometry aligns with the equirectangular projection, so",
        "# there are no corner seams and no wall bowing in VR.",
        "bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=1, location=(0, 0, 0))",
        "sky = bpy.context.active_object",
        f"sky.name = '{asset_id}_sky'",
        "sky.scale = (96, 96, 96)   # uniform radius 96",
        "bpy.ops.object.mode_set(mode='EDIT')",
        "bpy.ops.mesh.select_all(action='SELECT')",
        "bpy.ops.mesh.flip_normals()",
        "bpy.ops.object.mode_set(mode='OBJECT')",
        "",
        "# --- checker floor (16x16 tiles covering glTF 128 x 128, at glTF y=-32) ---",
        "# Inscribed inside sphere cross-section at y=-32 (radius ~90.5).",
        "# Plane is in Blender XY; glTF y=-32 means Blender Z=-32.",
        "bpy.ops.mesh.primitive_plane_add(size=2, location=(0, 0, -32))",
        "floor = bpy.context.active_object",
        f"floor.name = '{asset_id}_floor'",
        "floor.scale = (64, 64, 1)",
        "bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)",
        "bpy.ops.object.mode_set(mode='EDIT')",
        "bpy.ops.mesh.select_all(action='SELECT')",
        "bpy.ops.mesh.subdivide(number_cuts=15)",
        "bpy.ops.object.mode_set(mode='OBJECT')",
        "",
        "# --- materials ---",
        *_make_material_lines(),
        "",
        f"sky.data.materials.append(_make_unlit_mat('{asset_id}_sky_mat', {sky_str}))",
        f"_mat_a = _make_mat('{asset_id}_tile_a', {tile_a_str})",
        f"_mat_b = _make_mat('{asset_id}_tile_b', {tile_b_str})",
        "floor.data.materials.append(_mat_a)",
        "floor.data.materials.append(_mat_b)",
        "",
        "# assign alternating materials by face center (Blender XY for a horizontal plane)",
        "_tx = 8.0  # 128 / 16",
        "_ty = 8.0  # 128 / 16",
        "for _face in floor.data.polygons:",
        "    _c = _face.center",
        "    _col = int((_c.x + 64.0) // _tx)",
        "    _row = int((_c.y + 64.0) // _ty)",
        "    _face.material_index = (_col + _row) % 2",
        "",
        "# --- join parts ---",
        "bpy.ops.object.select_all(action='DESELECT')",
        "sky.select_set(True)",
        "floor.select_set(True)",
        "bpy.context.view_layer.objects.active = sky",
        "bpy.ops.object.join()",
        "sky.location = (0, 0, 0)",
        "",
        "# --- export ---",
        f"_path = r\"{unc_path}\"",
        "try:",
        "    os.makedirs(os.path.dirname(_path), exist_ok=True)",
        "    bpy.ops.export_scene.gltf(",
        "        filepath=_path,",
        "        export_format='GLB',",
        "        export_apply=True,",
        "        export_materials='EXPORT',",
        "        export_cameras=False,",
        "        export_lights=False,",
        "    )",
        "    _ok = os.path.exists(_path)",
        "    _sz = os.path.getsize(_path) if _ok else 0",
        "    open(_log, 'w').write('ok=' + str(_ok) + ' size=' + str(_sz))",
        "except Exception as _e:",
        "    open(_log, 'w').write('error=' + str(_e))",
        "    raise",
    ]
    return "\n".join(lines) + "\n"


def build_safe_entity_script(
    unc_path: str,
    asset_id: str,
    color: str | None = None,
    shape: str | None = None,
) -> str:
    """Return Blender Python that builds a low-poly entity.

    color is an optional CSS hex string. shape is a key from _SHAPE_OPS.
    """
    rgba     = hex_to_rgba(color) if color else (0.4, 0.7, 0.9, 1.0)
    rgba_str = f"({rgba[0]}, {rgba[1]}, {rgba[2]}, {rgba[3]})"
    prim_op  = _SHAPE_OPS.get(shape or "", _DEFAULT_SHAPE_OP)
    lines = [
        "import bpy",
        "import os",
        "import traceback",
        "",
        f"_log = r\"{unc_path}\" + \".log\"",
        "",
        "# --- clear scene ---",
        "bpy.ops.object.select_all(action='SELECT')",
        "bpy.ops.object.delete(use_global=False)",
        "",
        "# --- low-poly entity ---",
        prim_op,
        "entity = bpy.context.active_object",
        f"entity.name = '{asset_id}'",
        "",
        "# --- material ---",
        *_make_material_lines(),
        "",
        f"entity.data.materials.append(_make_mat('{asset_id}_mat', {rgba_str}))",
        "",
        "# --- export ---",
        f"_path = r\"{unc_path}\"",
        "try:",
        "    os.makedirs(os.path.dirname(_path), exist_ok=True)",
        "    bpy.ops.export_scene.gltf(",
        "        filepath=_path,",
        "        export_format='GLB',",
        "        export_apply=True,",
        "        export_materials='EXPORT',",
        "        export_cameras=False,",
        "        export_lights=False,",
        "    )",
        "    _ok = os.path.exists(_path)",
        "    _sz = os.path.getsize(_path) if _ok else 0",
        "    open(_log, 'w').write('ok=' + str(_ok) + ' size=' + str(_sz))",
        "except Exception as _e:",
        "    open(_log, 'w').write('error=' + str(_e))",
        "    raise",
    ]
    return "\n".join(lines) + "\n"


def execute_via_blender_mcp_direct(
    task: dict[str, Any],
    *,
    host: str | None = None,
    port: int | None = None,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """Build a safe Blender script for the task and execute it via the MCP socket.

    Strategy:
    - Use a proven safe template (environment or entity) as the primary script.
      Ollama-generated Blender Python is too unreliable (hallucinated API calls,
      Blender 3.x/4.x naming differences) to use directly.
    - Save the script to output/ollama-blender-scripts/ for inspection.
    - Send via the proven socket protocol (no SHUT_WR).
    - Validate GLB on disk and return a result dict.
    """
    linux_export_path = absolute_asset_path(task["asset_path"])
    unc_path = windows_unc_path(linux_export_path)
    asset_id = slugify(task["asset_id"])

    # Build the safe template script (pass colors / shape from the task)
    if task["task_type"] == "environment":
        code = build_safe_environment_script(
            unc_path, asset_id,
            sky_color=task.get("sky_color"),
            ground_color=task.get("ground_color"),
        )
        script_kind = "safe_environment"
    else:
        code = build_safe_entity_script(
            unc_path, asset_id,
            color=task.get("color"),
            shape=task.get("shape"),
        )
        script_kind = "safe_entity"

    # Save for inspection / replay
    script_dir = PROJECT_ROOT / "output" / "ollama-blender-scripts" / task.get("scene_id", "scene")
    script_dir.mkdir(parents=True, exist_ok=True)
    script_path = script_dir / f"{asset_id}.py"
    script_path.write_text(code, encoding="utf-8")

    # Send to Blender MCP
    mcp_result = execute_blender_mcp_code(
        code,
        host=host,
        port=port,
        timeout_seconds=timeout_seconds,
        expect_response=True,
    )

    # Validate
    asset_exists = linux_export_path.exists() and linux_export_path.stat().st_size > 0
    return {
        "status": "success" if asset_exists else "failed",
        "command": BLENDER_MCP_DIRECT_EXECUTOR,
        "script_kind": script_kind,
        "script_path": str(script_path),
        "mcp_result": mcp_result,
        "asset_exists": asset_exists,
        "expected_path": str(linux_export_path),
        "expected_unc_path": unc_path,
        "returncode": 0 if asset_exists else 1,
        "stdout": mcp_result.get("response", {}).get("message", ""),
        "stderr": "" if asset_exists else str(mcp_result.get("response", {})),
    }


def execute_asset_task(
    task: dict[str, Any],
    *,
    executor_command: str | None = None,
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    global _autorun_count
    asset_validation = validate_asset(task["asset_path"])
    result: dict[str, Any] = {
        "asset_id": task["asset_id"],
        "task_type": task["task_type"],
        "action": task["action"],
        "asset_path": task["asset_path"],
        "target_path": task["target_path"],
    }

    # Safeguard: skip generation if the asset already exists.
    if asset_validation["valid"]:
        return {
            **result,
            "status": "skipped_existing",
            "request_path": None,
            "command": None,
            "returncode": 0,
            "stdout": "",
            "stderr": "",
            **asset_validation,
        }

    if not task_requires_generation(task):
        return {
            **result,
            "status": "reused",
            "request_path": None,
            "command": None,
            "returncode": 0,
            "stdout": "",
            "stderr": "",
            **asset_validation,
        }

    prompt = build_claude_prompt(task)
    request_path = save_handoff_request(task, prompt)
    command = executor_command or executor_command_from_env()
    if not command:
        raise RuntimeError(
            f"No asset executor configured. Set {EXECUTOR_ENV_VAR} to a command that reads a handoff JSON payload from stdin."
        )

    max_autoruns = int(os.getenv(MAX_AUTORUN_ENV, "1"))
    if _autorun_count >= max_autoruns:
        return {
            **result,
            "status": "skipped_autorun_limit",
            "request_path": str(request_path),
            "command": command,
            "returncode": 0,
            "stdout": "",
            "stderr": f"Auto-exec limit reached: {max_autoruns}",
            **asset_validation,
        }

    expected_output_path = absolute_asset_path(task["asset_path"])
    if is_claude_code_executor(command):
        _autorun_count += 1
        claude_result = execute_via_claude_code(
            asset_prompt=prompt,
            expected_output_path=str(expected_output_path),
            timeout=timeout_seconds,
        )
        asset_validation = validate_asset(task["asset_path"])
        return {
            **result,
            "status": "generated" if claude_result["status"] == "success" else claude_result["status"],
            "request_path": str(request_path),
            "command": claude_result["command"],
            "returncode": claude_result["returncode"],
            "stdout": claude_result["stdout"],
            "stderr": claude_result["stderr"],
            "expected_unc_path": claude_result["expected_unc_path"],
            **asset_validation,
        }

    if is_windows_claude_code_executor(command):
        _autorun_count += 1
        claude_result = execute_via_windows_claude_code(
            asset_prompt=prompt,
            expected_output_path=str(expected_output_path),
            timeout=timeout_seconds,
        )
        asset_validation = validate_asset(task["asset_path"])
        return {
            **result,
            "status": "generated" if claude_result["status"] == "success" else claude_result["status"],
            "request_path": str(request_path),
            "command": claude_result["command"],
            "returncode": claude_result["returncode"],
            "stdout": claude_result["stdout"],
            "stderr": claude_result["stderr"],
            "expected_unc_path": claude_result["expected_unc_path"],
            "prompt_file_windows_path": claude_result.get("prompt_file_windows_path"),
            "bridge_script_windows_path": claude_result.get("bridge_script_windows_path"),
            **asset_validation,
        }

    if is_blender_mcp_direct_executor(command):
        _autorun_count += 1
        mcp_host = os.getenv(MCP_HOST_ENV) or None
        mcp_port_str = os.getenv(MCP_PORT_ENV, "")
        mcp_port = int(mcp_port_str) if mcp_port_str.strip() else None
        mcp_result = execute_via_blender_mcp_direct(
            task,
            host=mcp_host,
            port=mcp_port,
            timeout_seconds=timeout_seconds,
        )
        asset_validation = validate_asset(task["asset_path"])
        return {
            **result,
            "status": "generated" if mcp_result["status"] == "success" else mcp_result["status"],
            "request_path": str(request_path),
            "command": mcp_result["command"],
            "returncode": mcp_result.get("returncode"),
            "stdout": mcp_result.get("stdout", ""),
            "stderr": mcp_result.get("stderr", ""),
            "expected_unc_path": mcp_result.get("expected_unc_path"),
            "ollama_model": mcp_result.get("ollama_model"),
            "script_path": mcp_result.get("script_path"),
            "mcp_result": mcp_result.get("mcp_result"),
            **asset_validation,
        }

    payload = {
        "project_root": str(PROJECT_ROOT),
        "task": task,
        "prompt": prompt,
        "request_path": str(request_path),
        "expected_output_path": str(expected_output_path),
        "expected_output_unc_path": windows_unc_path(expected_output_path),
    }
    completed = subprocess.run(
        shlex.split(command),
        input=json.dumps(payload),
        text=True,
        cwd=PROJECT_ROOT,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    asset_validation = validate_asset(task["asset_path"])
    return {
        **result,
        "status": "generated" if completed.returncode == 0 and asset_validation["valid"] else "failed",
        "request_path": str(request_path),
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        **asset_validation,
    }


def execute_asset_tasks(
    tasks: list[dict[str, Any]],
    *,
    executor_command: str | None = None,
    timeout_seconds: int = 900,
) -> list[dict[str, Any]]:
    return [
        execute_asset_task(task, executor_command=executor_command, timeout_seconds=timeout_seconds)
        for task in tasks
    ]


def validate_asset(relative_path: str) -> dict[str, Any]:
    full_path = absolute_asset_path(relative_path)
    exists = full_path.exists()
    size = full_path.stat().st_size if exists else 0
    return {
        "path": str(full_path),
        "exists": exists,
        "size": size,
        "valid": exists and size > 0,
    }


def validate_asset_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []

    for task in tasks:
        asset_path = task.get("asset_path", task["target_path"])
        result = validate_asset(asset_path)
        results.append(
            {
                "asset_id": task["asset_id"],
                "task_type": task["task_type"],
                "action": task.get("action", GENERATE_NEW),
                "asset_path": asset_path,
                "target_path": task["target_path"],
                **result,
            }
        )

    return results


def asset_generation_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph-style node: execute asset tasks through the configured bridge."""
    tasks = state.get("asset_tasks", [])
    results = execute_asset_tasks(
        tasks,
        executor_command=state.get("asset_executor_command"),
        timeout_seconds=state.get("asset_executor_timeout_seconds", 900),
    )
    state["asset_execution_results"] = results
    state["asset_generation_complete"] = all(result["valid"] for result in results)
    return state


def save_json(data: Any, relative_path: str) -> Path:
    output_path = PROJECT_ROOT / relative_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return output_path
