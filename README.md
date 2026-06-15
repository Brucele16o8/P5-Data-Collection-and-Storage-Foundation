# Project 05: Glamira Data Collection & Storage Foundation on AWS

This project builds a Python data pipeline for collecting product information from Glamira event data. It uses MongoDB as a temporary processing database, extracts product IDs and product URLs from event records, crawls one active product page per product, and stores the final product information back in Amazon S3.

The system is designed to be developed locally first, then executed in AWS for the full dataset.

## Stakeholder Summary

The pipeline answers this business requirement:

```text
Use Glamira customer event data to discover products,
retrieve current product information from Glamira pages,
and store the resulting product dataset in AWS.
```

The main output is a product-information dataset that can later support analysis, transformation, reporting, or warehouse loading.

The project uses:

- **MongoDB** to query the restored Countly/Glamira event dump.
- **Python** to extract product targets and crawl product pages.
- **Amazon S3** to store the raw dump and final processed results.
- **Amazon EC2** as the cloud virtual machine for the final Project 05 execution.
- **GitHub** to move the tested code from the developer machine to EC2.

## Final Architecture

```text
LOCAL DEVELOPMENT ENVIRONMENT
Your Mac
├── PyCharm + AI support
├── Python source code
├── Docker MongoDB
│   └── small/sample Glamira data
└── local test output
        |
        | git push
        v
GitHub repository
        |
        | git clone / git pull
        v
AWS CLOUD ENVIRONMENT
EC2 instance
├── Python source code from GitHub
├── Docker MongoDB
│   └── full Glamira data restored from S3
├── extraction and crawler execution
└── output uploaded to S3
```

MongoDB and Python run on the same machine in both environments. This keeps the connection simple and consistent:

```text
mongodb://localhost:27017
```

## Why This Design

Local development is used because it is faster and easier to debug in PyCharm with a small sample.

EC2 is used for the final run because Project 05 requires cloud execution and the full MongoDB dump is stored in S3. Running Docker MongoDB and Python together on one temporary EC2 instance avoids extra network configuration between the database and the Python program.

This EC2 instance is not intended to be a permanent production database server. It is a temporary processing machine:

```text
start EC2
restore MongoDB dump
run pipeline
upload results to S3
stop or terminate EC2
```

Future production versions could move the crawler to AWS Batch or Fargate, but EC2 is the clearest fit for the current VM + MongoDB project requirement.

## Pipeline Stages

```text
1. Raw MongoDB dump is stored in S3
2. EC2 downloads the dump
3. mongorestore loads BSON into Docker MongoDB
4. Python queries MongoDB event records
5. Python extracts product IDs and candidate URLs
6. Python keeps one best target URL per product ID
7. Python crawls Glamira product pages
8. Python parses var react_data from product HTML
9. Python enriches distinct IP addresses with IP2Location
10. Python writes product information and IP output
11. Output is uploaded to S3
```

## Product Extraction Logic

The source collection is expected to be:

```text
countly.summary
```

The supplied schema is stored at:

```text
docs/schema/schema-countly-summary-standardJSON.json
```

Important fields:

| Field | Purpose |
| --- | --- |
| `collection` | Event type, such as `view_product_detail` |
| `product_id` | Main product ID for most product events |
| `viewing_product_id` | Fallback product ID, and main ID for recommendation clicks |
| `current_url` | Product URL for most product events |
| `referrer_url` | Product URL for recommendation-click events |
| `time_stamp` | Event timestamp used for ranking |
| `ip` | Visitor IP for later enrichment |

The pipeline extracts product targets from these events:

- `view_product_detail`
- `select_product_option`
- `select_product_option_quality`
- `add_to_cart_action`
- `product_detail_recommendation_visible`
- `product_detail_recommendation_noticed`
- `product_view_all_recommend_clicked`

