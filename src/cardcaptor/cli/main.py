"""Typer-based command line interface."""

from __future__ import annotations

import asyncio
import json
import webbrowser
from pathlib import Path
from typing import Optional

import typer

from ..config import get_config
from ..db.models import Activity
from ..db.session import get_session, init_db
from ..services import activity as activity_service
from ..services import canvas as canvas_service
from ..services import export as export_service
from ..services import import_ as import_service
from ..services import processing as processing_service
from ..services import review as review_service
from ..services import roster as roster_service

app = typer.Typer(
    help="card-captor: process student index card quizzes entirely on your machine.",
    no_args_is_help=True,
)


def _session():
    init_db()
    return get_session()


def _resolve(session, activity_id: Optional[int], activity_name: Optional[str]) -> Activity:
    try:
        return activity_service.resolve_activity(session, activity_id, activity_name)
    except LookupError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1)


@app.command("create-course")
def create_course(
    name: str = typer.Option(..., "--name", help="Course name, e.g. 'CS 101'"),
    term: str = typer.Option("", "--term", help="Term, e.g. 'Fall 2026'"),
    canvas_course_id: Optional[str] = typer.Option(None, "--canvas-course-id"),
    email_domain: Optional[str] = typer.Option(
        None, "--email-domain", help="Expected student email domain, e.g. university.edu"
    ),
) -> None:
    """Create a new course."""
    with _session() as session:
        course = activity_service.create_course(
            session, name=name, term=term, canvas_course_id=canvas_course_id,
            email_domain=email_domain,
        )
        typer.secho(f"Created course {course.id}: {course.name} ({course.term})", fg=typer.colors.GREEN)


@app.command("create")
def create_activity(
    course_id: int = typer.Option(..., "--course-id"),
    name: str = typer.Option(..., "--name"),
    date: Optional[str] = typer.Option(None, "--date", help="YYYY-MM-DD"),
    points: float = typer.Option(1.0, "--points"),
    answers: Optional[str] = typer.Option(
        None, "--answers", help="Comma separated accepted answers, e.g. 'B' or '3.14,3.1416'"
    ),
    rule_type: Optional[str] = typer.Option(
        None, "--rule-type", "--rule",
        help="exact|case_insensitive|multi_accept|numeric|mcq",
    ),
    tolerance: Optional[float] = typer.Option(None, "--tolerance", help="Numeric tolerance"),
    normalize: Optional[str] = typer.Option(
        None,
        "--normalize",
        help="Comma separated normalization steps: strip,lower,upper,remove_spaces,"
        "remove_punctuation,collapse_spaces",
    ),
    choices: Optional[str] = typer.Option(
        None, "--choices", help="Comma separated valid MCQ choices, e.g. 'A,B,C,D'"
    ),
    canvas_assignment_id: Optional[str] = typer.Option(None, "--canvas-assignment-id"),
    canvas_mode: Optional[str] = typer.Option(
        None, "--canvas-mode", help="direct|accumulated"
    ),
) -> None:
    """Create a new activity."""
    accepted = [a.strip() for a in (answers or "").split(",") if a.strip()]
    rules: dict = {}
    if rule_type:
        rules["rule_type"] = rule_type
    if tolerance is not None:
        rules["numeric_tolerance"] = tolerance
    if normalize:
        rules["normalization"] = [n.strip() for n in normalize.split(",") if n.strip()]
    if choices:
        rules["choices"] = [c.strip() for c in choices.split(",") if c.strip()]
    if accepted:
        rules["accepted_answers"] = accepted

    with _session() as session:
        activity = activity_service.create_activity(
            session,
            course_id=course_id,
            name=name,
            date=date,
            point_value=points,
            expected_answers=accepted,
            grading_rules=rules,
            canvas_assignment_id=canvas_assignment_id,
            canvas_assignment_mode=canvas_mode,
        )
        typer.secho(f"Created activity {activity.id}: {activity.name}", fg=typer.colors.GREEN)


