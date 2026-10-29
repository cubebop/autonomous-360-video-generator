"""Autonomous 360 Video Generator scene memory with ChromaDB.

This module provides a small local vector memory layer for scene novelty checks.
It stores completed scene summaries and lets the orchestration graph ask:
"Have we made something semantically similar before?"

The first implementation uses a deterministic hash embedding so it runs locally
without downloading an embedding model. A future Ollama embedding function can
replace `embed_text` without changing the LangGraph node contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import chromadb
from chromadb.api.models.Collection import Collection

DEFAULT_DB_PATH = ".video-generator-memory/chroma"
DEFAULT_COLLECTION = "scene_memory"
DEFAULT_POST_COLLECTION = "post_memory"
EMBEDDING_DIMENSIONS = 384
TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Tokenize text for a simple local embedding."""
    return TOKEN_PATTERN.findall(text.lower())


def embed_text(text: str, dimensions: int = EMBEDDING_DIMENSIONS) -> list[float]:
    """Return a deterministic normalized hash embedding for local prototyping."""
    vector = [0.0] * dimensions

    for token in tokenize(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % dimensions
        vector[bucket] += 1.0

    magnitude = math.sqrt(sum(value * value for value in vector))

    if magnitude == 0:
        return vector

    return [value / magnitude for value in vector]


def manifest_to_memory_text(manifest: dict[str, Any]) -> str:
    """Flatten the useful semantic parts of a manifest into searchable text."""
    environment = manifest.get("environment", {})
    entities = manifest.get("entities", [])
    render = manifest.get("render", {})
    export = manifest.get("export", {})
    parts = [
        f"id: {manifest.get('id', '')}",
        f"title: {manifest.get('title', '')}",
        f"description: {manifest.get('description', '')}",
        f"environment: {environment.get('type', '')}",
        f"background: {render.get('background', '')}",
        f"duration: {export.get('durationSeconds', '')}",
    ]

    for entity in entities:
        asset = entity.get("asset", {})
        physics = entity.get("physics", {})
        parts.extend(
            [
                f"entity id: {entity.get('id', '')}",
                f"label: {entity.get('label', '')}",
                f"asset kind: {asset.get('kind', '')}",
                f"asset path: {asset.get('path', '')}",
                f"primitive: {asset.get('primitive', '')}",
                f"physics: {physics.get('type', '')}",
            ]
        )

    return "\n".join(parts)


class SceneMemory:
    """Local ChromaDB scene memory for novelty checks."""

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        collection_name: str = DEFAULT_COLLECTION,
    ) -> None:
        self.db_path = db_path
        self.client = chromadb.PersistentClient(path=db_path)
        self.collection: Collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def add_manifest(self, manifest: dict[str, Any], source_path: str | None = None) -> dict[str, Any]:
        """Store a completed scene manifest in vector memory."""
        scene_id = manifest.get("id")

        if not scene_id:
            raise ValueError("Manifest must include an id before it can be stored")

        document = manifest_to_memory_text(manifest)
        metadata = {
            "scene_id": scene_id,
            "title": manifest.get("title", ""),
            "source_path": source_path or "",
            "stored_at": datetime.now().isoformat(),
        }

        self.collection.upsert(
            ids=[scene_id],
            documents=[document],
            metadatas=[metadata],
            embeddings=[embed_text(document)],
        )

        return {
            "scene_id": scene_id,
            "stored": True,
            "source_path": source_path,
            "document": document,
        }

    def query(self, text: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Return nearest prior scenes for a proposed scene description."""
        result = self.collection.query(
            query_embeddings=[embed_text(text)],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )
        matches: list[dict[str, Any]] = []

        for index, scene_id in enumerate(result.get("ids", [[]])[0]):
            distance = result.get("distances", [[]])[0][index]
            matches.append(
                {
                    "scene_id": scene_id,
                    "distance": distance,
                    "similarity": max(0.0, 1.0 - distance),
                    "metadata": result.get("metadatas", [[]])[0][index],
                    "document": result.get("documents", [[]])[0][index],
                }
            )

        return matches

    def novelty_gate(
        self,
        candidate_text: str,
        top_k: int = 5,
        similarity_threshold: float = 0.35,
    ) -> dict[str, Any]:
        """Check whether a candidate scene is too similar to existing scenes."""
        matches = self.query(candidate_text, top_k=top_k)
        max_similarity = max((match["similarity"] for match in matches), default=0.0)

        return {
            "candidate": candidate_text,
            "novel": max_similarity < similarity_threshold,
            "max_similarity": max_similarity,
            "similarity_threshold": similarity_threshold,
            "matches": matches,
        }


def post_to_memory_text(scene_id: str, scene_title: str, post_copy: dict[str, Any]) -> str:
    """Flatten the assistant's post variants into searchable text."""
    return "\n".join([
        f"scene_id: {scene_id}",
        f"title: {scene_title}",
        f"x: {post_copy.get('primary_x_post', '')}",
        f"facebook: {post_copy.get('facebook_post', '')}",
        f"instagram: {post_copy.get('instagram_caption', '')}",
        f"youtube: {post_copy.get('youtube_description', '')}",
    ])


class PostMemory:
    """Local ChromaDB collection for past social posts (the assistant's history)."""

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        collection_name: str = DEFAULT_POST_COLLECTION,
    ) -> None:
        self.db_path = db_path
        self.client = chromadb.PersistentClient(path=db_path)
        self.collection: Collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def add_post(
        self,
        scene_id: str,
        post_copy: dict[str, Any],
        scene_title: str = "",
    ) -> dict[str, Any]:
        document = post_to_memory_text(scene_id, scene_title, post_copy)
        metadata = {
            "scene_id": scene_id,
            "title": scene_title,
            "primary_x_post": post_copy.get("primary_x_post", ""),
            "stored_at": datetime.now().isoformat(),
        }
        self.collection.upsert(
            ids=[scene_id],
            documents=[document],
            metadatas=[metadata],
            embeddings=[embed_text(document)],
        )
        return {"scene_id": scene_id, "stored": True, "document": document}

    def recent_posts(self, top_k: int = 5) -> list[dict[str, Any]]:
        raw = self.collection.get(include=["metadatas", "documents"])
        ids   = raw.get("ids", [])
        metas = raw.get("metadatas") or []
        docs  = raw.get("documents") or []
        items: list[dict[str, Any]] = []
        for i, sid in enumerate(ids):
            items.append({
                "scene_id": sid,
                "metadata": metas[i] if i < len(metas) else {},
                "document": docs[i]  if i < len(docs)  else "",
            })
        items.sort(key=lambda s: s["metadata"].get("stored_at", ""), reverse=True)
        return items[:top_k]


