"""CLI tool — thin wrapper over the Vidya library."""

import json
from pathlib import Path

import click

from vidya.schema import init_db
from vidya.query import cascade_query
from vidya.store import (
    create_feedback, create_task, end_task, create_step, get_item, get_task,
    _VALID_ACTION_TYPES,
)
from vidya.learn import extract_from_feedback
from vidya.maintain import compute_stats, health_report, auto_archive_stale
from vidya.seed import seed_from_file
from vidya.brief import assemble_brief
from vidya.guidance import (
    for_start_task, for_end_task, for_record_step,
    for_query, for_feedback, for_explain, for_stats, for_maintain,
)
from vidya.evolve import (
    detect_clusters, synthesize_cluster,
    promote_candidate as promote_evolution_candidate,
    reject_candidate,
)


_DB_PATH = str(Path.home() / ".vidya" / "vidya.db")


def _db():
    return init_db(_DB_PATH)


@click.group()
@click.option("--json", "output_json", is_flag=True, default=False,
              help="Output as JSON (machine-readable).")
@click.pass_context
def main(ctx, output_json):
    """Vidya — agent-agnostic procedural learning system."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = output_json


@main.command()
@click.option("--language", default=None)
@click.option("--context", required=True)
@click.option("--runtime", default=None)
@click.option("--framework", default=None)
@click.option("--project", default=None)
@click.option("--goal", default=None)
@click.option("--min-confidence", default=0.2, type=float)
@click.pass_context
def query(ctx, language, context, runtime, framework, project, goal, min_confidence):
    """Query knowledge items relevant to the current context."""
    db = _db()
    results = cascade_query(
        db,
        context=context,
        language=language,
        runtime=runtime,
        framework=framework,
        project=project,
        goal=goal,
        min_confidence=min_confidence,
    )
    if ctx.obj.get("json"):
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
        click.echo(json.dumps({
            "items": items,
            "_guidance": for_query(items=items, context=context, db=db),
        }))
        return
    if not results:
        click.echo("No matching items found.")
        return
    for r in results:
        conf_label = "HIGH" if r.effective_confidence > 0.5 else "MED" if r.effective_confidence >= 0.2 else "LOW"
        click.echo(f"\n[{conf_label} {r.effective_confidence:.2f}] [{r.type}] [{r.scope_level}]")
        click.echo(f"  Pattern:  {r.pattern}")
        click.echo(f"  Guidance: {r.guidance}")
        click.echo(f"  Reason:   {r.match_reason}")


@main.command()
@click.option("--language", default=None)
@click.option("--project", default=None)
@click.pass_context
def stats(ctx, language, project):
    """Show knowledge base statistics."""
    db = _db()
    s = compute_stats(db, language=language, project=project)
    if ctx.obj.get("json"):
        payload = {
            "total_items": s.total_items,
            "by_confidence": s.by_confidence,
            "by_type": s.by_type,
            "by_scope": s.by_scope,
            "total_tasks": s.total_tasks,
            "total_feedback": s.total_feedback,
            "total_candidates": s.total_candidates,
        }
        payload["_guidance"] = for_stats(stats=payload, db=db)
        click.echo(json.dumps(payload))
        return
    click.echo(f"Total items:      {s.total_items}")
    click.echo(f"By confidence:    HIGH={s.by_confidence['high']}  MED={s.by_confidence['medium']}  LOW={s.by_confidence['low']}")
    click.echo(f"By scope:         {s.by_scope}")
    click.echo(f"By type:          {s.by_type}")
    click.echo(f"Total tasks:      {s.total_tasks}")
    click.echo(f"Total feedback:   {s.total_feedback}")
    click.echo(f"Total candidates: {s.total_candidates}")


@main.command()
@click.option("--item-id", required=True)
@click.pass_context
def explain(ctx, item_id):
    """Explain why a knowledge item exists (evidence, confidence, overrides)."""
    db = _db()
    item = get_item(db, item_id)
    overridden_by = db.execute(
        "SELECT id, pattern, guidance FROM knowledge_items WHERE overrides = ? AND status = 'active'",
        (item_id,),
    ).fetchall()
    overridden_list = [dict(r) for r in overridden_by]
    if ctx.obj.get("json"):
        payload = {"item": item, "overridden_by": overridden_list}
        payload["_guidance"] = for_explain(item=item, overridden_by=overridden_list, db=db)
        click.echo(json.dumps(payload, default=str))
        return
    click.echo(json.dumps(item, indent=2, default=str))


@main.command()
@click.option("--file", "file_path", required=True, type=click.Path(exists=True))
@click.option("--language", default=None)
@click.option("--runtime", default=None)
@click.option("--framework", default=None)
@click.option("--project", default=None)
@click.option("--confidence", default=0.5, type=float)
@click.pass_context
def seed(ctx, file_path, language, runtime, framework, project, confidence):
    """Seed knowledge items from a markdown rules file."""
    count = seed_from_file(
        _db(),
        file_path=file_path,
        language=language,
        runtime=runtime,
        framework=framework,
        project=project,
        base_confidence=confidence,
    )
    if ctx.obj.get("json"):
        click.echo(json.dumps({"created": count}))
        return
    click.echo(f"Created {count} knowledge item(s).")


@main.command()
@click.option("--type", "feedback_type", required=True,
              type=click.Choice(["review_accepted", "review_rejected", "test_passed",
                                 "test_failed", "user_correction", "user_confirmation"]))
@click.option("--detail", required=True)
@click.option("--language", default=None)
@click.option("--runtime", default=None)
@click.option("--framework", default=None)
@click.option("--project", default=None)
@click.option("--task-id", default=None)
@click.pass_context
def feedback(ctx, feedback_type, detail, language, runtime, framework, project, task_id):
    """Record feedback and trigger knowledge extraction."""
    db = _db()
    feedback_id = create_feedback(
        db,
        feedback_type=feedback_type,
        detail=detail,
        language=language,
        runtime=runtime,
        framework=framework,
        project=project,
        task_id=task_id,
    )
    feedback_row = db.execute(
        "SELECT * FROM feedback_records WHERE id = ?", (feedback_id,)
    ).fetchone()
    result = extract_from_feedback(db, dict(feedback_row))
    if ctx.obj.get("json"):
        payload = result if result else {"updated": True}
        payload["_guidance"] = for_feedback(
            feedback_type=feedback_type, learning=result, db=db,
        )
        click.echo(json.dumps(payload))
        return
    if result:
        if result.get("decomposed"):
            click.echo(f"Bundle {result['bundle_id']} decomposed. Source items released:")
            for sid in result.get("source_ids", []):
                click.echo(f"  - {sid}")
            click.echo("Re-run feedback to target a specific source item.")
        elif result.get("merged"):
            click.echo(f"Merged into existing item: {result['item_id']}")
        elif result.get("item_id"):
            click.echo(f"Created new knowledge item: {result['item_id']}")
        elif result.get("candidate_id"):
            click.echo(f"Created extraction candidate (pending review): {result['candidate_id']}")
    else:
        click.echo("Updated existing items.")


@main.command()
@click.option("--language", default=None)
@click.option("--project", default=None)
@click.option("--min-confidence", default=0.0, type=float)
@click.pass_context
def items(ctx, language, project, min_confidence):
    """List knowledge items, optionally filtered."""
    db = _db()
    conditions = ["status = 'active'"]
    params = []
    if language:
        conditions.append("language = ?")
        params.append(language)
    if project:
        conditions.append("project = ?")
        params.append(project)
    if min_confidence > 0:
        conditions.append("base_confidence >= ?")
        params.append(min_confidence)
    where = " AND ".join(conditions)
    rows = db.execute(
        f"SELECT id, type, language, project, pattern, guidance, base_confidence "
        f"FROM knowledge_items WHERE {where} ORDER BY base_confidence DESC",
        params,
    ).fetchall()
    if ctx.obj.get("json"):
        click.echo(json.dumps([dict(r) for r in rows]))
        return
    if not rows:
        click.echo("No items found.")
        return
    for row in rows:
        scope = row["project"] or row["language"] or "global"
        click.echo(f"[{row['base_confidence']:.2f}] [{row['type']}] [{scope}] {row['pattern']}")
        click.echo(f"  {row['guidance'][:80]}")


@main.command()
@click.option("--language", default=None)
@click.option("--framework", default=None)
@click.option("--project", default=None)
@click.pass_context
def brief(ctx, language, framework, project):
    """Get a structured context dump: item counts, attention items, input hints."""
    data = assemble_brief(_db(), language=language, framework=framework, project=project)
    if ctx.obj.get("json"):
        click.echo(json.dumps(data))
        return
    state = data["project_state"]
    click.echo(f"Items: {state['total_items']} (HIGH={state['high']} MED={state['medium']} LOW={state['low']})")
    click.echo(f"Types: {state['by_type']}")
    click.echo(f"Tasks: {state['total_tasks']}  Feedback: {state['total_feedback']}")
    if state.get("last_task_outcome"):
        click.echo(f"Last task: {state['last_task_outcome']}")
    attention = data["attention_items"]
    if attention:
        click.echo(f"\nAttention ({len(attention)} item(s)):")
        for a in attention[:5]:
            click.echo(f"  [{a['id'][:8]}] {a['pattern'][:60]}")
            click.echo(f"    {a['reason']}")


@main.group()
def task():
    """Manage task lifecycle (start / end)."""


@task.command("start")
@click.option("--goal", required=True, help="What you intend to accomplish.")
@click.option("--language", default=None)
@click.option("--runtime", default=None)
@click.option("--framework", default=None)
@click.option("--project", default=None)
@click.option("--goal-type", default=None)
@click.pass_context
def task_start(ctx, goal, language, runtime, framework, project, goal_type):
    """Start a task and surface relevant knowledge."""
    db = _db()
    task_id = create_task(
        db,
        goal=goal,
        language=language,
        goal_type=goal_type,
        runtime=runtime,
        framework=framework,
        project=project,
    )
    results = cascade_query(
        db,
        context=goal,
        language=language,
        runtime=runtime,
        framework=framework,
        project=project,
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
        }
        for r in results
    ]
    if ctx.obj.get("json"):
        click.echo(json.dumps({
            "task_id": task_id,
            "knowledge": knowledge,
            "_guidance": for_start_task(knowledge=knowledge, project=project, db=db),
        }))
        return
    click.echo(f"Task: {task_id}")
    if not knowledge:
        click.echo("No matching knowledge items.")
        return
    for k in knowledge:
        conf_label = "HIGH" if k["effective_confidence"] > 0.5 else "MED" if k["effective_confidence"] >= 0.2 else "LOW"
        click.echo(f"\n[{conf_label} {k['effective_confidence']:.2f}] [{k['type']}] {k['pattern']}")
        click.echo(f"  {k['guidance']}")


@task.command("end")
@click.option("--task-id", required=True)
@click.option("--outcome", required=True,
              type=click.Choice(["success", "partial", "failure", "abandoned"]))
@click.option("--detail", "outcome_detail", default=None)
@click.option("--failure-type", default=None)
@click.pass_context
def task_end(ctx, task_id, outcome, outcome_detail, failure_type):
    """Mark a task complete."""
    end_task(
        _db(),
        task_id=task_id,
        outcome=outcome,
        outcome_detail=outcome_detail,
        failure_type=failure_type,
    )
    if ctx.obj.get("json"):
        click.echo(json.dumps({
            "ok": True,
            "outcome": outcome,
            "_guidance": for_end_task(outcome=outcome, task_id=task_id, db=_db()),
        }))
        return
    click.echo(f"Task ended: {outcome}")


@main.command()
@click.option("--task-id", required=True)
@click.option("--action", required=True, help="What was done.")
@click.option("--result", "result_text", required=True, help="What happened.")
@click.option("--outcome", required=True,
              type=click.Choice(["success", "error", "rejected"]))
@click.option("--action-type", default="decision",
              type=click.Choice(sorted(_VALID_ACTION_TYPES)),
              help="Category of action taken.")
@click.option("--rationale", default=None)
@click.pass_context
def step(ctx, task_id, action, result_text, outcome, action_type, rationale):
    """Record a step taken during a task."""
    db = _db()
    step_id = create_step(
        db,
        task_id=task_id,
        action_type=action_type,
        action_name=action,
        result_status=outcome,
        result_output=result_text,
        thought=rationale,
    )
    if ctx.obj.get("json"):
        task = get_task(db, task_id)
        matched = cascade_query(
            db,
            context=action,
            language=task.get("language"),
            runtime=task.get("runtime"),
            framework=task.get("framework"),
            project=task.get("project"),
        )
        matched_items = [{"id": r.id, "pattern": r.pattern, "guidance": r.guidance} for r in matched]
        click.echo(json.dumps({
            "step_id": step_id,
            "matched_items": matched_items,
            "_guidance": for_record_step(outcome=outcome, matched_items=matched_items, db=db),
        }))
        return
    click.echo(f"Step recorded: {step_id}")


@main.command()
@click.option("--language", default=None)
@click.option("--project", default=None)
@click.option("--archive", is_flag=True, default=False,
              help="Include archive recommendations for stale items.")
@click.option("--confirm", is_flag=True, default=False,
              help="Actually archive stale items (requires --archive).")
@click.pass_context
def maintain(ctx, language, project, archive, confirm):
    """Run maintenance: health check, stale item detection, optional archival."""
    if confirm and not archive:
        raise click.UsageError("--confirm requires --archive")
    db = _db()
    report = health_report(db, language=language, project=project)

    archive_result = None
    if archive:
        archive_result = auto_archive_stale(
            db, language=language, project=project, dry_run=not confirm,
        )

    if ctx.obj.get("json"):
        payload = {
            "health": report["health"],
            "total_items": report["total_items"],
            "by_confidence": report["by_confidence"],
            "stale_count": report["stale_count"],
            "stale_items": report["stale_items"],
        }
        if archive_result is not None:
            payload["archive"] = archive_result
        payload["_guidance"] = for_maintain(
            health=report["health"],
            stale_count=report["stale_count"],
            archive_result=archive_result,
        )
        click.echo(json.dumps(payload))
        return

    click.echo(f"Health: {report['health']}")
    click.echo(f"Items:  {report['total_items']} (HIGH={report['by_confidence']['high']} "
               f"MED={report['by_confidence']['medium']} LOW={report['by_confidence']['low']})")
    click.echo(f"Tasks:  {report['total_tasks']}  Feedback: {report['total_feedback']}")
    if report["stale_count"] > 0:
        click.echo(f"\nStale items ({report['stale_count']}):")
        for s in report["stale_items"][:10]:
            click.echo(f"  [{s['base_confidence']:.3f}] {s['pattern'][:60]}")
            click.echo(f"    {s['reason']}")
    if archive_result:
        if archive_result.get("archived_count", 0) > 0:
            click.echo(f"\nArchived {archive_result['archived_count']} item(s).")
        elif archive_result.get("would_archive_count", 0) > 0:
            click.echo(f"\nWould archive {archive_result['would_archive_count']} item(s). "
                       f"Use --confirm to execute.")


@main.command()
@click.option("--language", default=None, help="Scope filter: language.")
@click.option("--framework", default=None, help="Scope filter: framework.")
@click.option("--project", default=None, help="Scope filter: project.")
@click.option("--cluster-only", is_flag=True, default=False,
              help="Show clusters without synthesis.")
@click.option("--dry-run", is_flag=True, default=False,
              help="Synthesize but do not persist candidates.")
@click.option("--review", is_flag=True, default=False,
              help="Interactive review of pending candidates.")
@click.option("--model", default=None, help="Override LLM model for synthesis.")
@click.option("--min-size", default=3, type=int, show_default=True,
              help="Minimum cluster size.")
@click.option("--overlap-threshold", default=0.4, type=float, show_default=True,
              help="Minimum pairwise token overlap to connect items.")
@click.option("--min-cohesion", default=0.5, type=float, show_default=True,
              help="Minimum average pairwise overlap to accept a cluster.")
@click.pass_context
def evolve(ctx, language, framework, project, cluster_only, dry_run, review,
           model, min_size, overlap_threshold, min_cohesion):
    """Detect knowledge clusters and synthesize compound rules."""
    use_json = ctx.obj.get("json")
    db = _db()

    # --- --review mode ---
    if review:
        conditions = ["status = 'pending'"]
        params = []
        if language:
            conditions.append("scope_language = ?")
            params.append(language)
        if framework:
            conditions.append("scope_framework = ?")
            params.append(framework)
        if project:
            conditions.append("scope_project = ?")
            params.append(project)
        where = " AND ".join(conditions)
        rows = db.execute(
            f"SELECT * FROM evolution_candidates WHERE {where} ORDER BY timestamp",
            params,
        ).fetchall()

        if use_json:
            output = []
            for row in rows:
                r = dict(row)
                source_item_ids = json.loads(r.get("source_item_ids") or "[]")
                source_items = []
                for iid in source_item_ids:
                    try:
                        item = get_item(db, iid)
                    except KeyError:
                        continue
                    source_items.append({
                        "id": iid,
                        "pattern": item.get("pattern"),
                        "guidance": item.get("guidance"),
                        "base_confidence": item.get("base_confidence"),
                    })
                output.append({
                    "id": r["id"],
                    "pattern": r["pattern"],
                    "guidance": r["guidance"],
                    "source_items": source_items,
                    "cohesion": r["cohesion_score"],
                    "theme": r["cluster_theme"],
                })
            click.echo(json.dumps(output))
            return

        if not rows:
            click.echo("No pending candidates to review.")
            return

        total = len(rows)
        for idx, row in enumerate(rows, start=1):
            r = dict(row)
            source_item_ids = json.loads(r.get("source_item_ids") or "[]")
            source_items = []
            for iid in source_item_ids:
                try:
                    item = get_item(db, iid)
                except KeyError:
                    continue
                source_items.append(item)

            click.echo(f"\nCandidate {idx} of {total} "
                       f"[cohesion: {r['cohesion_score']:.2f}, "
                       f"{len(source_item_ids)} source items]")
            click.echo(f"Theme: {r['cluster_theme']}")
            click.echo(f"\nSynthesized rule:")
            click.echo(f"  Pattern:  {r['pattern']}")
            click.echo(f"  Guidance: {r['guidance']}")
            if r.get("review_notes"):
                click.echo(f"  Note: {r['review_notes']}")
            click.echo(f"\nSource items:")
            for i, item in enumerate(source_items, start=1):
                conf = item.get("base_confidence", 0.0)
                click.echo(f"  {i}. [{conf:.2f}] {item.get('pattern', '')}")

            while True:
                action = click.prompt(
                    "\nAction: [a]pprove  [e]dit  [r]eject  [s]kip  [q]uit",
                    default="s",
                ).strip().lower()

                if action == "a":
                    bundle_id = promote_evolution_candidate(db, r["id"])
                    click.echo(f"Approved — bundle created: {bundle_id}")
                    break
                elif action == "e":
                    edited_text = click.edit(r["guidance"])
                    if edited_text and edited_text.strip():
                        bundle_id = promote_evolution_candidate(
                            db, r["id"], edited_guidance=edited_text.strip()
                        )
                        click.echo(f"Approved with edits — bundle created: {bundle_id}")
                    else:
                        click.echo("No changes; skipping.")
                    break
                elif action == "r":
                    reject_candidate(db, r["id"])
                    click.echo("Rejected.")
                    break
                elif action == "s":
                    click.echo("Skipped.")
                    break
                elif action == "q":
                    click.echo("Review stopped.")
                    return
                else:
                    click.echo("Unknown action. Use a/e/r/s/q.")
        return

    # --- Detect clusters ---
    clusters = detect_clusters(
        db,
        language=language,
        framework=framework,
        project=project,
        min_size=min_size,
        overlap_threshold=overlap_threshold,
        min_cohesion=min_cohesion,
    )

    if not clusters:
        if use_json:
            click.echo(json.dumps({"clusters": [], "message": "No clusters found."}))
        else:
            click.echo("No clusters found.")
        return

    # --- --cluster-only mode ---
    if cluster_only:
        if use_json:
            click.echo(json.dumps({
                "clusters": [
                    {
                        "item_ids": c.item_ids,
                        "scope": c.scope,
                        "cohesion": round(c.cohesion, 4),
                        "theme_tokens": c.theme_tokens,
                        "size": len(c.item_ids),
                    }
                    for c in clusters
                ]
            }))
        else:
            click.echo(f"Found {len(clusters)} cluster(s).")
            for i, c in enumerate(clusters, start=1):
                click.echo(f"\nCluster {i}: {len(c.item_ids)} item(s), "
                           f"cohesion={c.cohesion:.2f}")
                click.echo(f"  Scope:  {c.scope}")
                click.echo(f"  Theme:  {' '.join(c.theme_tokens)}")
                for iid in c.item_ids:
                    try:
                        item = get_item(db, iid)
                    except KeyError:
                        continue
                    click.echo(f"  - {item.get('pattern', iid)}")
        return

    # --- Full pipeline (or dry-run) ---
    created = []
    for cluster in clusters:
        items = []
        for iid in cluster.item_ids:
            try:
                items.append(get_item(db, iid))
            except KeyError:
                pass
        candidate = synthesize_cluster(cluster, items, db, model=model)
        if candidate is None:
            continue
        if dry_run:
            db.execute(
                "DELETE FROM evolution_candidates WHERE id = ?", (candidate.id,)
            )
            db.commit()
        created.append(candidate)

    if use_json:
        click.echo(json.dumps({
            "dry_run": dry_run,
            "candidates": [
                {
                    "id": c.id,
                    "pattern": c.pattern,
                    "guidance": c.guidance,
                    "cluster_theme": c.cluster_theme,
                    "cohesion_score": round(c.cohesion_score, 4),
                    "source_item_ids": c.source_item_ids,
                    "review_notes": c.review_notes,
                }
                for c in created
            ],
        }))
        return

    if not created:
        click.echo("Clusters found but synthesis failed for all.")
        return

    tag = "[dry-run] " if dry_run else ""
    for candidate in created:
        click.echo(f"\n{tag}Candidate synthesized:")
        click.echo(f"  Pattern:  {candidate.pattern}")
        click.echo(f"  Guidance: {candidate.guidance}")
        click.echo(f"  Theme:    {candidate.cluster_theme}")
        click.echo(f"  Cohesion: {candidate.cohesion_score:.2f}")
        if not dry_run:
            click.echo(f"  ID:       {candidate.id}")
