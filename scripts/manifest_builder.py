"""Manifest assembly helpers for Autonomous 360 Video Generator notebooks.

This module converts a scene plan plus validated asset tasks into a renderable
manifest that can be saved under public/manifests and passed directly to the
export pipeline.
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _scene_rng(scene_id: str) -> random.Random:
    """Deterministic per-scene RNG so positions are stable across re-renders of the same scene."""
    seed = int(hashlib.md5(scene_id.encode("utf-8")).hexdigest()[:8], 16)
    return random.Random(seed)


def to_public_path(relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/")
    return normalized[6:] if normalized.startswith("public/") else f"/{normalized.lstrip('/')}"


def build_manifest(scene_plan: dict[str, Any], asset_tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble a render-ready scene manifest from the validated tasks."""
    environment_task = next(task for task in asset_tasks if task["task_type"] == "environment")
    entity_tasks = [task for task in asset_tasks if task["task_type"] == "entity"]
    environment_asset_path = environment_task.get("asset_path", environment_task["target_path"])

    rng = _scene_rng(scene_plan["scene_id"])

    # Pre-compute random per-entity positions and velocities. Entities stagger
    # backward in z (closest = camera-near, furthest = deep) so the render has
    # "always something near, always something far". The stagger cap keeps
    # spawns INSIDE wrapBounds so entities don't teleport on frame 1. Spawns
    # are still near-camera relative to the 192x192 room (not filling the
    # whole depth) so the closest entity passes camera mid-render.
    entity_positions: list[list[float]] = []
    entity_velocities: list[list[float]] = []
    Z_NEAR = -12.0   # closest spawn (mid-depth so first pass feels natural)
    Z_FAR  = -38.0   # furthest spawn (buffer inside wrapBounds z=40)
    z = -18.0
    for _ in entity_tasks:
        if z < Z_FAR:
            z = Z_NEAR - rng.uniform(0.0, 4.0)     # restart near, with jitter
        # y is bracketed around camera.y=4 so entities pass at eye level
        # (equator of the equirectangular image) rather than being pulled toward
        # the nadir (floor) or zenith (top of sphere) as they approach camera.
        entity_positions.append([
            round(rng.uniform(-10.0, 10.0), 2),
            round(rng.uniform(3.0, 5.0), 2),
            round(z, 2),
        ])
        # Slow enough that the closest entity (spawn z ~ -12) takes ~40–80s to
        # reach the camera and the deepest (spawn z ~ -38) stays in frame as
        # distant background for the full ~120s render — procession, not flyby.
        entity_velocities.append([
            round(rng.uniform(0.10, 0.25), 3),
            round(rng.uniform(0.0, 0.02), 3),
            round(rng.uniform(0.15, 0.28), 3),
        ])
        z -= rng.uniform(8.0, 12.0)                # tighter stagger fits within wrap

    return {
        "id": scene_plan["scene_id"],
        "title": scene_plan["title"],
        "description": scene_plan["scene_description"],
        "environment": {
            "type": "model",
            "asset": {
                "kind": "model",
                "path": to_public_path(environment_asset_path),
                "scale": [1, 1, 1],
                "fallback": {
                    "primitive": "box",
                    "color": "#89c8ff",
                },
            },
            "position": [0, 0, 0],
            "rotation": [0, 0, 0],
        },
        "camera": {
            "position": [0, 4, 0],
            "lookAt": [0, 4, -1],
            "fov": 75,
        },
        "render": {
            "background": "#89c8ff",
            "ambientLight": 1.6,
            "directionalLight": 1,
        },
        "export": {
            "fps": 30,
            "width": 3840,
            "height": 1920,
            "durationSeconds": 10,
            "outputName": f"{scene_plan['scene_id']}.mp4",
        },
        "entities": [
            {
                "id": task["asset_id"],
                "label": task["asset_id"].replace("_", " ").title(),
                "asset": {
                    "kind": "model",
                    "path": to_public_path(task.get("asset_path", task["target_path"])),
                    "scale": [1, 1, 1],
                    "fallback": {
                        "primitive": "capsule",
                        "color": "#4f7cff",
                    },
                },
                "position": entity_positions[index],
                "rotation": [0, 0, 0],
                "physics": {
                    "type": "drift",
                    "velocity": entity_velocities[index],
                    "bobAmplitude": 0.18,
                    "bobFrequency": 1,
                    "spin": [0, 0.08, 0],
                    "wrapBounds": [94, 30, 94],
                },
            }
            for index, task in enumerate(entity_tasks)
        ],
    }


def save_manifest(manifest: dict[str, Any]) -> Path:
    output_path = PROJECT_ROOT / "public" / "manifests" / f"{manifest['id']}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return output_path