By default, `scripts/extract_product_targets.py` writes one best crawl target per distinct `product_id`. This matches the requirement to crawl one active product information record per distinct product. If a full audit of every ranked `product_id + URL` candidate is needed, run extraction with `--include-all-candidates` and write that audit to a separate file.

For each distinct product ID, the crawler keeps one active product result.

## Product Crawling Logic

The crawler first tries the original URL found in MongoDB. If that page is blocked, old, or unavailable, it builds product-ID fallback URLs:

```text
https://<store-domain>/catalog/product/view/id/{product_id}
```

The fallback order prefers English-language stores first, then other language stores. For example:

```text
www.glamira.com.au
www.glamira.com
www.glamira.co.uk
www.glamira.ca
www.glamira.ie
www.glamira.de
www.glamira.fr
www.glamira.it
www.glamira.pl
www.glamira.vn
```

When a valid page is found, the crawler parses:

```javascript
var react_data = {...}
```

from the product page HTML.

The output stores basic fields such as:

- `product_id`
- `name`
- `sku`
- `price`
- `min_price`
- `max_price`
- `category_name`
- `store_code`
- `source_url`
- `resolved_url`
- `country_store`

It also stores the parsed `react_data_basic` and full `react_data` object where available.

## Output

The final outputs are written as JSONL:

```text
outputs/local-full-run/product_information.jsonl
outputs/local-full-run/ip_locations.jsonl
```

In AWS, the output is uploaded to S3:

```text
s3://glamira-data-lake-20260611/product-information/run_date=YYYY-MM-DD/
```

Failed pages are still recorded with statuses such as:

- `http_403`
- `http_404`
- `crawl_failed`
- `not_found_in_configured_stores`

This prevents the pipeline from stopping when individual product pages are unavailable.

## IP Location Processing

IP geolocation is part of the Project 05 requirements. The AWS version uses the same idea as the original requirement: read unique IPs from MongoDB, use `ip2location-python`, and store the result in both a file and a MongoDB collection.

The IP2Location BIN file should live in S3, for example:

```text
s3://glamira-data-lake-20260611/raw/reference/ip2location/IP-COUNTRY-REGION-CITY.BIN
```

On EC2, the IP processing script downloads the BIN file from S3, reads distinct IP addresses from MongoDB, enriches them, and writes:

```text
outputs/local-full-run/ip_locations.jsonl
countly_enriched.ip_locations
```

The EC2 IAM role supplies S3 credentials automatically, so no permanent AWS keys are stored in the project.

## Development And Execution Flow

```text
1. Develop and debug locally on Mac
2. Test using Docker MongoDB and a small/sample collection
3. Run unit tests
4. Push code to GitHub
5. Clone or pull the same code on EC2
6. Restore the full S3 dump into Docker MongoDB on EC2
7. Create a 100,000-record cloud sample and validate the pipeline
8. Run the pipeline against the full MongoDB collection
9. Upload final results to S3
```

The same Python code runs locally and on EC2. Only configuration changes:

```text
MONGO_URI
MONGO_DATABASE
MONGO_COLLECTION
OUTPUT_DIRECTORY
GLAMIRA_FALLBACK_DOMAINS
```

## Quick Local Setup

Install dependencies:

```bash
uv sync
```

Start local Docker MongoDB:

```bash
docker compose up -d mongodb
```

Run tests:

```bash
uv run --extra dev pytest
```

## Choose A Run Size

Stakeholders can run the pipeline in two ways:

| Run mode | Use when | MongoDB source | Output folder |
| --- | --- | --- | --- |
| Sample run | You want a fast validation before waiting hours | `countly_samples.summary_random_<N>` | `outputs/local-sample-<N>` |
| Full run | You want the final local result from all records | `countly.summary` | `outputs/local-full-run` |

The sample size is configurable. For example, use:

```text
1,000 records    quick check
10,000 records   stronger validation
100,000 records  realistic pre-cloud test
full collection  final local run
```

