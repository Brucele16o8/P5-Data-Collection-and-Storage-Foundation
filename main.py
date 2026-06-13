"""Local entry point for the AWS Project 05 Python package."""

from __future__ import annotations


PIPELINE_STEPS = [
    ("01", "restore the S3 MongoDB dump into MongoDB on EC2", "mongorestore --drop <dump-path>"),
    ("02", "create an AWS MongoDB sample collection", "scripts/create_mongodb_sample.py"),
    ("03", "extract ranked product crawl targets from MongoDB", "scripts/extract_product_targets.py"),
    ("04", "crawl product information", "scripts/crawl_products.py"),
    ("05", "upload outputs to S3", "scripts/crawl_products.py --output-s3-uri s3://..."),
]


def main() -> None:
    print("Project 05 AWS EC2 + MongoDB pipeline")
    for step_id, purpose, path in PIPELINE_STEPS:
        print(f"{step_id}. {purpose}: {path}")


if __name__ == "__main__":
    main()
