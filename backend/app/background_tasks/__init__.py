"""Background jobs and the registry that schedules them.

``jobs`` self-register via the :func:`background_task` decorator at import;
``main.py``'s lifespan iterates :func:`all_tasks` and hands each to APScheduler.
Adding a job means writing it in ``jobs.py`` with the decorator — no
``main.py`` change.
"""

from app.background_tasks import jobs as _jobs  # noqa: F401 — registers the jobs
from app.background_tasks.registry import BackgroundTask, all_tasks, background_task

__all__ = ["BackgroundTask", "all_tasks", "background_task"]
