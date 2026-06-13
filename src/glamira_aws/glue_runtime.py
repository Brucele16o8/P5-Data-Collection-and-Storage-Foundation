"""Runtime helpers shared by local scripts and AWS Glue jobs."""

from __future__ import annotations

import argparse
import os
from typing import Iterable


def parse_job_args(required: Iterable[str] = (), optional: Iterable[str] = ()) -> dict[str, str]:
    """Parse Glue-style --key value arguments locally and in AWS Glue.

    AWS Glue exposes getResolvedOptions, while local development is easier with
    argparse. This function supports both without importing Glue modules unless
    they exist in the runtime.
    """
    keys = list(dict.fromkeys([*required, *optional]))

    try:
        from awsglue.utils import getResolvedOptions  # type: ignore
        import sys

        return getResolvedOptions(sys.argv, keys)
    except Exception:
        parser = argparse.ArgumentParser()
        for key in required:
            parser.add_argument(f"--{key}", required=True)
        for key in optional:
            parser.add_argument(f"--{key}", required=False, default=os.getenv(key.upper()))
        namespace = parser.parse_args()
        return {key: value for key, value in vars(namespace).items() if value is not None}


def add_src_to_path() -> None:
    """Allow Glue job files to import src modules during local execution."""
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if src.exists() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