All long-running scripts print progress and write summary JSON files at the end.

If you want to run the full dataset immediately, skip the sample section and go directly to **Full Local Run**.

## Sample Local Run

Use this when stakeholders cannot wait for the full dataset. The sample remains a MongoDB collection with native BSON document types; it is not converted to JSONL.

Choose the sample size:

```bash
export SAMPLE_SIZE=100000
export SAMPLE_COLLECTION="summary_random_${SAMPLE_SIZE}"
export RUN_OUTPUT="outputs/local-sample-${SAMPLE_SIZE}"
```

Create a random MongoDB sample from the full local `countly.summary` collection:

```bash
uv run python scripts/create_mongodb_sample.py \
  --source-db countly \
  --source-collection summary \
  --target-db countly_samples \
  --target-collection "$SAMPLE_COLLECTION" \
  --size "$SAMPLE_SIZE" \
  --mode random
```

Extract product targets from the sample:

```bash
MONGO_URI="mongodb://localhost:27017" \
MONGO_DATABASE=countly_samples \
MONGO_COLLECTION="$SAMPLE_COLLECTION" \
OUTPUT_DIRECTORY="$RUN_OUTPUT" \
uv run python scripts/extract_product_targets.py \
  --progress-every 1000
```

This writes one best target row per distinct `product_id` to `product_targets.csv`.

Run the product crawler against the sample:

```bash
OUTPUT_DIRECTORY="$RUN_OUTPUT" \
uv run python scripts/crawl_products.py \
  --input "$RUN_OUTPUT/product_targets.csv" \
  --output "$RUN_OUTPUT/product_information.jsonl" \
  --resume \
  --timeout-seconds 20 \
  --workers 6 \
  --progress-every 25
```

For a very quick crawler check, add `--limit 100`:

```bash
OUTPUT_DIRECTORY="$RUN_OUTPUT" \
uv run python scripts/crawl_products.py \
  --input "$RUN_OUTPUT/product_targets.csv" \
  --output "$RUN_OUTPUT/product_information_test_100.jsonl" \
  --limit 100 \
  --resume \
  --timeout-seconds 20 \
  --workers 6 \
  --progress-every 10
```

Run IP geolocation against the same sample:

```bash
MONGO_URI="mongodb://localhost:27017" \
MONGO_DATABASE=countly_samples \
MONGO_COLLECTION="$SAMPLE_COLLECTION" \
OUTPUT_DIRECTORY="$RUN_OUTPUT" \
IP2LOCATION_DB_URI="/path/to/IP-COUNTRY-REGION-CITY.BIN" \
uv run python scripts/process_ip_locations.py \
  --target-db countly_enriched \
  --target-collection "ip_locations_sample_${SAMPLE_SIZE}" \
  --mongodb-batch-size 1000 \
  --progress-every 1000
```

Example local BIN path:

```text
/path/to/IP-COUNTRY-REGION-CITY.BIN
```

Sample outputs:

```text
outputs/local-sample-<N>/product_targets.csv
outputs/local-sample-<N>/product_targets_summary.json
outputs/local-sample-<N>/product_information.jsonl
outputs/local-sample-<N>/product_information_summary.json
outputs/local-sample-<N>/product_failed_targets.csv
outputs/local-sample-<N>/ip_locations.jsonl
outputs/local-sample-<N>/ip_locations_summary.json
MongoDB: countly_enriched.ip_locations_sample_<N>
```

## Full Local Run

Use this when you want to run the complete Project 05 pipeline locally against all restored records. This may take a long time, so it is suitable to start at the beginning of the day and let it run.

Expected local source:

```text
MongoDB URI: mongodb://localhost:27017
Database: countly
Collection: summary
Container: mongo-explore
```

Check that the full collection is available:

```bash
docker exec mongo-explore mongosh countly --quiet --eval 'db.summary.countDocuments({})'
```

Create a dedicated output folder:

