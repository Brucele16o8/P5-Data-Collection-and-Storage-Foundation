# Project 5 Local/EC2 Architecture

Project 5 is focused on source understanding and data collection. The intended deployment is the same workflow on a laptop or on one EC2 instance:

- MongoDB runs in Docker.
- Python runs from this repository.
- The MongoDB dump, IP2Location BIN file, and outputs live on local disk or EBS.
- S3 can be used to store or transfer large files, but the application reads local paths after files are copied down.

## Flow

```text
Local or EC2 filesystem / EBS
    -> Docker MongoDB restore
    -> MongoDB collection
    -> product event extraction
    -> ranked product_targets.csv
    -> product crawler
    -> product_information.jsonl
    -> product_information_warehouse.csv
    -> product_react_data.jsonl

MongoDB collection
    -> distinct IP discovery
    -> local IP2Location lookup
    -> ip_locations.jsonl
```

## Boundaries

`src/glamira_aws/events.py`
: Pure rules for identifying product events and candidate URLs.

`src/glamira_aws/product_targets.py`
: Application service for writing ranked product target outputs.

`src/glamira_aws/product_crawler.py`
: Product page URL strategy, HTTP crawling, and `react_data` parsing.

`src/glamira_aws/product_export.py`
: Splits full crawler output into warehouse-friendly product fields and raw `react_data` archive.

`src/glamira_aws/ip_locations.py`
: IP normalization, validation, and IP2Location lookups.

`src/glamira_aws/crawl_state.py`
: Resume/checkpoint and failed-target reporting.

`src/glamira_aws/observability.py`
: Observer interfaces for progress, logging, and later audit writers.

## Why This Shape

This project keeps source acquisition separate from later warehouse work:

- Python owns local extraction, crawling, validation, and raw output files.
- S3/EBS are storage locations, not application architecture.
- The same scripts can run on a laptop or EC2 without changing code.
- Transformations into star-schema facts and dimensions belong to a later project.

This keeps Project 5 testable, explainable, and close to how it will actually run on EC2.
