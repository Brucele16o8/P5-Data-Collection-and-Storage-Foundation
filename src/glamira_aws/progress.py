"""Small terminal progress helper for long-running local and EC2 scripts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any


def format_duration(seconds: float) -> str:
    seconds_int = int(seconds)
    hours, remainder = divmod(seconds_int, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def write_summary_json(path: str | Path, payload: dict[str, Any]) -> None:
    output_path = Path(path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


@dataclass
class ProgressReporter:
    label: str
    total: int | None = None
    every: int = 1000

    def __post_init__(self) -> None:
        self.started_at = monotonic()
        self.last_reported = 0
        self.report(0, force=True)

    def elapsed_seconds(self) -> float:
        return monotonic() - self.started_at

    def report(self, count: int, force: bool = False, suffix: str = "") -> None:
        if not force and count != self.total and count - self.last_reported < self.every:
            return

        self.last_reported = count
        elapsed = max(self.elapsed_seconds(), 0.001)
        rate = count / elapsed
        if self.total:
            percent = (count / self.total) * 100
            base = (
                f"{self.label}: {count:,}/{self.total:,} ({percent:.1f}%) "
                f"at {rate:.1f}/s elapsed={format_duration(elapsed)}"
            )
        else:
            base = f"{self.label}: {count:,} at {rate:.1f}/s elapsed={format_duration(elapsed)}"
        if suffix:
            base = f"{base} | {suffix}"
        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[{timestamp} UTC] {base}", flush=True)