```bash
export RUN_OUTPUT="outputs/local-full-run"
mkdir -p "$RUN_OUTPUT"
```

Extract all product crawl targets from the full collection:

```bash
MONGO_URI="mongodb://localhost:27017" \
MONGO_DATABASE="countly" \
MONGO_COLLECTION="summary" \
OUTPUT_DIRECTORY="$RUN_OUTPUT" \
uv run python scripts/extract_product_targets.py \
  --progress-every 10000
```

This writes the crawl input file with one best target row per distinct `product_id`. Do not use `--include-all-candidates` for the normal pipeline; that option writes every ranked URL candidate and can produce a multi-GB audit file.

Run the full product crawler:

```bash
OUTPUT_DIRECTORY="$RUN_OUTPUT" \
CRAWL_WORKERS=6 \
uv run python scripts/crawl_products.py \
  --input "$RUN_OUTPUT/product_targets.csv" \
  --output "$RUN_OUTPUT/product_information.jsonl" \
  --resume \
  --timeout-seconds 20 \
  --workers 6 \
  --progress-every 25
```

Notes for the full crawl:

- The crawler may take a long time because it tries fallback country stores when old product URLs fail.
- Start with `--limit 100` if you want a quick confidence check before running all products.
- `product_information.jsonl` can become large because it stores the full parsed `react_data` object.
- Failed products are written with status values such as `not_found_in_configured_stores` instead of stopping the run.
- Use `--resume` for long crawls. The crawler writes each completed product row immediately and skips existing `requested_product_id` values when restarted with the same output file.
- The crawler also writes `product_failed_targets.csv`, which can be used later to retry only failed products.

Retry only failed products later:

```bash
OUTPUT_DIRECTORY="$RUN_OUTPUT" \
uv run python scripts/crawl_products.py \
  --input "$RUN_OUTPUT/product_failed_targets.csv" \
  --output "$RUN_OUTPUT/product_information_retry.jsonl" \
  --failed-output "$RUN_OUTPUT/product_failed_targets_retry.csv" \
  --resume \
  --timeout-seconds 20 \
  --workers 6 \
  --progress-every 25
```

Keep retry output separate from the original crawl output. This makes it clear which rows came from the first crawl and which rows came from the later retry.

Run IP geolocation against the full collection using the local BIN file:

```bash
MONGO_URI="mongodb://localhost:27017" \
MONGO_DATABASE="countly" \
MONGO_COLLECTION="summary" \
OUTPUT_DIRECTORY="$RUN_OUTPUT" \
IP2LOCATION_DB_URI="/path/to/IP-COUNTRY-REGION-CITY.BIN" \
uv run python scripts/process_ip_locations.py \
  --target-db countly_enriched \
  --target-collection ip_locations \
  --mongodb-batch-size 1000 \
  --progress-every 1000
```

Example local BIN path:

```text
/path/to/IP-COUNTRY-REGION-CITY.BIN
```

So the full local IP command is:

```bash
MONGO_URI="mongodb://localhost:27017" \
MONGO_DATABASE="countly" \
MONGO_COLLECTION="summary" \
OUTPUT_DIRECTORY="$RUN_OUTPUT" \
IP2LOCATION_DB_URI="/path/to/IP-COUNTRY-REGION-CITY.BIN" \
uv run python scripts/process_ip_locations.py \
  --target-db countly_enriched \
  --target-collection ip_locations \
  --mongodb-batch-size 1000 \
  --progress-every 1000
```

Each long-running script prints progress to the terminal. Example:

```text
[16:05:10 UTC] Crawling products: 250/8,663 (2.9%) at 1.8/s elapsed=4m 12s | ok=245, active=245
```

Product target extraction has multiple visible phases:

```text
Scanning and aggregating MongoDB product events
Organizing URLs by product
Ranking product URL candidates
Sorting ranked product URL rows
Writing product target CSV
```