@app.command("roster")
def import_roster(
    csv_path: Path = typer.Option(..., "--csv", exists=True, help="Roster CSV file"),
    activity_id: Optional[int] = typer.Option(None, "--activity-id"),
    activity_name: Optional[str] = typer.Option(None, "--activity-name"),
    course_id: Optional[int] = typer.Option(None, "--course-id"),
) -> None:
    """Import a student roster CSV into a course."""
    with _session() as session:
        if course_id is None:
            activity = _resolve(session, activity_id, activity_name)
            course_id = activity.course_id
        count, warnings = roster_service.import_roster_from_csv(csv_path, course_id, session)
        typer.secho(f"Imported {count} students into course {course_id}", fg=typer.colors.GREEN)
        for warning in warnings:
            typer.secho(f"  warning: {warning}", fg=typer.colors.YELLOW)


@app.command("import")
def import_images(
    activity_id: Optional[int] = typer.Option(None, "--activity-id"),
    activity_name: Optional[str] = typer.Option(None, "--activity-name"),
    folder: Optional[Path] = typer.Option(None, "--folder"),
    zip_file: Optional[Path] = typer.Option(None, "--zip"),
    url: Optional[str] = typer.Option(None, "--url"),
) -> None:
    """Import photographs from a folder, ZIP archive or URL."""
    if not any([folder, zip_file, url]):
        typer.secho("Provide one of --folder, --zip or --url", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    with _session() as session:
        activity = _resolve(session, activity_id, activity_name)
        if folder:
            result = import_service.import_from_folder(session, activity.id, folder)
        elif zip_file:
            result = import_service.import_from_zip(session, activity.id, zip_file)
        else:
            result = import_service.import_from_url(session, activity.id, url or "")

        typer.secho(
            f"Imported {result.count} image(s); "
            f"{result.skipped_duplicates} duplicate(s) skipped",
            fg=typer.colors.GREEN,
        )
        for rejected in result.rejected:
            typer.secho(f"  rejected: {rejected}", fg=typer.colors.YELLOW)


@app.command("process")
def process(
    activity_id: Optional[int] = typer.Option(None, "--activity-id"),
    activity_name: Optional[str] = typer.Option(None, "--activity-name"),
    force: bool = typer.Option(False, "--force", help="Reprocess already processed cards"),
) -> None:
    """Run the detection + OCR + grading pipeline."""
    with _session() as session:
        activity = _resolve(session, activity_id, activity_name)
        result = processing_service.process_activity(
            session, activity, force=force, progress=lambda msg: typer.echo(f"  {msg}")
        )
        typer.secho(
            f"Processed {result.images_processed} image(s), "
            f"{result.cards_detected} card(s), "
            f"{result.submissions_created} new submission(s); "
            f"{result.needs_review} need review",
            fg=typer.colors.GREEN,
        )
        for error in result.errors:
            typer.secho(f"  error: {error}", fg=typer.colors.RED)


@app.command("status")
def status(
    activity_id: Optional[int] = typer.Option(None, "--activity-id"),
    activity_name: Optional[str] = typer.Option(None, "--activity-name"),
    as_json: bool = typer.Option(False, "--json", help="Print raw JSON"),
) -> None:
    """Show a summary of an activity."""
    with _session() as session:
        activity = _resolve(session, activity_id, activity_name)
        summary = activity_service.activity_status(session, activity.id)
        if as_json:
            typer.echo(json.dumps(summary, indent=2))
            return
        typer.secho(f"Activity {summary['activity_id']}: {summary['activity_name']}", bold=True)
        for key, value in summary.items():
            if key in ("activity_id", "activity_name"):
                continue
            typer.echo(f"  {key.replace('_', ' ')}: {value}")


@app.command("review")
def review(
    activity_id: Optional[int] = typer.Option(None, "--activity-id"),
    activity_name: Optional[str] = typer.Option(None, "--activity-name"),
    filter_: str = typer.Option("needs_review", "--filter", help="needs_review|unmatched|all|..."),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the web UI"),
) -> None:
    """List the review queue and launch the web review UI."""
    config = get_config()
    with _session() as session:
        activity = _resolve(session, activity_id, activity_name)
        queue = review_service.build_review_queue(activity.id, session, {"filter": filter_})
        typer.secho(f"{len(queue)} submission(s) matching filter {filter_!r}", bold=True)
        for submission in queue[:50]:
            flags = ",".join(review_service.flags_from_json(submission.review_flags)) or "-"
            typer.echo(
                f"  #{submission.id} name={submission.effective_name!r} "
                f"answer={submission.effective_answer!r} score={submission.effective_score:g} "
                f"flags={flags}"
            )
        url = f"http://{config.web_host}:{config.web_port}/activities/{activity.id}/review"

    typer.echo(f"Review UI: {url}")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # pragma: no cover - headless environments
            pass
    typer.echo("Start the server with: quizcards web")


@app.command("export")
def export(
    activity_id: Optional[int] = typer.Option(None, "--activity-id"),
    activity_name: Optional[str] = typer.Option(None, "--activity-name"),
    output: Optional[Path] = typer.Option(None, "--output", help="Output directory"),
) -> None:
    """Export the Canvas grade CSV and the audit CSV."""
    config = get_config()
    with _session() as session:
        activity = _resolve(session, activity_id, activity_name)
        target = Path(output) if output else config.exports_dir
        canvas_path, audit_path = export_service.export_both(activity.id, session, target)
        typer.secho(f"Canvas CSV: {canvas_path}", fg=typer.colors.GREEN)
        typer.secho(f"Audit CSV : {audit_path}", fg=typer.colors.GREEN)


@app.command("canvas-upload")
def canvas_upload(
    activity_id: Optional[int] = typer.Option(None, "--activity-id"),
    activity_name: Optional[str] = typer.Option(None, "--activity-name"),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
) -> None:
    """Upload grades to Canvas (dry run by default)."""
    with _session() as session:
        activity = _resolve(session, activity_id, activity_name)
        try:
            result, warnings = asyncio.run(
                canvas_service.upload_activity_grades(session, activity, dry_run=dry_run)
            )
        except ValueError as exc:
            typer.secho(str(exc), fg=typer.colors.RED)
            raise typer.Exit(code=1)

        prefix = "[dry-run] " if result.dry_run else ""
        if result.skipped:
            typer.secho(
                f"{prefix}Payload unchanged since the last upload - nothing to do.",
                fg=typer.colors.YELLOW,
            )
        typer.secho(
            f"{prefix}{result.success_count} grade(s) ok, {result.error_count} error(s); "
            f"payload {result.payload_hash[:12]}",
            fg=typer.colors.GREEN if result.error_count == 0 else typer.colors.RED,
        )
        for warning in warnings:
            typer.secho(f"  warning: {warning}", fg=typer.colors.YELLOW)
        for error in result.errors:
            typer.secho(f"  error: {error}", fg=typer.colors.RED)


@app.command("web")
def web(
    host: Optional[str] = typer.Option(None, "--host", help="Defaults to 127.0.0.1"),
    port: Optional[int] = typer.Option(None, "--port"),
    reload: bool = typer.Option(False, "--reload"),
) -> None:
    """Start the local web server."""
    import uvicorn

    config = get_config()
    init_db()
    bind_host = host or config.web_host
    bind_port = int(port or config.web_port)
    if bind_host not in ("127.0.0.1", "localhost", "::1"):
        typer.secho(
            f"Warning: binding to {bind_host} exposes the app beyond this machine.",
            fg=typer.colors.YELLOW,
        )
    typer.echo(f"card-captor web UI: http://{bind_host}:{bind_port}")
    uvicorn.run("cardcaptor.web.app:app", host=bind_host, port=bind_port, reload=reload)


@app.command("list")
def list_items(
    courses: bool = typer.Option(False, "--courses", help="List courses instead of activities"),
    course_id: Optional[int] = typer.Option(None, "--course-id"),
) -> None:
    """List courses or activities."""
    with _session() as session:
        if courses:
            for course in activity_service.list_courses(session):
                typer.echo(f"{course.id}\t{course.name}\t{course.term}")
            return
        for item in activity_service.list_activities(session, course_id):
            typer.echo(f"{item.id}\t{item.name}\t{item.date}\t{item.point_value} pt")


def main() -> None:  # pragma: no cover - console-script entry point
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
