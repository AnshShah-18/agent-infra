from __future__ import annotations

import os
from typing import Any

import weaviate
from loguru import logger
from weaviate.classes.config import Configure, Property, DataType
from weaviate.classes.query import MetadataQuery


COLLECTION_NAME = "EpisodicMemory"


class MemoryClient:
    """
    Stores and retrieves agent episodic memory in Weaviate.

    Each memory entry contains:
      - agent_id   : which agent stored it
      - goal_id    : which goal it relates to
      - content    : the text content (vectorized)
      - metadata   : arbitrary JSON string
    """

    def __init__(self) -> None:
        self._client = weaviate.connect_to_local(
            host=os.getenv("WEAVIATE_HOST", "localhost"),
            port=int(os.getenv("WEAVIATE_PORT", "8080")),
        )
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        if self._client.collections.exists(COLLECTION_NAME):
            self._collection = self._client.collections.get(COLLECTION_NAME)
            return
        self._collection = self._client.collections.create(
            name=COLLECTION_NAME,
            vector_config=Configure.Vectors.text2vec_transformers(),
            properties=[
                Property(name="agent_id", data_type=DataType.TEXT),
                Property(name="goal_id", data_type=DataType.TEXT),
                Property(name="content", data_type=DataType.TEXT),
                Property(name="meta", data_type=DataType.TEXT),
            ],
        )
        logger.info(f"Weaviate collection created: {COLLECTION_NAME}")

    def store(self, agent_id: str, goal_id: str, content: str, meta: dict[str, Any] | None = None) -> str:
        import json
        uuid = self._collection.data.insert(
            {
                "agent_id": agent_id,
                "goal_id": goal_id,
                "content": content,
                "meta": json.dumps(meta or {}),
            }
        )
        logger.debug(f"Memory stored: {uuid} ({agent_id}/{goal_id})")
        return str(uuid)

    def search(self, query: str, agent_id: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        filters = None
        if agent_id:
            from weaviate.classes.query import Filter
            filters = Filter.by_property("agent_id").equal(agent_id)

        results = self._collection.query.near_text(
            query=query,
            limit=limit,
            filters=filters,
            return_metadata=MetadataQuery(distance=True),
        )
        return [
            {
                "content": o.properties["content"],
                "goal_id": o.properties["goal_id"],
                "agent_id": o.properties["agent_id"],
                "distance": o.metadata.distance,
            }
            for o in results.objects
        ]

    def count_by_goal(self, goal_id: str) -> dict[str, int]:
        """Return the number of memory entries per agent_id for a given goal."""
        from weaviate.classes.query import Filter
        results = self._collection.query.fetch_objects(
            filters=Filter.by_property("goal_id").equal(goal_id),
            limit=200,
        )
        counts: dict[str, int] = {}
        for obj in results.objects:
            aid = obj.properties.get("agent_id", "unknown")
            counts[aid] = counts.get(aid, 0) + 1
        return counts

    def fetch_all_by_goal(self, goal_id: str, agent_id: str | None = None) -> list[dict[str, Any]]:
        """Fetch all memory entries for a goal, optionally filtered by agent_id."""
        from weaviate.classes.query import Filter
        f = Filter.by_property("goal_id").equal(goal_id)
        if agent_id:
            f = f & Filter.by_property("agent_id").equal(agent_id)
        results = self._collection.query.fetch_objects(filters=f, limit=200)
        return [
            {
                "agent_id": obj.properties.get("agent_id", ""),
                "goal_id": obj.properties.get("goal_id", ""),
                "content": obj.properties.get("content", ""),
            }
            for obj in results.objects
        ]

    def fetch_summary(self, goal_id: str) -> str | None:
        """Return the final summary stored by the summarizer for a goal, or None."""
        from weaviate.classes.query import Filter
        results = self._collection.query.fetch_objects(
            filters=(
                Filter.by_property("goal_id").equal(goal_id)
                & Filter.by_property("agent_id").equal("summarizer")
            ),
            limit=1,
        )
        if not results.objects:
            return None
        content: str = results.objects[0].properties.get("content", "")
        marker = "Final Summary:\n"
        idx = content.find(marker)
        return content[idx + len(marker):].strip() if idx != -1 else content

    def close(self) -> None:
        self._client.close()