For quieter output, increase `--progress-every`. For more frequent output, decrease it.

This produces:

```text
outputs/local-full-run/product_targets.csv
outputs/local-full-run/product_targets_summary.json
outputs/local-full-run/product_information.jsonl
outputs/local-full-run/product_information_summary.json
outputs/local-full-run/product_failed_targets.csv
outputs/local-full-run/ip_locations.jsonl
outputs/local-full-run/ip_locations_summary.json
MongoDB: countly_enriched.ip_locations
```

The summary JSON files record elapsed time, processed counts, success counts, success rates, output paths, failed target counts, and whether the run was cancelled. For crawler summaries, `success_rate_processed_percent` means successful crawls divided by processed products, while `success_rate_target_percent` means successful crawls divided by all target products originally scheduled for the run.

The IP geolocation script tracks each long-running phase:

```text
Preparing IP2Location database
Scanning MongoDB IP values
Processing IP locations
Writing IP locations to MongoDB
Uploading IP output to S3, when configured
```

By default, IP discovery uses a scan mode so progress remains visible while unique IPs are found. Use `--ip-discovery-mode aggregate` only if you prefer MongoDB-side grouping and accept that it may be quiet while MongoDB builds the distinct IP set. The script streams `ip_locations.jsonl` as it processes each distinct IP and writes MongoDB rows in upsert batches. Tune `--mongodb-batch-size` only if the EC2 instance or MongoDB container needs smaller or larger write batches.

If the crawler or IP geolocation script is cancelled with `Ctrl+C`, it writes the rows already processed plus a summary file like:

```json
{
  "cancelled": true,
  "processed_count": 250,
  "remaining_count": 8413,
  "elapsed": "4m 12s"
}
```

Product target extraction writes its final target file only after aggregation completes. If it is cancelled during MongoDB scanning, it writes a cancellation summary but does not write a partial target file because ranking requires the full candidate set.

To continue a stopped product crawl, rerun the same crawler command with `--resume` and the same `--output` path. To start a clean crawl from the beginning, omit `--resume` or use a new output file.

After the full local run finishes, review row counts:

```bash
wc -l outputs/local-full-run/product_targets.csv
wc -l outputs/local-full-run/product_information.jsonl
wc -l outputs/local-full-run/ip_locations.jsonl

docker exec mongo-explore mongosh countly_enriched --quiet --eval 'db.ip_locations.countDocuments({})'
```

## Cloud Setup

The cloud setup uses:

- one EC2 instance
- Docker MongoDB
- Python and `uv`
- AWS CLI
- MongoDB Database Tools
- an EC2 IAM role with S3 read/write permissions

Detailed EC2 setup and full run commands are documented separately:

```text
phase_zips/ec2-cloud-runbook.md
```

`phase_zips/` is a local ignored phase-guide folder for this workspace. It is not intended as a GitHub deliverable.

## Repository Structure

```text
scripts/
  create_mongodb_sample.py
  extract_product_targets.py
  crawl_products.py
  process_ip_locations.py

src/glamira_aws/
  events.py
  mongodb.py
  product_crawler.py
  ip_locations.py
  s3_io.py
  ...

docs/
  architecture.md
  schema/schema-countly-summary-standardJSON.json

phase_zips/           local ignored guide and handoff folder
  00_big_picture.md
  01_decision_diary.md
  02_phase_guide_from_scratch.md
  03_command_cheatsheet.md
  ec2-cloud-runbook.md
  session-handoff.md

tests/
  test_product_url_extraction.py
  test_product_crawler.py
  test_ip_validation.py

docker-compose.yml
pyproject.toml
.env.example
```

## What Not To Commit

The repository should contain code and configuration templates only.

Do not commit:

- `.env`
- `.venv/`
- MongoDB dump files
- MongoDB database files
- large sample files
- crawler outputs
- AWS credentials

These are already covered by `.gitignore`.
