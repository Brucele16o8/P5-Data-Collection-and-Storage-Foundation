"""Workflow observer helpers for progress, logging, and audit boundaries."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from glamira_aws.progress import ProgressReporter


@dataclass(frozen=True)
class WorkflowEvent:
    """A small event object emitted by application workflows."""

    name: str
    count: int | None = None
    total: int | None = None
    message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


class WorkflowObserver(Protocol):
    """Observer interface for workflow lifecycle/progress events."""

    def on_event(self, event: WorkflowEvent) -> None:
        """Handle one workflow event."""


class NoOpWorkflowObserver:
    """Default observer used when callers do not need progress or audit output."""

    def on_event(self, event: WorkflowEvent) -> None:
        return


class CompositeWorkflowObserver:
    """Composite observer that fans one event out to multiple observers."""

    def __init__(self, observers: list[WorkflowObserver]) -> None:
        self._observers = observers

    def on_event(self, event: WorkflowEvent) -> None:
        for observer in self._observers:
            observer.on_event(event)


class LoggingWorkflowObserver:
    """Structured logging observer for local runs and CloudWatch stdout logs."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(__name__)

    def on_event(self, event: WorkflowEvent) -> None:
        payload = {
            "event": event.name,
            "count": event.count,
            "total": event.total,
            **event.details,
        }
        self._logger.info(event.message or event.name, extra=payload)


class TerminalProgressObserver:
    """Progress observer that keeps terminal output out of core workflows."""

    def __init__(self, label: str, total: int | None = None, every: int = 1000) -> None:
        self._progress = ProgressReporter(label, total=total, every=every)

    def on_event(self, event: WorkflowEvent) -> None:
        if event.count is None:
            return
        suffix = ""
        if event.details:
            suffix = ", ".join(f"{key}={value}" for key, value in event.details.items())
        self._progress.report(event.count, force=event.name.endswith("completed"), suffix=suffix)
