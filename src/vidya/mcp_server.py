"""MCP server — thin wrapper over the Vidya library.

Each tool maps to 1-3 library calls. No business logic here.
"""

import json
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
    get_task,
)
from vidya.query import cascade_query
from vidya.learn import extract_from_feedback
from vidya.maintain import compute_stats
from vidya.guidance import (
    for_start_task, for_end_task, for_record_step,
    for_query, for_feedback, for_explain, for_stats,
)
from vidya.brief import assemble_brief


_DB_PATH = str(Path.home() / ".vidya" / "vidya.db")

app = Server("vidya")

_db_conn = None


def _get_db():
    global _db_conn
    if _db_conn is None:
        _db_conn = init_db(_DB_PATH)
    return _db_conn


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
                "required": ["goal"],
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
                "required": ["context"],
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
        types.Tool(
            name="vidya_brief",
            description=(
                "Get a structured context dump for the current scope. "
                "Returns project state, items needing attention, and input quality hints. "
                "Call at session start or when you need situational awareness."
            ),
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
    db = _get_db()

    if name == "vidya_start_task":
        task_id = create_task(
            db,
            goal=arguments["goal"],
            language=arguments.get("language"),
            goal_type=arguments.get("goal_type"),
            runtime=arguments.get("runtime"),
            framework=arguments.get("framework"),
            project=arguments.get("project"),
        )
        results = cascade_query(
            db,
            context=arguments["goal"],
            language=arguments.get("language"),
            runtime=arguments.get("runtime"),
            framework=arguments.get("framework"),
            project=arguments.get("project"),
        )
        knowledge = [
            {
                "id": r.id,
                "pattern": r.pattern,
                "guidance": r.guidance,
                "type": r.type,
                "effective_confidence": round(r.effective_confidence, 3),
                "scope_level": r.scope_level,
                "match_reason": r.match_reason,
                "fire_count": 0,  # not available from QueryResult
            }
            for r in results
        ]
        payload = {"task_id": task_id, "knowledge": knowledge}
        payload["_guidance"] = for_start_task(
            knowledge=knowledge, project=arguments.get("project"), db=db,
        )

    elif name == "vidya_end_task":
        end_task(
            db,
            task_id=arguments["task_id"],
            outcome=arguments["outcome"],
            outcome_detail=arguments.get("outcome_detail"),
            failure_type=arguments.get("failure_type"),
        )
        payload = {"ok": True}
        payload["_guidance"] = for_end_task(
            outcome=arguments["outcome"], task_id=arguments["task_id"], db=db,
        )

    elif name == "vidya_record_step":
        alts = arguments.get("alternatives")
        step_id = create_step(
            db,
            task_id=arguments["task_id"],
            action_type="decision",
            action_name=arguments["action"],
            result_status=arguments["outcome"],
            result_output=arguments["result"],
            thought=arguments.get("rationale"),
            alternatives=json.dumps(alts) if alts else None,
        )
        task = get_task(db, arguments["task_id"])
        matched = cascade_query(
            db,
            context=arguments["action"],
            language=task["language"],
            runtime=task.get("runtime"),
            framework=task.get("framework"),
            project=task.get("project"),
        )
        matched_items = [
            {"id": r.id, "pattern": r.pattern, "guidance": r.guidance}
            for r in matched
        ]
        payload = {"step_id": step_id, "matched_items": matched_items}
        payload["_guidance"] = for_record_step(
            outcome=arguments["outcome"], matched_items=matched_items, db=db,
        )

    elif name == "vidya_query":
        results = cascade_query(
            db,
            context=arguments["context"],
            language=arguments.get("language"),
            runtime=arguments.get("runtime"),
            framework=arguments.get("framework"),
            project=arguments.get("project"),
            goal=arguments.get("goal"),
            min_confidence=arguments.get("min_confidence", 0.2),
        )
        items = [
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
        payload = {"items": items}
        payload["_guidance"] = for_query(
            items=items, context=arguments["context"], db=db,
        )

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
        payload["_guidance"] = for_feedback(
            feedback_type=arguments["feedback_type"], learning=result, db=db,
        )

    elif name == "vidya_explain":
        item = get_item(db, arguments["item_id"])
        overridden_by = db.execute(
            "SELECT id, pattern, guidance FROM knowledge_items WHERE overrides = ? AND status = 'active'",
            (arguments["item_id"],),
        ).fetchall()
        overridden_list = [dict(r) for r in overridden_by]
        payload = {"item": item, "overridden_by": overridden_list}
        payload["_guidance"] = for_explain(
            item=item, overridden_by=overridden_list, db=db,
        )

    elif name == "vidya_stats":
        stats = compute_stats(
            db,
            language=arguments.get("language"),
            project=arguments.get("project"),
        )
        stats_payload = {
            "total_items": stats.total_items,
            "by_confidence": stats.by_confidence,
            "by_type": stats.by_type,
            "by_scope": stats.by_scope,
            "total_tasks": stats.total_tasks,
            "total_feedback": stats.total_feedback,
            "total_candidates": stats.total_candidates,
        }
        payload = stats_payload
        payload["_guidance"] = for_stats(stats=stats_payload, db=db)

    elif name == "vidya_brief":
        payload = assemble_brief(
            db,
            language=arguments.get("language"),
            project=arguments.get("project"),
        )

    else:
        payload = {"error": f"Unknown tool: {name}"}

    return [types.TextContent(type="text", text=json.dumps(payload, indent=2))]


def main():
    import asyncio

    async def _run():
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await app.run(read_stream, write_stream, app.create_initialization_options())

    asyncio.run(_run())


if __name__ == "__main__":
    main()
