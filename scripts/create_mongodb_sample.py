"""Create a MongoDB sample collection for local validation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from glamira_aws.mongodb import MongoSettings, create_sample_collection


def parse_args() -> argparse.Namespace:
    defaults = MongoSettings.from_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default=defaults.uri)
    parser.add_argument("--source-db", default=defaults.database)
    parser.add_argument("--source-collection", default=defaults.collection)
    parser.add_argument("--target-db", default="countly_samples")
    parser.add_argument("--target-collection", default="summary_random_100000")
    parser.add_argument("--size", type=int, default=100000)
    parser.add_argument("--mode", choices=("random", "first"), default="random")
    parser.add_argument("--seed", type=int, default=None, help="Reserved for compatibility; MongoDB $sample is not seedable.")
    parser.add_argument("--keep-target", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    inserted = create_sample_collection(
        uri=args.uri,
        source_db=args.source_db,
        source_collection=args.source_collection,
        target_db=args.target_db,
        target_collection=args.target_collection,
        size=args.size,
        mode=args.mode,
        seed=args.seed,
        drop_target=not args.keep_target,
    )
    print(f"Inserted {inserted} documents into {args.target_db}.{args.target_collection}")


if __name__ == "__main__":
    main()
