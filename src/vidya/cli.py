"""CLI tool — thin wrapper over the Vidya library."""

import json
from pathlib import Path

import click

from vidya.schema import init_db
from vidya.query import cascade_query
from vidya.store import create_feedback, get_item
from vidya.learn import extract_from_feedback
from vidya.maintain import compute_stats
from vidya.seed import seed_from_file


_DB_PATH = str(Path.home() / ".vidya" / "vidya.db")


def _db():
    return init_db(_DB_PATH)


@click.group()
def main():
    """Vidya — agent-agnostic procedural learning system."""


@main.command()
@click.option("--language", required=True)
@click.option("--context", required=True)
@click.option("--runtime", default=None)
@click.option("--framework", default=None)
@click.option("--project", default=None)
@click.option("--goal", default=None)
@click.option("--min-confidence", default=0.2, type=float)
def query(language, context, runtime, framework, project, goal, min_confidence):
    """Query knowledge items relevant to the current context."""
    results = cascade_query(
        _db(),
        context=context,
        language=language,
        runtime=runtime,
        framework=framework,
        project=project,
        goal=goal,
        min_confidence=min_confidence,
    )
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
def stats(language, project):
    """Show knowledge base statistics."""
    s = compute_stats(_db(), language=language, project=project)
    click.echo(f"Total items:      {s.total_items}")
    click.echo(f"By confidence:    HIGH={s.by_confidence['high']}  MED={s.by_confidence['medium']}  LOW={s.by_confidence['low']}")
    click.echo(f"By scope:         {s.by_scope}")
    click.echo(f"By type:          {s.by_type}")
    click.echo(f"Total tasks:      {s.total_tasks}")
    click.echo(f"Total feedback:   {s.total_feedback}")
    click.echo(f"Total candidates: {s.total_candidates}")


@main.command()
@click.option("--item-id", required=True)
def explain(item_id):
    """Explain why a knowledge item exists (evidence, confidence, overrides)."""
    db = _db()
    item = get_item(db, item_id)
    click.echo(json.dumps(item, indent=2, default=str))


@main.command()
@click.option("--file", "file_path", required=True, type=click.Path(exists=True))
@click.option("--language", default=None)
@click.option("--runtime", default=None)
@click.option("--framework", default=None)
@click.option("--project", default=None)
@click.option("--confidence", default=0.5, type=float)
def seed(file_path, language, runtime, framework, project, confidence):
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
def feedback(feedback_type, detail, language, runtime, framework, project, task_id):
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
    if result:
        if result.get("merged"):
            click.echo(f"Merged into existing item: {result['item_id']}")
        else:
            click.echo(f"Created new knowledge item: {result['item_id']}")
    else:
        click.echo("Updated existing items.")


@main.command()
@click.option("--language", default=None)
@click.option("--project", default=None)
@click.option("--min-confidence", default=0.0, type=float)
def items(language, project, min_confidence):
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
    if not rows:
        click.echo("No items found.")
        return
    for row in rows:
        scope = row["project"] or row["language"] or "global"
        click.echo(f"[{row['base_confidence']:.2f}] [{row['type']}] [{scope}] {row['pattern']}")
        click.echo(f"  {row['guidance'][:80]}")
