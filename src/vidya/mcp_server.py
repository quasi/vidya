"""MCP server — thin wrapper over the Vidya library.

Each tool maps to 1-3 library calls. No business logic here.
"""

from pathlib import Path

import mcp.server.stdio
import mcp.types as types
from mcp.server import Server

from vidya.schema import init_db
from vidya.store import (
    create_task,
    end_task,
    create_step,
    create_feedback,
    get_item,
)
from vidya.query import cascade_query
from vidya.learn import extract_from_feedback
from vidya.maintain import compute_stats


_DB_PATH = str(Path.home() / ".vidya" / "vidya.db")

app = Server("vidya")


def _get_db():
    return init_db(_DB_PATH)


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="vidya_start_task",
            description="Start a task and get relevant knowledge from past sessions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "goal_type": {"type": "string"},
                    "language": {"type": "string"},
                    "runtime": {"type": "string"},
                    "framework": {"type": "string"},
                    "project": {"type": "string"},
                },
                "required": ["goal", "language"],
            },
        ),
        types.Tool(
            name="vidya_end_task",
            description="Mark a task complete and record its outcome.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "outcome": {"type": "string", "enum": ["success", "partial", "failure", "abandoned"]},
                    "outcome_detail": {"type": "string"},
                    "failure_type": {"type": "string"},
                },
                "required": ["task_id", "outcome"],
            },
        ),
        types.Tool(
            name="vidya_record_step",
            description="Record a step taken during a task.",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "action": {"type": "string"},
                    "result": {"type": "string"},
                    "outcome": {"type": "string", "enum": ["success", "error", "rejected"]},
                    "alternatives": {"type": "array", "items": {"type": "string"}},
                    "rationale": {"type": "string"},
                },
                "required": ["task_id", "action", "result", "outcome"],
            },
        ),
        types.Tool(
            name="vidya_query",
            description="Query relevant knowledge items for the current context.",
            inputSchema={
                "type": "object",
                "properties": {
                    "context": {"type": "string"},
                    "language": {"type": "string"},
                    "runtime": {"type": "string"},
                    "framework": {"type": "string"},
                    "project": {"type": "string"},
                    "goal": {"type": "string"},
                    "min_confidence": {"type": "number", "default": 0.2},
                },
                "required": ["context", "language"],
            },
        ),
        types.Tool(
            name="vidya_feedback",
            description="Record feedback and trigger learning (creates or updates knowledge items).",
            inputSchema={
                "type": "object",
                "properties": {
                    "feedback_type": {
                        "type": "string",
                        "enum": ["review_accepted", "review_rejected", "test_passed",
                                 "test_failed", "user_correction", "user_confirmation"],
                    },
                    "detail": {"type": "string"},
                    "task_id": {"type": "string"},
                    "step_id": {"type": "string"},
                    "language": {"type": "string"},
                    "runtime": {"type": "string"},
                    "framework": {"type": "string"},
                    "project": {"type": "string"},
                },
                "required": ["feedback_type", "detail"],
            },
        ),
        types.Tool(
            name="vidya_explain",
            description="Explain why a knowledge item exists and its evidence trail.",
            inputSchema={
                "type": "object",
                "properties": {"item_id": {"type": "string"}},
                "required": ["item_id"],
            },
        ),
        types.Tool(
            name="vidya_stats",
            description="Get knowledge base statistics.",
            inputSchema={
                "type": "object",
                "properties": {
                    "language": {"type": "string"},
                    "project": {"type": "string"},
                },
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    import json
    db = _get_db()

    if name == "vidya_start_task":
        task_id = create_task(
            db,
            goal=arguments["goal"],
            language=arguments["language"],
            goal_type=arguments.get("goal_type"),
            runtime=arguments.get("runtime"),
            framework=arguments.get("framework"),
            project=arguments.get("project"),
        )
        results = cascade_query(
            db,
            context=arguments["goal"],
            language=arguments["language"],
            runtime=arguments.get("runtime"),
            framework=arguments.get("framework"),
            project=arguments.get("project"),
        )
        payload = {
            "task_id": task_id,
            "knowledge": [
                {
                    "id": r.id,
                    "pattern": r.pattern,
                    "guidance": r.guidance,
                    "type": r.type,
                    "effective_confidence": round(r.effective_confidence, 3),
                    "scope_level": r.scope_level,
                    "match_reason": r.match_reason,
                }
                for r in results
            ],
        }

    elif name == "vidya_end_task":
        end_task(
            db,
            task_id=arguments["task_id"],
            outcome=arguments["outcome"],
            outcome_detail=arguments.get("outcome_detail"),
            failure_type=arguments.get("failure_type"),
        )
        payload = {"ok": True}

    elif name == "vidya_record_step":
        import json as _json
        alts = arguments.get("alternatives")
        step_id = create_step(
            db,
            task_id=arguments["task_id"],
            action_type="decision",
            action_name=arguments["action"],
            result_status=arguments["outcome"],
            result_output=arguments["result"],
            thought=arguments.get("rationale"),
            alternatives=_json.dumps(alts) if alts else None,
        )
        matched = cascade_query(
            db,
            context=arguments["action"],
            language="unknown",  # step doesn't carry language — caller should use vidya_query
        )
        payload = {
            "step_id": step_id,
            "matched_items": [
                {"id": r.id, "pattern": r.pattern, "guidance": r.guidance}
                for r in matched
            ],
        }

    elif name == "vidya_query":
        results = cascade_query(
            db,
            context=arguments["context"],
            language=arguments["language"],
            runtime=arguments.get("runtime"),
            framework=arguments.get("framework"),
            project=arguments.get("project"),
            goal=arguments.get("goal"),
            min_confidence=arguments.get("min_confidence", 0.2),
        )
        payload = {
            "items": [
                {
                    "id": r.id,
                    "pattern": r.pattern,
                    "guidance": r.guidance,
                    "type": r.type,
                    "effective_confidence": round(r.effective_confidence, 3),
                    "scope_level": r.scope_level,
                    "match_reason": r.match_reason,
                }
                for r in results
            ]
        }

    elif name == "vidya_feedback":
        feedback_id = create_feedback(
            db,
            feedback_type=arguments["feedback_type"],
            detail=arguments["detail"],
            task_id=arguments.get("task_id"),
            step_id=arguments.get("step_id"),
            language=arguments.get("language"),
            runtime=arguments.get("runtime"),
            framework=arguments.get("framework"),
            project=arguments.get("project"),
        )
        feedback_row = db.execute(
            "SELECT * FROM feedback_records WHERE id = ?", (feedback_id,)
        ).fetchone()
        result = extract_from_feedback(db, dict(feedback_row))
        payload = {"feedback_id": feedback_id, "learning": result}

    elif name == "vidya_explain":
        item = get_item(db, arguments["item_id"])
        # Fetch any items that override this one
        overridden_by = db.execute(
            "SELECT id, pattern, guidance FROM knowledge_items WHERE overrides = ? AND status = 'active'",
            (arguments["item_id"],),
        ).fetchall()
        payload = {
            "item": item,
            "overridden_by": [dict(r) for r in overridden_by],
        }

    elif name == "vidya_stats":
        stats = compute_stats(
            db,
            language=arguments.get("language"),
            project=arguments.get("project"),
        )
        payload = {
            "total_items": stats.total_items,
            "by_confidence": stats.by_confidence,
            "by_type": stats.by_type,
            "by_scope": stats.by_scope,
            "total_tasks": stats.total_tasks,
            "total_feedback": stats.total_feedback,
            "total_candidates": stats.total_candidates,
        }

    else:
        payload = {"error": f"Unknown tool: {name}"}

    return [types.TextContent(type="text", text=json.dumps(payload, indent=2))]


def main():
    import asyncio
    asyncio.run(mcp.server.stdio.stdio_server(app))


if __name__ == "__main__":
    main()