def scene_memory_query_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph node: query memory before Ollama finalizes a scene idea."""
    memory = SceneMemory(db_path=state.get("scene_memory_path", DEFAULT_DB_PATH))
    candidate = (
        state.get("candidate_scene_description")
        or state.get("scene_description")
        or state.get("prompt")
        or ""
    )
    gate = memory.novelty_gate(
        candidate_text=candidate,
        top_k=state.get("memory_top_k", 5),
        similarity_threshold=state.get("memory_similarity_threshold", 0.35),
    )

    state["scene_memory_matches"] = gate["matches"]
    state["scene_memory_novel"] = gate["novel"]
    state["scene_memory_max_similarity"] = gate["max_similarity"]
    return state


def scene_memory_store_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph node: store a completed manifest after a successful render."""
    manifest = state.get("manifest")

    if not manifest:
        raise ValueError("state['manifest'] is required to store scene memory")

    memory = SceneMemory(db_path=state.get("scene_memory_path", DEFAULT_DB_PATH))
    result = memory.add_manifest(
        manifest=manifest,
        source_path=state.get("manifest_path"),
    )
    state["scene_memory_stored"] = result
    return state


def load_json(path: str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main() -> None:
    parser = argparse.ArgumentParser(description="Autonomous 360 Video Generator scene memory")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="ChromaDB persistence path")
    parser.add_argument("--add-manifest", help="Manifest JSON path to add to memory")
    parser.add_argument("--query", help="Candidate scene text to query")
    parser.add_argument("--top-k", default=5, type=int, help="Number of matches to return")
    parser.add_argument("--threshold", default=0.35, type=float, help="Novelty similarity threshold")
    args = parser.parse_args()

    memory = SceneMemory(db_path=args.db)

    if args.add_manifest:
        manifest = load_json(args.add_manifest)
        result = memory.add_manifest(manifest, source_path=args.add_manifest)
        print(json.dumps(result, indent=2))

    if args.query:
        result = memory.novelty_gate(
            candidate_text=args.query,
            top_k=args.top_k,
            similarity_threshold=args.threshold,
        )
        print(json.dumps(result, indent=2))

    if not args.add_manifest and not args.query:
        parser.print_help()


if __name__ == "__main__":
    main()
