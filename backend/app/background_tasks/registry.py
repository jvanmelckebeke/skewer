"""Background-task registry.

Ported from fibenchi essentially unchanged — you named it as a keeper, and the
property that makes it one is that a job's *existence* and the app's *wiring*
are decoupled: jobs declare themselves with a decorator, and the lifespan
schedules whatever is registered. Adding a job never touches ``main.py``.

A trigger is either a ready APScheduler trigger or a zero-arg factory resolved
at scheduling time. A factory may return ``None`` to disable just that job
after logging why — the seam that stops one malformed cron expression from
silently taking down every other background job.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from apscheduler.triggers.base import BaseTrigger

logger = logging.getLogger(__name__)

TriggerFactory = Callable[[], "BaseTrigger | None"]


@dataclass(frozen=True)
class BackgroundTask:
    """One scheduled job: its scheduler id, coroutine function, and trigger."""

    id: str
    func: Callable[[], Awaitable[None]]
    trigger: BaseTrigger | TriggerFactory

    def resolve_trigger(self) -> BaseTrigger | None:
        """The trigger to schedule with, or None when the task is disabled
        (the factory is expected to have logged the reason)."""
        if isinstance(self.trigger, BaseTrigger):
            return self.trigger
        return self.trigger()


_REGISTRY: dict[str, BackgroundTask] = {}


def background_task(id: str, trigger: BaseTrigger | TriggerFactory):
    """Register the decorated coroutine function as a scheduled job.

    Stackable: the same function may be registered under several ids with
    different triggers.
    """

    def decorator(func: Callable[[], Awaitable[None]]):
        if id in _REGISTRY:
            raise ValueError(f"Duplicate background task id: {id!r}")
        _REGISTRY[id] = BackgroundTask(id=id, func=func, trigger=trigger)
        return func

    return decorator


def all_tasks() -> list[BackgroundTask]:
    """Every registered task, in registration order."""
    return list(_REGISTRY.values())
