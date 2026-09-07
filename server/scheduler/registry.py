"""Static task registration for the M8 scheduler (PLAN-M8 §5.2).

Three stable definitions exist: ``daily_organizer`` (default 23:00),
``weekly_review`` (default Sunday 20:00) and the optional
``index_consistency`` interval job (default off).  Registration is derived
from configuration only — no user-supplied prompts, paths or schedules ever
enter here at runtime.
"""

from __future__ import annotations

from server.config import SchedulerSettings

from .models import TASK_IDS, JobDefinition

DEFAULT_TASK_IDS: tuple[TASK_IDS, ...] = (
    "daily_organizer",
    "weekly_review",
    "index_consistency",
)


def build_definitions(settings: SchedulerSettings) -> list[JobDefinition]:
    """Return the definitions to register with the backend.

    ``daily_organizer`` and ``weekly_review`` are cron jobs in the configured
    timezone.  ``index_consistency`` is an interval job that is only present
    when ``index_check_enabled`` is true.
    """
    definitions: list[JobDefinition] = []
    definitions.append(
        JobDefinition(
            job_id="daily_organizer",
            trigger="cron",
            expression=settings.daily_cron,
            timezone=settings.timezone,
            enabled=True,
        )
    )
    definitions.append(
        JobDefinition(
            job_id="weekly_review",
            trigger="cron",
            expression=settings.weekly_cron,
            timezone=settings.timezone,
            enabled=True,
        )
    )
    if settings.index_check_enabled:
        definitions.append(
            JobDefinition(
                job_id="index_consistency",
                trigger="interval",
                expression=str(settings.index_check_interval_hours),
                timezone=settings.timezone,
                enabled=True,
            )
        )
    return definitions


def all_task_statuses(settings: SchedulerSettings) -> list[dict[str, object]]:
    """Status projection for every stable task incl. disabled ones."""
    registered = {item.job_id: item for item in build_definitions(settings)}
    rows: list[dict[str, object]] = []
    for job_id in DEFAULT_TASK_IDS:
        item = registered.get(job_id)  # type: ignore[arg-type]
        fallback_trigger = (
            "interval" if job_id == "index_consistency" else "cron"
        )
        rows.append(
            {
                "id": job_id,
                "enabled": item is not None,
                "trigger": item.trigger if item else fallback_trigger,
                "expression": (
                    item.expression
                    if item
                    else (
                        str(settings.index_check_interval_hours)
                        if job_id == "index_consistency"
                        else (
                            settings.daily_cron
                            if job_id == "daily_organizer"
                            else settings.weekly_cron
                        )
                    )
                ),
                "timezone": settings.timezone,
            }
        )
    return rows


__all__ = ["DEFAULT_TASK_IDS", "all_task_statuses", "build_definitions"]
