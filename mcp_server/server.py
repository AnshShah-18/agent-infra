"""
MCP Server — Agent Infrastructure Tools
----------------------------------------
Exposes the agent pipeline as callable MCP tools so Claude Desktop (or any
MCP client) can submit goals, query memory, and retrieve final summaries.

Run:
    python -m mcp_server.server
"""
from __future__ import annotations

import asyncio
import json
import os

from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

load_dotenv()

server = Server("agent-infra")

# ---------------------------------------------------------------------------
# Lazy singletons — initialised on first use so the server starts instantly
# ---------------------------------------------------------------------------

_publisher = None
_memory = None


def _get_publisher():
    global _publisher
    if _publisher is None:
        from core.kafka import KafkaPublisher
        _publisher = KafkaPublisher()
    return _publisher


def _get_memory():
    global _memory
    if _memory is None:
        from core.memory import MemoryClient
        _memory = MemoryClient()
    return _memory


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    types.Tool(
        name="submit_goal",
        description=(
            "Submit a high-level goal to the agent pipeline. "
            "The Planner will decompose it into tasks for the Executor. "
            "Returns the goal_id you can use to track progress."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "The goal for the agent pipeline to work on.",
                }
            },
            "required": ["description"],
        },
    ),
    types.Tool(
        name="get_goal_status",
        description=(
            "Check how many Weaviate memory entries exist for a goal, broken down "
            "by agent (planner, executor, critic, summarizer). Useful for tracking "
            "pipeline progress after submitting a goal."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "goal_id": {
                    "type": "string",
                    "description": "The goal_id returned by submit_goal.",
                }
            },
            "required": ["goal_id"],
        },
    ),
    types.Tool(
        name="search_memory",
        description=(
            "Semantic search over all episodic memory stored in Weaviate. "
            "Optionally filter to a specific agent's memories."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language search query.",
                },
                "agent_id": {
                    "type": "string",
                    "description": (
                        "Optional. Filter results to a specific agent: "
                        "'planner', 'executor', 'critic', or 'summarizer'."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 5).",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name="get_final_summary",
        description=(
            "Retrieve the final synthesized answer produced by the Summarizer agent "
            "for a completed goal. Returns null if the goal is not yet summarized."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "goal_id": {
                    "type": "string",
                    "description": "The goal_id to retrieve the summary for.",
                }
            },
            "required": ["goal_id"],
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return TOOLS


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "submit_goal":
        return await _submit_goal(arguments["description"])
    elif name == "get_goal_status":
        return await _get_goal_status(arguments["goal_id"])
    elif name == "search_memory":
        return await _search_memory(
            arguments["query"],
            arguments.get("agent_id"),
            arguments.get("limit", 5),
        )
    elif name == "get_final_summary":
        return await _get_final_summary(arguments["goal_id"])
    else:
        raise ValueError(f"Unknown tool: {name}")


async def _submit_goal(description: str) -> list[types.TextContent]:
    from core.models import Goal

    goal = Goal(description=description)
    goal_topic = "goals.submitted"

    def _publish():
        pub = _get_publisher()
        pub.publish(goal_topic, goal, key=goal.goal_id)
        pub.flush()

    await asyncio.to_thread(_publish)
    result = {"goal_id": goal.goal_id, "description": description, "status": "submitted"}
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


async def _get_goal_status(goal_id: str) -> list[types.TextContent]:
    def _query():
        return _get_memory().count_by_goal(goal_id)

    counts = await asyncio.to_thread(_query)
    total = sum(counts.values())
    result = {
        "goal_id": goal_id,
        "total_memory_entries": total,
        "by_agent": counts,
        "pipeline_stages_seen": list(counts.keys()),
    }
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


async def _search_memory(query: str, agent_id: str | None, limit: int) -> list[types.TextContent]:
    def _query():
        return _get_memory().search(query=query, agent_id=agent_id, limit=limit)

    hits = await asyncio.to_thread(_query)
    return [types.TextContent(type="text", text=json.dumps(hits, indent=2))]


async def _get_final_summary(goal_id: str) -> list[types.TextContent]:
    def _query():
        return _get_memory().fetch_summary(goal_id)

    summary = await asyncio.to_thread(_query)
    if summary is None:
        result = {
            "goal_id": goal_id,
            "status": "not_ready",
            "summary": None,
            "message": "No final summary found. The goal may still be processing.",
        }
    else:
        result = {"goal_id": goal_id, "status": "complete", "summary": summary}
    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
